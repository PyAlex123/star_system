"""Учётные записи панели: суперадмин заводит сотрудников — учителей и администрацию
школы — и раздаёт им школы. Пароль сотрудника виден суперадмину, пока сотрудник
не сменит его сам (как у учеников)."""
import secrets
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import config, db, images, passwords, schemas
from ..checkin import clock as checkin_clock, service as checkin
from ..security import require_superadmin
from .common import bad_request, clean_name, not_found

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
        schools = _clean_schools(database, item.school_ids)
        return db.make_user(database, login, name, item.role, password, schools)

    user = db.transaction(_apply)
    return {"ok": True, "user": db.public_user(user, with_password=True), "password": password}


@router.patch("/{user_id}")
def update_user(user_id: int, item: schemas.UserUpdate, me: Dict[str, Any] = Depends(require_superadmin)):
    """Имя и школы учителя. Снятая школа пропадает у него сразу, без повторного входа.
    Здесь же поля отметок: ставка, «отмечается в боте», «ведёт отметки»."""
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        before.update(user)
        if item.name is not None:
            user["name"] = clean_name(item.name, "Введите имя учителя")
        if item.school_ids is not None:
            user["school_ids"] = _clean_schools(database, item.school_ids)
        if item.role is not None and item.role != user.get("role"):
            if db.is_superadmin(user):
                raise bad_request("Роль администратора не меняется")
            user["role"] = item.role
            user["session_key"] = db.new_session_key()   # новые права — с нового входа
        if "hourly_rate" in item.model_fields_set:
            user["hourly_rate"] = item.hourly_rate
        if item.checkin_tracked is not None:
            user["checkin_tracked"] = item.checkin_tracked
        if item.checkin_manager is not None:
            if item.checkin_manager and user.get("role") != db.ROLE_TEACHER:
                raise bad_request("Вести отметки можно поручить только учителю: "
                                  "администрация школы только смотрит, администратору раздел открыт и так")
            user["checkin_manager"] = item.checkin_manager
        if user.get("role") != db.ROLE_TEACHER and user.get("checkin_manager"):
            user["checkin_manager"] = False                # стал администрацией школы — поручение снято
        return user

    user = db.transaction(_apply)
    checkin.audit_staff(me, user_id, "update", before, user)
    return {"ok": True, "user": db.public_user(user, with_password=True)}


@router.post("/{user_id}/invite")
def invite_to_checkins(user_id: int, me: Dict[str, Any] = Depends(require_superadmin)):
    """Ссылка-приглашение в бот отметок: одноразовая, живёт неделю.
    Сотрудник сразу начинает отмечаться — его уроки появятся в «Сегодня»."""
    code = secrets.token_urlsafe(9)
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        before.update(user)
        user["invite_code"] = code
        user["invite_created_at"] = checkin_clock.iso(checkin_clock.now_utc())
        # Администратору Telegram нужен для кнопок в группе «Отметки», а не для отметок:
        # его занятия (он внёс их сам) в проверку не попадают
        if not db.is_superadmin(user):
            user["checkin_tracked"] = True
        return user

    user = db.transaction(_apply)
    checkin.audit_staff(me, user_id, "invite", before, user)
    link = config.checkin_invite_link(code)
    return {"ok": True, "link": link or None, "bot_connected": bool(link), "days": config.CHECKIN_INVITE_DAYS,
            "user": db.public_user(user, with_password=True)}


@router.delete("/{user_id}/telegram")
def unbind_telegram(user_id: int, me: Dict[str, Any] = Depends(require_superadmin)):
    """Отвязать Telegram преподавателя (новый телефон, чужой аккаунт). Согласие
    на отметки спросим заново; для новой привязки — новое приглашение."""
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        before.update(user)
        for field in ("tg_id", "tg_username", "tg_bound_at", "tg_blocked_at", "consent_at",
                      "invite_code", "invite_created_at"):
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
