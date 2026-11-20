"""Вход: ученик через Telegram Web App, учитель по паролю."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import config, db, passwords, ratelimit, schemas, security

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


@router.post("/preview", dependencies=[Depends(security.require_teacher)])
def login_preview(student_id: int):
    """Учитель открывает кабинет глазами конкретного ученика.

    Доступно только из панели учителя — у неё и так полный доступ к данным.
    """
    student = db.find_student(db.load_db(), student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    return {"token": security.issue_student_token(student, preview=True), "preview": True}


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

    student = db.find_by_login(db.load_db(), login)
    # Хеш считаем всегда, даже для несуществующего логина: иначе ответ «нет такого»
    # приходит заметно быстрее, и по времени можно собрать список логинов класса.
    stored = student.get("password_hash", "") if student else passwords.DUMMY_HASH
    password_ok = passwords.verify_password(payload.password, stored)

    if not student or not password_ok:
        ratelimit.register_failure("student-ip", ip)
        ratelimit.register_failure("student-login", login)
        # Не уточняем, что именно неверно: иначе логины можно перебрать по ответу
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    # Обнуляем только счётчик этого логина. Счётчик адреса не трогаем: иначе,
    # имея свой аккаунт, можно чередовать «свой вход — попытки по чужим».
    ratelimit.reset("student-login", login)
    return {"token": security.issue_student_token(student)}


@router.post("/teacher")
def login_teacher(payload: schemas.TeacherLogin, response: Response, request: Request):
    """Вход в панель учителя. Сессия кладётся в httpOnly-cookie."""
    ip = ratelimit.client_key(request)
    _guard("teacher", ip)

    if not security.check_teacher_password(payload.password):
        ratelimit.register_failure("teacher", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный пароль")

    ratelimit.reset("teacher", ip)

    token = security.issue_teacher_token()
    response.set_cookie(
        security.TEACHER_COOKIE,
        token,
        max_age=config.TEACHER_SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        # За nginx приложение всегда видит http, поэтому смотрим на объявленный адрес сайта
        secure=config.IS_HTTPS,
        # На подпути cookie не должна улетать к соседним сайтам того же домена
        path=config.URL_PREFIX or "/",
    )
    return {"ok": True}


@router.post("/teacher/logout")
def logout_teacher(response: Response):
    response.delete_cookie(security.TEACHER_COOKIE, path=config.URL_PREFIX or "/")
    return {"ok": True}
