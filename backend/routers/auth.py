"""Вход: ученик через Telegram Web App или по логину, панель — суперадмин и учителя."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import config, db, passwords, ratelimit, schemas, security, themes
from .common import student_or_404

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/twa")
def login_via_telegram(payload: schemas.TwaAuth):
    """Кабинет ученика присылает initData из Telegram — проверяем подпись и выдаём сессию."""
    try:
        data = security.validate_init_data(payload.init_data)
    except security.InitDataError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    user = data.get("user") or {}
    tg_id = user.get("id")
    if not tg_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Telegram не передал данные пользователя")

    database = db.load_db()
    student = db.find_by_tg_id(database, tg_id)
    if not student:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Этот аккаунт Telegram ещё не привязан. Отсканируй свой QR-код с карточки класса.",
        )

    # Подтянем ссылку на фото профиля — пригодится, если ребёнок выберет аватар из Telegram
    photo_url = user.get("photo_url")
    username = user.get("username")

    def _touch(database_inner):
        fresh = db.find_student(database_inner, student["id"])
        if not fresh:
            return db.NO_SAVE
        changed = False
        if username and fresh.get("tg_username") != username:
            fresh["tg_username"] = username
            changed = True
        # Адрес фото храним у себя: он пришёл в подписанных данных, значит настоящий.
        # Аватар «из Telegram» потом берётся только отсюда, а не из запроса клиента.
        if photo_url and fresh.get("tg_photo_url") != photo_url:
            fresh["tg_photo_url"] = photo_url
            changed = True
        if fresh.get("avatar", {}).get("type") == "telegram" and photo_url:
            if fresh["avatar"].get("value") != photo_url:
                fresh["avatar"]["value"] = photo_url
                changed = True
        return None if changed else db.NO_SAVE

    db.transaction(_touch)

    return {
        "token": security.issue_student_token(student),
        "telegram_photo_url": photo_url,
    }


@router.post("/dev")
def login_dev(student_id: int):
    """Отладочный вход без Telegram. Работает только при DEV_LOGIN=1 в .env."""
    if not config.DEV_LOGIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")

    student = db.find_student(db.load_db(), student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    return {"token": security.issue_student_token(student), "dev": True}


@router.post("/preview")
def login_preview(student_id: int, user: Dict[str, Any] = Depends(security.require_teacher)):
    """Учитель открывает кабинет глазами конкретного ученика — только из своих школ."""
    database = db.load_db()
    student = student_or_404(database, student_id, user)
    return {"token": security.issue_student_token(student, preview=True), "preview": True,
            "theme": themes.theme_of_class(database, student.get("class_id"))}


def _guard(scope: str, key: str) -> None:
    """Тормозит перебор пароля. Сообщаем, сколько ждать, — честнее, чем молчать."""
    wait = ratelimit.retry_after(scope, key)
    if wait:
        minutes = max(1, round(wait / 60))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Слишком много попыток входа. Попробуйте через {minutes} мин.",
            headers={"Retry-After": str(wait)},
        )


@router.post("/student")
def login_student(payload: schemas.StudentLogin, request: Request):
    """Вход через сайт по логину и паролю — для детей без Telegram."""
    ip = ratelimit.client_key(request)
    login = passwords.normalize_login(payload.login)
    _guard("student-ip", ip)
    _guard("student-login", login)

    database = db.load_db()
    student = db.find_by_login(database, login)
    # Хеш считаем всегда, даже для несуществующего логина: иначе ответ «нет такого»
    # приходит заметно быстрее, и по времени можно собрать список логинов класса.
    stored = student.get("password_hash", "") if student else passwords.dummy_hash()
    password_ok = passwords.verify_password(payload.password, stored)

    if not student or not password_ok:
        ratelimit.register_failure("student-ip", ip)
        ratelimit.register_failure("student-login", login)
        # Не уточняем, что именно неверно: иначе логины можно перебрать по ответу
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    # Обнуляем только счётчик этого логина. Счётчик адреса не трогаем: иначе,
    # имея свой аккаунт, можно чередовать «свой вход — попытки по чужим».
    ratelimit.reset("student-login", login)

    # Отметка «входил через сайт»: в журнале учитель видит, что карточка работает
    def _seen(database_inner):
        fresh = db.find_student(database_inner, student["id"])
        if not fresh:
            return db.NO_SAVE
        fresh["last_login"] = db.now_iso()
        return None
    db.transaction(_seen)

    # Тема школы — чтобы кабинет сразу открылся в своём оформлении
    return {"token": security.issue_student_token(student),
            "theme": themes.theme_of_class(database, student.get("class_id"))}


def _set_panel_cookie(response: Response, user: Dict[str, Any]) -> None:
    response.set_cookie(
        security.TEACHER_COOKIE,
        security.issue_teacher_token(user),
        max_age=config.TEACHER_SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        # За nginx приложение всегда видит http, поэтому смотрим на объявленный адрес сайта
        secure=config.IS_HTTPS,
        # На подпути cookie не должна улетать к соседним сайтам того же домена
        path=config.URL_PREFIX or "/",
    )


@router.post("/teacher")
def login_teacher(payload: schemas.TeacherLogin, response: Response, request: Request):
    """Вход в панель по логину и паролю. Сессия кладётся в httpOnly-cookie.

    Защита та же, что у входа учеников: счётчики неудач по логину и по адресу,
    одинаковый ответ и одинаковое время для «нет логина» и «неверный пароль».
    """
    ip = ratelimit.client_key(request)
    login = passwords.normalize_login(payload.login)
    _guard("teacher-ip", ip)
    _guard("teacher-login", login)

    user = db.find_user_by_login(db.load_db(), login)
    stored = user.get("password_hash", "") if user else passwords.dummy_hash()
    password_ok = passwords.verify_password((payload.password or "").strip(), stored)

    if not user or not password_ok:
        ratelimit.register_failure("teacher-ip", ip)
        ratelimit.register_failure("teacher-login", login)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    ratelimit.reset("teacher-login", login)
    _set_panel_cookie(response, user)
    return {"ok": True, "role": user["role"]}


@router.post("/teacher/password")
def change_panel_password(payload: schemas.PasswordChange, response: Response,
                          user: Dict[str, Any] = Depends(security.require_panel)):
    """Свой пароль: нужен текущий. Его больше не видит никто, даже администратор. Остальные входы этого пользователя завершаются,
    а текущему выдаём новую cookie — чтобы не выкинуло из панели."""
    if not passwords.verify_password((payload.current or "").strip(), user.get("password_hash", "")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Текущий пароль указан неверно")
    try:
        new = passwords.validate_new(payload.new)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    def _apply(database: Dict[str, Any]):
        fresh = db.find_user(database, user["id"])
        if not fresh:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен вход в панель учителя")
        fresh["password_hash"] = passwords.hash_password(new)
        fresh["password_shown"] = None
        fresh["session_key"] = db.new_session_key()
        return fresh

    _set_panel_cookie(response, db.transaction(_apply))
    return {"ok": True}


@router.post("/teacher/logout")
def logout_teacher(response: Response):
    response.delete_cookie(security.TEACHER_COOKIE, path=config.URL_PREFIX or "/")
    return {"ok": True}
