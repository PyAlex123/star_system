"""Вход: ученик через Telegram Web App, учитель по паролю."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import config, db, schemas, security

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
        if fresh.get("avatar", {}).get("type") == "telegram" and photo_url:
            if fresh["avatar"].get("value") != photo_url:
                fresh["avatar"]["value"] = photo_url
                changed = True
        return None if changed else db.NO_SAVE

    db.transaction(_touch)

    return {
        "token": security.issue_student_token(student["id"]),
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
    return {"token": security.issue_student_token(student_id), "dev": True}


@router.post("/preview", dependencies=[Depends(security.require_teacher)])
def login_preview(student_id: int):
    """Учитель открывает кабинет глазами конкретного ученика.

    Доступно только из панели учителя — у неё и так полный доступ к данным.
    """
    student = db.find_student(db.load_db(), student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    return {"token": security.issue_student_token(student_id), "preview": True}


@router.post("/teacher")
def login_teacher(payload: schemas.TeacherLogin, response: Response, request: Request):
    """Вход в панель учителя. Сессия кладётся в httpOnly-cookie."""
    if not security.check_teacher_password(payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный пароль")

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
