"""Авторизация: Telegram Web App, сессии учеников и вход в панель (суперадмин, учителя)."""
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status

from . import config, db

TEACHER_COOKIE = "teacher_session"
ALGORITHM = "HS256"


# ── Telegram Web App ──────────────────────────────────────────────────────────

class InitDataError(Exception):
    """initData не прошла проверку подписи или просрочена."""


def validate_init_data(init_data: str, max_age_seconds: int = 86400) -> Dict[str, Any]:
    """Проверяет подпись initData по спецификации Telegram Web Apps.

    secret_key = HMAC_SHA256(ключ="WebAppData", сообщение=BOT_TOKEN)
    hash       = HMAC_SHA256(ключ=secret_key, сообщение=data_check_string)

    Возвращает разобранные поля, включая распакованный user.
    """
    # Тексты ошибок видит ребёнок, поэтому без технических подробностей:
    # что именно не так с подписью, нужно только в логах.
    if not config.BOT_TOKEN:
        raise InitDataError("Вход через Telegram сейчас недоступен")
    if not init_data:
        raise InitDataError("Не удалось войти через Telegram, открой кабинет заново")

    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)

    received_hash = data.pop("hash", "")
    if not received_hash:
        raise InitDataError("Не удалось войти через Telegram, открой кабинет заново")

    data_check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise InitDataError("Не удалось войти через Telegram, открой кабинет заново")

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        raise InitDataError("Некорректная дата авторизации")

    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        raise InitDataError("Сессия Telegram устарела, откройте кабинет заново")

    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except json.JSONDecodeError:
            raise InitDataError("Не удалось прочитать данные пользователя")

    return data


# ── Сессии ────────────────────────────────────────────────────────────────────

def issue_student_token(student: Dict[str, Any], preview: bool = False) -> str:
    """Сессия ученика.

    В токен зашит ключ сессии ученика ("sk"). Сменили ключ — все выданные раньше
    токены перестали подходить: так работают сброс пароля, выход и новый код.
    Заодно токен удалённого ученика не откроет кабинет того, кто получит его номер.

    preview=True — токен выдан учителю, который смотрит кабинет глазами ребёнка.
    Такой токен помечаем claim'ом "prv": иначе он неотличим от настоящего, и
    учитель, нажав в кабинете «Выйти», отвязал бы Telegram реального ученика.
    """
    now = int(time.time())
    payload = {
        "sub": str(student["id"]),
        "sk": student.get("session_key") or "",
        "role": "student",
        "iat": now,
        "exp": now + config.STUDENT_SESSION_DAYS * 86400,
    }
    if preview:
        payload["prv"] = True
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def issue_teacher_token(user: Dict[str, Any]) -> str:
    """Сессия панели. Как у учеников, в токене ключ сессии пользователя:
    смена пароля или удаление учителя сразу завершает все его входы."""
    now = int(time.time())
    payload = {
        "sub": str(user["id"]),
        "sk": user.get("session_key") or "",
        "role": "panel",
        "iat": now,
        "exp": now + config.TEACHER_SESSION_HOURS * 3600,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, config.JWT_SECRET, algorithms=[ALGORITHM])


# ── Зависимости FastAPI ───────────────────────────────────────────────────────

def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def require_student(request: Request) -> Dict[str, Any]:
    """Ученик из JWT. Отдаёт запись ученика целиком."""
    token = _bearer(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен вход через Telegram")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла, откройте кабинет заново")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительная сессия")

    if payload.get("role") != "student":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")

    # Запоминаем, что это предпросмотр из панели учителя. Нужно тем действиям,
    # которые ребёнок вправе делать только со своим аккаунтом (см. /api/me/unbind).
    request.state.student_preview = bool(payload.get("prv"))

    student = db.find_student(db.load_db(), int(payload["sub"]))
    if not student:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия завершена, войди заново")

    # Ключ сессии сменился (сброс пароля, выход, новый код карточки) — токен отозван
    expected = student.get("session_key") or ""
    if not expected or not hmac.compare_digest(str(payload.get("sk", "")), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия завершена, войди заново")
    return student


def panel_user(request: Request) -> Optional[Dict[str, Any]]:
    """Пользователь панели из cookie или None.

    Роль берём из базы, а не из токена: снятая с учителя школа или удалённый
    учитель должны действовать сразу, а не после истечения сессии.
    Старые токены (до появления учётных записей) ключа сессии не содержат
    и не подходят — нужен один повторный вход.
    """
    token = request.cookies.get(TEACHER_COOKIE) or _bearer(request)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError:
        return None
    if payload.get("role") != "panel":
        return None
    try:
        user_id = int(payload.get("sub", ""))
    except ValueError:
        return None
    user = db.find_user(db.load_db(), user_id)
    if not user:
        return None
    expected = user.get("session_key") or ""
    if not expected or not hmac.compare_digest(str(payload.get("sk", "")), expected):
        return None
    return user


def require_panel(request: Request) -> Dict[str, Any]:
    """Любой вошедший в панель — для чтения: снимок журнала, статистика, расписание.
    401, если не вошёл. Отдаёт пользователя."""
    user = panel_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен вход в панель учителя")
    return user


def require_teacher(request: Request) -> Dict[str, Any]:
    """Всё, что меняет данные: учитель или суперадмин. Администрация школы только
    смотрит — ей 403. Защита «закрыто по умолчанию»: новый маршрут на этой зависимости
    недоступен администрации школы, пока его явно не откроют через require_panel."""
    user = require_panel(request)
    if not db.can_edit(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Администрация школы может только смотреть")
    return user


def require_checkin_admin(request: Request) -> Dict[str, Any]:
    """Раздел «Отметки преподавателей»: администратор и ответственный за отметки.
    Учителю без поручения и администрации школы — 403, в том числе на чтение:
    там часы, ставки и суммы."""
    user = require_panel(request)
    if not db.can_manage_checkins(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Раздел отметок доступен администратору "
                                                       "и ответственному за отметки")
    return user


def require_superadmin(request: Request) -> Dict[str, Any]:
    """Только суперадмин: школы, учителя, витрина призов."""
    user = require_panel(request)
    if not db.is_superadmin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это может сделать только администратор")
    return user


def require_prize_editor(request: Request) -> Dict[str, Any]:
    """Витрина призов: администратор системы — вся, администрация школы — призы своей школы.
    Учитель призы только выдаёт."""
    user = require_panel(request)
    if not (db.is_superadmin(user) or db.is_school_admin(user)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Призы добавляет администрация")
    return user


def require_teacher_page(request: Request) -> bool:
    """Для HTML-страниц учителя: перенаправляем на форму входа.

    Адреса собираем с подпутём: в браузере страница живёт по /stars/..., даже
    если до приложения запрос доходит уже без префикса.
    """
    if not panel_user(request):
        target = urllib.parse.quote(f"{config.URL_PREFIX}{request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            detail="Нужен вход",
            headers={"Location": f"{config.URL_PREFIX}/login?next={target}"},
        )
    return True


def require_editor_page(request: Request) -> bool:
    """Страницы с данными для входа учеников (карточки с паролями): без входа — на форму,
    администрации школы — 403."""
    require_teacher_page(request)
    if not db.can_edit(panel_user(request)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Администрация школы может только смотреть")
    return True


def require_superadmin_page(request: Request) -> bool:
    """Служебные страницы администратора: без входа — на форму, учителю — 404."""
    require_teacher_page(request)
    if not db.is_superadmin(panel_user(request)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return True


PanelDep = Depends(require_panel)
TeacherDep = Depends(require_teacher)
SuperadminDep = Depends(require_superadmin)
TeacherPageDep = Depends(require_teacher_page)
StudentDep = Depends(require_student)
