"""Учётные записи панели: суперадмин заводит сотрудников и раздаёт им должности в школах
(директор, завуч начальной / средней школы, учитель — в каждой школе свои, можно несколько).
Пароль сотрудника виден суперадмину, пока сотрудник не сменит его сам (как у учеников)."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import config, db, images, passwords, schemas
from ..checkin import service as checkin
from ..security import require_superadmin
from .common import bad_request, clean_name, not_found
from .schedule import release_teacher

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_superadmin)])


def _user_or_404(database: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    user = db.find_user(database, user_id)
    if not user:
        raise not_found("Пользователь не найден")
    return user


def _clean_schools(database: Dict[str, Any], school_ids: List[int]) -> List[int]:
    known = {s["id"] for s in database["schools"]}
    unknown = [i for i in school_ids if i not in known]
    if unknown:
        raise bad_request("Такой школы нет")
    return sorted(set(school_ids))


def _clean_posts(database: Dict[str, Any], posts: Dict[int, List[str]]) -> Dict[int, List[str]]:
    _clean_schools(database, list(posts))
    return posts


def _legacy_posts(user: Dict[str, Any], item: schemas.UserUpdate) -> Dict[str, List[str]]:
    """Старый способ (role + school_ids): новые школы — с должностью по роли, оставшиеся —
    со своими должностями; смена роли переводит все должности в неё."""
    role = item.role or user.get("role")
    school_ids = item.school_ids if item.school_ids is not None else list(user.get("school_ids") or [])
    if item.role is not None and item.role != user.get("role"):
        return db.posts_from_role(role, school_ids)
    current = user.get("posts") or {}
    return {str(s): current.get(str(s)) or db.posts_from_role(role, [s])[str(s)] for s in school_ids}


def apply_posts(database: Dict[str, Any], actor: Dict[str, Any], user: Dict[str, Any], posts) -> None:
    """Записать должности; там, где человек перестал быть учителем, — снять его с занятий."""
    taught = db.teach_school_ids(user)
    db.set_posts(user, posts)
    for school_id in taught - db.teach_school_ids(user):
        release_teacher(database, actor, user["id"], school_id)


def _password(raw) -> str:
    """Свой пароль или придуманный системой — тот же формат, что у учеников."""
    if (raw or "").strip():
        try:
            return passwords.validate_new(raw)
        except ValueError as exc:
            raise bad_request(str(exc))
    return passwords.new_password()


@router.post("")
def create_user(item: schemas.UserCreate):
    """Новый учитель. Пароль возвращается один раз — его нужно передать учителю."""
    login = passwords.normalize_login(item.login)
    if not passwords.LOGIN_RE.match(login):
        raise bad_request("Логин: латинские буквы, цифры, точка, дефис; от 3 до 31 символа")
    name = clean_name(item.name, "Введите имя учителя")
    password = _password(item.password)

    def _apply(database: Dict[str, Any]):
        if db.find_user_by_login(database, login):
            raise bad_request("Такой логин уже занят")
        if item.posts is not None:
            return db.make_user(database, login, name, item.role, password,
                                posts=_clean_posts(database, item.posts))
        schools = _clean_schools(database, item.school_ids)
        return db.make_user(database, login, name, item.role, password, schools)

    user = db.transaction(_apply)
    return {"ok": True, "user": db.public_user(user, with_password=True), "password": password}


@router.patch("/{user_id}")
def update_user(user_id: int, item: schemas.UserUpdate, me: Dict[str, Any] = Depends(require_superadmin)):
    """Имя и должности в школах. Снятая школа пропадает у него сразу, без повторного входа.
    Здесь же поля отметок: ставка и «отмечается в боте»."""
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        before.update(user)
        if item.name is not None:
            user["name"] = clean_name(item.name, "Введите имя учителя")
        if item.role is not None and item.role != user.get("role") and db.is_superadmin(user):
            raise bad_request("Роль администратора не меняется")
        if not db.is_superadmin(user) and (item.posts is not None or item.school_ids is not None
                                           or item.role is not None):
            role = user.get("role")
            if item.posts is not None:
                posts = _clean_posts(database, item.posts)
            else:
                if item.school_ids is not None:
                    _clean_schools(database, item.school_ids)
                posts = _legacy_posts(user, item)
            apply_posts(database, me, user, posts)
            if item.role is not None and user.get("role") != role:
                user["session_key"] = db.new_session_key()   # новые права — с нового входа
        if "hourly_rate" in item.model_fields_set:
            user["hourly_rate"] = item.hourly_rate
        if item.checkin_tracked is not None:
            user["checkin_tracked"] = item.checkin_tracked
        return user

    user = db.transaction(_apply)
    checkin.audit_staff(me, user_id, "update", before, user)
    return {"ok": True, "user": db.public_user(user, with_password=True)}


TELEGRAM_FIELDS = ("tg_id", "tg_username", "tg_bound_at", "tg_blocked_at", "consent_at")


@router.post("/{user_id}/code")
def new_bot_code(user_id: int, me: Dict[str, Any] = Depends(require_superadmin)):
    """Новый код для бота — как «новый QR» у ученика: старая карточка сотрудника перестаёт
    работать, Telegram отвязывается, согласие на отметки спросим заново."""
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        before.update(user)
        user["bot_code"] = db.new_staff_code(database)
        for field in TELEGRAM_FIELDS:
            user[field] = None
        return user

    user = db.transaction(_apply)
    checkin.audit_staff(me, user_id, "new_code", before, user)
    return {"ok": True, "code": user["bot_code"], "link": config.staff_link(user["bot_code"]) or None,
            "user": db.public_user(user, with_password=True)}


@router.delete("/{user_id}/telegram")
def unbind_telegram(user_id: int, me: Dict[str, Any] = Depends(require_superadmin)):
    """Отвязать Telegram сотрудника (новый телефон). Код с карточки остаётся прежним — как
    у ученика; согласие на отметки спросим заново. Карточку потеряли — «Новый код»."""
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        before.update(user)
        for field in TELEGRAM_FIELDS:
            user[field] = None
        return user

    user = db.transaction(_apply)
    checkin.audit_staff(me, user_id, "unbind_telegram", before, user)
    return {"ok": True, "user": db.public_user(user, with_password=True)}


@router.post("/{user_id}/password")
def reset_user_password(user_id: int, item: schemas.PasswordReset):
    """Новый пароль учителю. Все его входы завершаются."""
    password = _password(item.password)

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        user["password_hash"] = passwords.hash_password(password)
        user["password_shown"] = password
        user["session_key"] = db.new_session_key()
        return None

    db.transaction(_apply)
    return {"ok": True, "password": password}


@router.delete("/{user_id}/photo")
def drop_user_photo(user_id: int):
    """Убрать фото сотрудника (например, неподходящее) — снова инициалы."""
    removed: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        removed["url"] = images.avatar_upload_url(user)
        user["avatar"] = None
        return user

    user = db.transaction(_apply)
    images.drop_upload(removed.get("url"))
    return {"ok": True, "user": db.public_user(user, with_password=True)}


@router.delete("/{user_id}")
def delete_user(user_id: int, me: Dict[str, Any] = Depends(require_superadmin)):
    """Удалить учителя. Себя удалить нельзя — иначе можно остаться без администратора."""
    if user_id == me["id"]:
        raise bad_request("Нельзя удалить самого себя")

    removed: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = db.find_user(database, user_id)
        if not user:
            return db.NO_SAVE
        removed["url"] = images.avatar_upload_url(user)
        if db.is_superadmin(user):
            admins = [u for u in database["users"] if db.is_superadmin(u)]
            if len(admins) == 1:
                raise bad_request("Нельзя удалить последнего администратора")
        database["users"] = [u for u in database["users"] if u["id"] != user_id]
        for entry in database.get("schedule", []):
            if entry.get("user_id") == user_id:
                entry["user_id"] = None
        return None

    db.transaction(_apply)
    images.drop_upload(removed.get("url"))
    return {"ok": True}
