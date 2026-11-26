"""Кабинет ученика. Отдаёт только его собственные данные, топ-5 класса, параллели
и школы, расписание класса и его активность."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from .. import config, db, images, passwords, schemas, security, stats, themes
from ..security import require_student

router = APIRouter(prefix="/api/me", tags=["student"])

TOP_SIZE = 5


def avatar_url(student: Dict[str, Any]) -> str:
    avatar = student.get("avatar") or {}
    kind, value = avatar.get("type"), avatar.get("value") or ""
    if kind == "preset":
        return f"/static/img/avatars/{value}.svg"
    if kind in {"photo", "telegram"} and value:
        return value
    return f"/static/img/avatars/{db.default_avatar(student['id'])['value']}.svg"


def public_card(student: Dict[str, Any], place: int, me_id: int, class_names: Dict[int, str]) -> Dict[str, Any]:
    """Строка рейтинга. Намеренно без истории, кода карточки и Telegram-данных.
    score — заработано за всё время: по нему считается место."""
    return {
        "place": place,
        "id": student["id"],
        "name": student["name"],
        "class_name": class_names.get(student.get("class_id"), ""),
        "score": stats.earned(student),
        "total_stars": stats.earned(student),
        "avatar_url": avatar_url(student),
        "is_me": student["id"] == me_id,
    }


def build_leaderboards(database: Dict[str, Any], student: Dict[str, Any]) -> Dict[str, Any]:
    """Рейтинг класса, параллели («5-А», «5-Б»…) и школы — по заработанным звёздам.
    Уровня нет (None), если в нём нечего сравнивать: кружок без параллели, одна школа-класс."""
    class_names = {c["id"]: c["name"] for c in database["classes"]}

    def card(s: Dict[str, Any], place: int) -> Dict[str, Any]:
        return public_card(s, place, student["id"], class_names)

    boards = {}
    for scope in ("class", "parallel", "school"):
        group = stats.scope_students(database, student, scope)
        boards[scope] = None if group is None else stats.leaderboard(database, student, group, card)
    return boards


def build_prizes(database: Dict[str, Any], stars: int, school_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Витрина ученика: общие призы и призы от администрации его школы."""
    result = []
    for prize in database["prizes"]:
        if not prize.get("active", True) or not db.prize_for_school(prize, {school_id}):
            continue
        cost = max(1, int(prize["cost"]))
        result.append({
            "id": prize["id"],
            "name": prize["name"],
            "desc": prize.get("desc", ""),
            "cost": cost,
            "category": prize.get("category", "small"),
            "image": prize.get("image") or "",
            "affordable": stars >= cost,
            "left": max(0, cost - stars),
            "progress": min(100, round(stars / cost * 100)),
            "from_school": prize.get("school_id") is not None,   # пометка «от администрации школы»
        })
    return sorted(result, key=lambda p: (not p["affordable"], p["left"], p["cost"]))


@router.get("")
def get_me(request: Request, student: Dict[str, Any] = Depends(require_student)):
    """Всё для кабинета одним запросом."""
    _mark_seen(request, student["id"])
    database = db.load_db()
    fresh = db.find_student(database, student["id"]) or student
    leaderboards = build_leaderboards(database, fresh)
    prizes = build_prizes(database, fresh["total_stars"], stats.school_of_class(database, fresh.get("class_id")))
    next_prize = next((p for p in prizes if not p["affordable"]), None)

    # Выданные призы: запись истории знает номер приза — ребёнку показываем его фото и название
    all_prizes = {p["id"]: p for p in database["prizes"]}
    history = []
    for entry in fresh.get("history", []):
        item = dict(entry)
        prize = all_prizes.get(entry.get("prize_id"))
        if prize:
            item["prize"] = {"id": prize["id"], "name": prize["name"], "image": prize.get("image") or ""}
        history.append(item)

    return {
        "student": {
            "id": fresh["id"],
            "name": fresh["name"],
            "login": fresh.get("login") or "",
            "class_name": db.class_name(database, fresh.get("class_id")),
            "school_name": db.school_name_of_class(database, fresh.get("class_id")),
            "total_stars": fresh["total_stars"],
            "avatar": fresh.get("avatar"),
            "avatar_url": avatar_url(fresh),
            "theme": themes.theme_of_class(database, fresh.get("class_id")),
            "rating_hidden": bool(fresh.get("rating_hidden")),
        },
        "leaderboard": leaderboards["class"],
        "leaderboards": leaderboards,
        "schedule": stats.class_schedule(database, fresh.get("class_id")),
        "activity": stats.student_activity(database, fresh),
        "prizes": prizes,
        "next_prize": next_prize,
        "history": history,
        "avatar_presets": db.AVATAR_PRESETS,
    }


def _mark_seen(request: Request, student_id: int) -> None:
    """Дата последнего захода в кабинет — для статистики школы. Пишем не чаще раза
    в день и не в предпросмотре учителя: иначе каждый опрос переписывал бы базу."""
    if getattr(request.state, "student_preview", False):
        return
    today = db.today()

    def _apply(database: Dict[str, Any]):
        fresh = db.find_student(database, student_id)
        if not fresh or fresh.get("last_seen") == today:
            return db.NO_SAVE
        fresh["last_seen"] = today
        return None

    db.transaction(_apply)


def _forbid_preview(request: Request, message: str) -> None:
    """Учитель в режиме предпросмотра смотрит, но не меняет кабинет ребёнка."""
    if getattr(request.state, "student_preview", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, message)


def _set_avatar(student_id: int, avatar: Dict[str, str]) -> Dict[str, Any]:
    """Сохраняет аватар и удаляет прежнее загруженное фото, если оно было."""
    previous: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        fresh = db.find_student(database, student_id)
        if not fresh:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        previous["url"] = images.avatar_upload_url(fresh)
        fresh["avatar"] = avatar
        return fresh

    saved = db.transaction(_apply)
    if previous.get("url") and previous["url"] != avatar.get("value"):
        images.drop_upload(previous["url"])
    return saved


@router.post("/avatar")
def choose_avatar(payload: schemas.AvatarChoice, request: Request,
                  student: Dict[str, Any] = Depends(require_student)):
    """Выбрать готовый аватар или поставить фото профиля Telegram."""
    _forbid_preview(request, "Это предпросмотр кабинета, аватар здесь не меняется.")

    if payload.use_telegram_photo:
        # Адрес берём только из данных, подписанных Telegram при входе.
        # То, что прислал клиент, игнорируем: иначе аватаром стала бы любая
        # картинка из интернета, и её увидел бы весь класс в рейтинге.
        photo = (student.get("tg_photo_url") or "").strip()
        if not photo.startswith("https://"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "В твоём профиле Telegram нет открытого фото")
        avatar = {"type": "telegram", "value": photo}
    else:
        preset = (payload.preset or "").strip()
        if preset not in db.AVATAR_PRESETS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Такого аватара нет в наборе")
        avatar = {"type": "preset", "value": preset}

    saved = _set_avatar(student["id"], avatar)
    return {"ok": True, "avatar": avatar, "avatar_url": avatar_url(saved)}


@router.post("/photo")
async def upload_photo(request: Request, file: UploadFile = File(...),
                       student: Dict[str, Any] = Depends(require_student)):
    """Поставить своё фото: обрезаем в квадрат и ужимаем до 320 px."""
    _forbid_preview(request, "Это предпросмотр кабинета, фото здесь не меняется.")

    data = await images.read_upload(file)
    filename = images.new_avatar_name()
    images.save_square(data, config.AVATARS_UPLOAD_DIR / filename)
    url = f"{images.UPLOADS_URL}avatars/{filename}"

    try:
        _set_avatar(student["id"], {"type": "photo", "value": url})
    except Exception:
        images.drop_upload(url)
        raise
    return {"ok": True, "avatar_url": url}


@router.post("/unbind")
def unbind_self(request: Request, student: Dict[str, Any] = Depends(require_student)):
    """Выход из аккаунта: открепляем Telegram от карточки и завершаем сессии.

    Звёзды, историю и аватар не трогаем — ребёнок вернётся по тому же коду
    или логину, и всё окажется на месте. Нужно, когда карточки перепутали,
    ребёнок хочет зайти с другого аккаунта или вошёл с чужого телефона.
    """
    _forbid_preview(request,
                    "Это предпросмотр кабинета. Отвязать аккаунт ученика можно в панели учителя.")

    def _apply(database: Dict[str, Any]):
        fresh = db.find_student(database, student["id"])
        if not fresh:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        db.clear_telegram(fresh)
        db.rotate_session(fresh)
        return None

    db.transaction(_apply)
    return {"ok": True}


@router.post("/password")
def change_password(payload: schemas.PasswordChange, request: Request,
                    student: Dict[str, Any] = Depends(require_student)):
    """Ребёнок меняет себе пароль для входа через сайт.

    Все прочие сессии завершаются — если кто-то подсмотрел старый пароль и вошёл,
    его выкинет. Текущему устройству возвращаем свежую сессию, чтобы ребёнку
    не пришлось входить заново.
    """
    _forbid_preview(request, "Это предпросмотр кабинета, пароль здесь не меняется.")

    try:
        new = passwords.validate_new(payload.new)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if not passwords.verify_password(payload.current, student.get("password_hash", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Текущий пароль указан неверно")

    def _apply(database: Dict[str, Any]):
        fresh = db.find_student(database, student["id"])
        if not fresh:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        fresh["password_hash"] = passwords.hash_password(new)
        # Пароль придуман ребёнком — учителю его больше не показываем,
        # у него остаётся только возможность выдать новый.
        fresh["password_shown"] = None
        db.rotate_session(fresh)
        return fresh

    saved = db.transaction(_apply)
    return {"ok": True, "token": security.issue_student_token(saved)}
