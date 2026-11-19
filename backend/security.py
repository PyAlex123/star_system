"""Авторизация: Telegram Web App, сессии учеников и вход учителя."""
import hashlib
import hmac
import json
import secrets
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
    if not config.BOT_TOKEN:
        raise InitDataError("Бот не настроен: в .env не указан BOT_TOKEN")
    if not init_data:
        raise InitDataError("Пустая initData")

    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)

    received_hash = data.pop("hash", "")
    if not received_hash:
        raise InitDataError("В initData нет подписи")

    data_check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise InitDataError("Подпись initData неверна")

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

def issue_student_token(student_id: int, preview: bool = False) -> str:
    """Сессия ученика.

    preview=True — токен выдан учителю, который смотрит кабинет глазами ребёнка.
    Такой токен помечаем claim'ом "prv": иначе он неотличим от настоящего, и
    учитель, нажав в кабинете «Выйти», отвязал бы Telegram реального ученика.
    """
    now = int(time.time())
    payload = {
        "sub": str(student_id),
        "role": "student",
        "iat": now,
        "exp": now + config.STUDENT_SESSION_DAYS * 86400,
    }
    if preview:
        payload["prv"] = True
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def issue_teacher_token() -> str:
    now = int(time.time())
    payload = {
        "sub": "teacher",
        "role": "teacher",
        "iat": now,
        "exp": now + config.TEACHER_SESSION_HOURS * 3600,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, config.JWT_SECRET, algorithms=[ALGORITHM])


def check_teacher_password(password: str) -> bool:
    return secrets.compare_digest((password or "").strip(), config.TEACHER_PASSWORD)


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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден — обратись к учителю")
    return student


def _teacher_ok(request: Request) -> bool:
    token = request.cookies.get(TEACHER_COOKIE) or _bearer(request)
    if not token:
        return False
    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError:
        return False
    return payload.get("role") == "teacher"


def require_teacher(request: Request) -> bool:
    """Для API учителя: 401, если не вошёл."""
    if not _teacher_ok(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен вход в панель учителя")
    return True


def require_teacher_page(request: Request) -> bool:
    """Для HTML-страниц учителя: перенаправляем на форму входа.

    Адреса собираем с подпутём: в браузере страница живёт по /stars/..., даже
    если до приложения запрос доходит уже без префикса.
    """
    if not _teacher_ok(request):
        target = urllib.parse.quote(f"{config.URL_PREFIX}{request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            detail="Нужен вход",
            headers={"Location": f"{config.URL_PREFIX}/login?next={target}"},
        )
    return True


TeacherDep = Depends(require_teacher)
TeacherPageDep = Depends(require_teacher_page)
StudentDep = Depends(require_student)
