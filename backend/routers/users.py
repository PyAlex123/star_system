"""Учётные записи панели: суперадмин заводит учителей и раздаёт им школы."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import db, passwords, schemas
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
        return db.make_user(database, login, name, db.ROLE_TEACHER, password, schools)

    user = db.transaction(_apply)
    return {"ok": True, "user": db.public_user(user), "password": password}


@router.patch("/{user_id}")
def update_user(user_id: int, item: schemas.UserUpdate):
    """Имя и школы учителя. Снятая школа пропадает у него сразу, без повторного входа."""
    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        if item.name is not None:
            user["name"] = clean_name(item.name, "Введите имя учителя")
        if item.school_ids is not None:
            user["school_ids"] = _clean_schools(database, item.school_ids)
        return user

    return {"ok": True, "user": db.public_user(db.transaction(_apply))}


@router.post("/{user_id}/password")
def reset_user_password(user_id: int, item: schemas.PasswordReset):
    """Новый пароль учителю. Все его входы завершаются."""
    password = _password(item.password)

    def _apply(database: Dict[str, Any]):
        user = _user_or_404(database, user_id)
        user["password_hash"] = passwords.hash_password(password)
        user["session_key"] = db.new_session_key()
        return None

    db.transaction(_apply)
    return {"ok": True, "password": password}


@router.delete("/{user_id}")
def delete_user(user_id: int, me: Dict[str, Any] = Depends(require_superadmin)):
    """Удалить учителя. Себя удалить нельзя — иначе можно остаться без администратора."""
    if user_id == me["id"]:
        raise bad_request("Нельзя удалить самого себя")

    def _apply(database: Dict[str, Any]):
        user = db.find_user(database, user_id)
        if not user:
            return db.NO_SAVE
        if db.is_superadmin(user):
            admins = [u for u in database["users"] if db.is_superadmin(u)]
            if len(admins) == 1:
                raise bad_request("Нельзя удалить последнего администратора")
        database["users"] = [u for u in database["users"] if u["id"] != user_id]
        return None

    db.transaction(_apply)
    return {"ok": True}
