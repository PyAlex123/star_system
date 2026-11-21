"""Панель: снимок журнала, школы и классы.

Школы заводит и удаляет только суперадмин. Учитель работает с классами
в своих школах; чужие для него не существуют (404).
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import db, images, schemas
from ..security import require_superadmin, require_teacher
from .common import bad_request, class_or_404, clean_name, drop_photos, school_or_404

router = APIRouter(prefix="/api", tags=["schools"], dependencies=[Depends(require_teacher)])


@router.get("/state")
def get_state(user: Dict[str, Any] = Depends(require_teacher)):
    """Снимок базы для панели: только видимые пользователю школы.
    Хеши паролей и ключи сессий не покидают сервер."""
    return db.public_state(db.load_db(), user)


# ── Школы ─────────────────────────────────────────────────────────────────────

@router.post("/schools", dependencies=[Depends(require_superadmin)])
def add_school(item: schemas.SchoolCreate):
    name = clean_name(item.name, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        if any(s["name"].strip().lower() == name.lower() for s in database["schools"]):
            raise bad_request("Такая школа уже есть")
        school = {"id": db.next_id(database["schools"]), "name": name}
        database["schools"].append(school)
        return school

    return {"ok": True, "school": db.transaction(_apply)}


@router.patch("/schools/{school_id}", dependencies=[Depends(require_superadmin)])
def rename_school(school_id: int, item: schemas.SchoolUpdate):
    name = clean_name(item.name, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        school = school_or_404(database, school_id)
        school["name"] = name
        return school

    return {"ok": True, "school": db.transaction(_apply)}


@router.delete("/schools/{school_id}", dependencies=[Depends(require_superadmin)])
def delete_school(school_id: int):
    """Удаляет школу вместе со всеми её классами, учениками и их фото."""
    removed: Dict[str, list] = {}

    def _apply(database: Dict[str, Any]):
        if not db.find_school(database, school_id):
            return db.NO_SAVE
        if len(database["schools"]) == 1:
            raise bad_request("Нельзя удалить единственную школу")

        class_ids = {c["id"] for c in db.school_classes(database, school_id)}
        removed["photos"] = [images.avatar_upload_url(s) for s in database["students"]
                             if s.get("class_id") in class_ids]
        database["students"] = [s for s in database["students"]
                                if s.get("class_id") not in class_ids]
        database["classes"] = [c for c in database["classes"] if c["school_id"] != school_id]
        database["lessons"] = [lesson for lesson in database["lessons"]
                               if lesson.get("class_id") not in class_ids]
        database["schools"] = [s for s in database["schools"] if s["id"] != school_id]
        for user in database.get("users", []):
            if school_id in (user.get("school_ids") or []):
                user["school_ids"] = [i for i in user["school_ids"] if i != school_id]
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    return {"ok": True}


# ── Классы ────────────────────────────────────────────────────────────────────

@router.post("/classes")
def add_class(item: schemas.ClassCreate, user: Dict[str, Any] = Depends(require_teacher)):
    name = clean_name(item.name, "Введите название класса")

    def _apply(database: Dict[str, Any]):
        school_or_404(database, item.school_id, user)
        if db.find_class_by_name(database, item.school_id, name):
            raise bad_request("В этой школе такой класс уже есть")
        cls = {"id": db.next_id(database["classes"]), "school_id": item.school_id, "name": name}
        database["classes"].append(cls)
        return cls

    return {"ok": True, "class": db.transaction(_apply)}


@router.post("/classes/bulk")
def add_classes(item: schemas.ClassBulkCreate, user: Dict[str, Any] = Depends(require_teacher)):
    """Несколько классов за раз — из сетки «5-А, 5-Б, 6-В». Уже существующие пропускаются."""
    names: List[str] = []
    for raw in item.names:
        name = (raw or "").strip()
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    if not names:
        raise bad_request("Выберите хотя бы один класс")

    def _apply(database: Dict[str, Any]):
        school_or_404(database, item.school_id, user)
        created = []
        for name in names:
            if db.find_class_by_name(database, item.school_id, name):
                continue
            cls = {"id": db.next_id(database["classes"]), "school_id": item.school_id, "name": name}
            database["classes"].append(cls)
            created.append(cls)
        return created if created else db.NO_SAVE

    result = db.transaction(_apply)
    created = [] if result is db.NO_SAVE else result
    return {"ok": True, "created": created, "skipped": len(names) - len(created)}


@router.patch("/classes/{class_id}")
def update_class(class_id: int, item: schemas.ClassUpdate,
                 user: Dict[str, Any] = Depends(require_teacher)):
    """Переименовать класс или перенести его в другую школу (из своих)."""
    def _apply(database: Dict[str, Any]):
        cls = class_or_404(database, class_id, user)

        if item.school_id is not None:
            school_or_404(database, item.school_id, user)
            cls["school_id"] = item.school_id

        if item.name is not None:
            name = clean_name(item.name, "Введите название класса")
            twin = db.find_class_by_name(database, cls["school_id"], name)
            if twin and twin["id"] != class_id:
                raise bad_request("В этой школе такой класс уже есть")
            cls["name"] = name
        return cls

    return {"ok": True, "class": db.transaction(_apply)}


@router.delete("/classes/{class_id}")
def delete_class(class_id: int, user: Dict[str, Any] = Depends(require_teacher)):
    """Удаляет класс вместе с учениками и их фото."""
    removed: Dict[str, list] = {}

    def _apply(database: Dict[str, Any]):
        if not db.find_class(database, class_id):
            return db.NO_SAVE
        class_or_404(database, class_id, user)
        removed["photos"] = [images.avatar_upload_url(s) for s in database["students"]
                             if s.get("class_id") == class_id]
        database["students"] = [s for s in database["students"]
                                if s.get("class_id") != class_id]
        database["classes"] = [c for c in database["classes"] if c["id"] != class_id]
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    return {"ok": True}
