"""Кабинет ученика. Отдаёт только его собственные данные и топ-5 его класса."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from .. import config, db, images, passwords, schemas, security
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


def public_card(student: Dict[str, Any], place: int, me_id: int) -> Dict[str, Any]:
    """Строка рейтинга. Намеренно без истории, кода карточки и Telegram-данных."""
    return {
        "place": place,
        "id": student["id"],
        "name": student["name"],
        "total_stars": student["total_stars"],
        "avatar_url": avatar_url(student),
        "is_me": student["id"] == me_id,
    }


def class_ranking(database: Dict[str, Any], class_id: int) -> List[Dict[str, Any]]:
    """Звёзды по убыванию, при равенстве — по алфавиту."""
    classmates = db.class_students(database, class_id)
    return sorted(classmates, key=lambda s: (-s["total_stars"], s["name"].lower()))


def build_leaderboard(database: Dict[str, Any], student: Dict[str, Any]) -> Dict[str, Any]:
    ranking = class_ranking(database, student.get("class_id"))
    my_index = next((i for i, s in enumerate(ranking) if s["id"] == student["id"]), None)

    top = [public_card(s, i + 1, student["id"]) for i, s in enumerate(ranking[:TOP_SIZE])]

    me_row: Optional[Dict[str, Any]] = None
    if my_index is not None and my_index >= TOP_SIZE:
        me_row = public_card(student, my_index + 1, student["id"])

    gap_to_next: Optional[int] = None
    next_name: Optional[str] = None
    if my_index is not None and my_index > 0:
        ahead = ranking[my_index - 1]
        gap_to_next = max(1, ahead["total_stars"] - student["total_stars"] + 1)
        next_name = ahead["name"]

    return {
        "top": top,
        "me": me_row,
        "my_place": (my_index + 1) if my_index is not None else None,
        "class_size": len(ranking),
        "gap_to_next": gap_to_next,
        "next_name": next_name,
    }


def build_prizes(database: Dict[str, Any], stars: int) -> List[Dict[str, Any]]:
    result = []
    for prize in database["prizes"]:
        if not prize.get("active", True):
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
        })
    return sorted(result, key=lambda p: (not p["affordable"], p["left"], p["cost"]))


@router.get("")
def get_me(student: Dict[str, Any] = Depends(require_student)):
    """Всё для кабинета одним запросом."""
    database = db.load_db()
    fresh = db.find_student(database, student["id"]) or student
    leaderboard = build_leaderboard(database, fresh)
    prizes = build_prizes(database, fresh["total_stars"])
    next_prize = next((p for p in prizes if not p["affordable"]), None)

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
        },
        "leaderboard": leaderboard,
        "prizes": prizes,
        "next_prize": next_prize,
        "history": fresh.get("history", []),
        "avatar_presets": db.AVATAR_PRESETS,
    }


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
        fresh["tg_id"] = None
        fresh["tg_username"] = None
        fresh["bound_at"] = None
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
