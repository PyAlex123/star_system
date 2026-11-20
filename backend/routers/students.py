"""Панель учителя: ученики, импорт, коды карточек, логины и пароли."""
from typing import Annotated, Any, Dict, List

from fastapi import APIRouter, Body, Depends

from .. import db, images, passwords, schemas
from ..security import require_teacher
from .common import (bad_request, class_or_404, clean_name, drop_photos, school_or_404,
                     student_or_404)

router = APIRouter(prefix="/api", tags=["students"], dependencies=[Depends(require_teacher)])


def _make_student(database: Dict[str, Any], class_id: int, name: str, stars: int) -> Dict[str, Any]:
    """Новый ученик сразу со всем нужным: кодом карточки, логином, паролем, сессией."""
    tokens = {s.get("token") for s in database["students"]}
    student_id = db.next_student_id(database)
    student = {
        "id": student_id,
        "class_id": class_id,
        "name": name,
        "total_stars": max(0, stars),
        "history": [],
        "token": db.new_token(tokens),
        "tg_id": None,
        "tg_username": None,
        "bound_at": None,
        "avatar": db.default_avatar(student_id),
        "login": passwords.suggest_login(name, db.logins_taken(database)),
        "session_key": db.new_session_key(),
    }
    passwords.set_credentials(student)   # заполняет password_hash и password_shown
    if student["total_stars"] > 0:
        student["history"].append({
            "date": db.today(),
            "delta": student["total_stars"],
            "reason": "Стартовый баланс",
        })
    return student


@router.post("/students")
def add_student(item: schemas.StudentCreate):
    name = clean_name(item.name, "Укажите фамилию и имя ученика")

    def _apply(database: Dict[str, Any]):
        class_or_404(database, item.class_id)
        student = _make_student(database, item.class_id, name, item.total_stars)
        database["students"].append(student)
        return student

    return {"ok": True, "student": db.public_student(db.transaction(_apply))}


@router.post("/students/bulk")
def bulk_import(items: Annotated[List[schemas.BulkImportItem],
                                 Body(max_length=schemas.MAX_IMPORT_ROWS)]):
    """Импорт из Excel/CSV. Классы, которых ещё нет, создаются в указанной школе."""
    def _apply(database: Dict[str, Any]):
        added = 0
        for item in items:
            name = item.name.strip()
            if not name:
                continue
            school_or_404(database, item.school_id)
            cls = db.ensure_class(database, item.school_id, item.class_name.strip() or "Общий")
            student = _make_student(database, cls["id"], name, item.total_stars)
            if student["history"]:
                student["history"][0]["reason"] = "Импорт из таблицы"
            database["students"].append(student)
            added += 1
        return added

    return {"ok": True, "added": db.transaction(_apply)}


@router.patch("/students/{student_id}")
def update_student(student_id: int, item: schemas.StudentUpdate):
    """Правка имени и логина. Звёзды и история не затрагиваются."""
    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, student_id)
        changed = False

        if item.name is not None:
            student["name"] = clean_name(item.name, "Имя не может быть пустым")
            changed = True

        if item.login is not None:
            login = passwords.normalize_login(item.login)
            if not passwords.LOGIN_RE.match(login):
                raise bad_request("Логин: латинские буквы, цифры, точка, дефис; от 3 до 31 символа")
            if login in db.logins_taken(database, except_id=student_id):
                raise bad_request("Такой логин уже занят другим учеником")
            student["login"] = login
            changed = True

        return student if changed else db.NO_SAVE

    result = db.transaction(_apply)
    return {"ok": True, "student": None if result is db.NO_SAVE else db.public_student(result)}


@router.delete("/students/{student_id}")
def delete_student(student_id: int):
    removed: Dict[str, list] = {}

    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            return db.NO_SAVE
        removed["photos"] = [images.avatar_upload_url(student)]
        database["students"] = [s for s in database["students"] if s["id"] != student_id]
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    return {"ok": True}


@router.post("/students/{student_id}/move")
def move_student(student_id: int, class_id: int):
    """Перевести ученика в другой класс — звёзды и история остаются при нём."""
    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, student_id)
        class_or_404(database, class_id)
        student["class_id"] = class_id
        return student

    return {"ok": True, "student": db.public_student(db.transaction(_apply))}


# ── Вход ученика: код карточки, Telegram, пароль ────────────────────────────

@router.post("/students/{student_id}/password")
def reset_password(student_id: int, item: schemas.PasswordReset):
    """Выдать ученику новый пароль. Пустое поле — система придумает сама.

    Пароль возвращается в открытом виде: учитель должен продиктовать его
    ребёнку и перепечатать карточку. Все сессии ученика завершаются — если
    пароль меняют из-за того, что его кто-то узнал, посторонний вылетит.
    """
    custom = None
    if (item.password or "").strip():
        try:
            custom = passwords.validate_new(item.password)
        except ValueError as exc:
            raise bad_request(str(exc))

    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, student_id)
        db.rotate_session(student)
        return passwords.set_credentials(student, custom)

    return {"ok": True, "password": db.transaction(_apply)}


@router.post("/students/{student_id}/reset-token")
def reset_token(student_id: int):
    """Новый QR-код: старая карточка перестаёт работать, Telegram отвязывается."""
    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, student_id)
        tokens = {s.get("token") for s in database["students"] if s["id"] != student_id}
        student["token"] = db.new_token(tokens)
        db.clear_telegram(student)
        db.rotate_session(student)
        return student["token"]

    return {"ok": True, "token": db.transaction(_apply)}


@router.post("/students/{student_id}/unbind")
def unbind_telegram(student_id: int):
    """Отвязать Telegram-аккаунт, код карточки остаётся прежним."""
    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, student_id)
        if student.get("tg_id") is None:
            return db.NO_SAVE
        db.clear_telegram(student)
        db.rotate_session(student)
        return None

    db.transaction(_apply)
    return {"ok": True}


@router.post("/students/{student_id}/reset-avatar")
def reset_avatar(student_id: int):
    """Сбросить аватар на стандартный — если ребёнок поставил неуместное фото."""
    removed: Dict[str, list] = {}

    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, student_id)
        removed["photos"] = [images.avatar_upload_url(student)]
        student["avatar"] = db.default_avatar(student_id)
        return student["avatar"]

    avatar = db.transaction(_apply)
    # Убираем и сам файл: иначе неуместное фото осталось бы доступно по прежнему адресу
    drop_photos(removed.get("photos"))
    return {"ok": True, "avatar": avatar}
