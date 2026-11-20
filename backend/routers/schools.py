"""Панель учителя: снимок журнала, школы и классы."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from .. import db, images, schemas
from ..security import require_teacher
from .common import bad_request, class_or_404, clean_name, drop_photos, school_or_404

router = APIRouter(prefix="/api", tags=["schools"], dependencies=[Depends(require_teacher)])


@router.get("/state")
def get_state():
    """Снимок базы для панели. Хеши паролей и ключи сессий не покидают сервер."""
    return db.public_state(db.load_db())


# ── Школы ─────────────────────────────────────────────────────────────────────

@router.post("/schools")
def add_school(item: schemas.SchoolCreate):
    name = clean_name(item.name, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        if any(s["name"].strip().lower() == name.lower() for s in database["schools"]):
            raise bad_request("Такая школа уже есть")
        school = {"id": db.next_id(database["schools"]), "name": name}
        database["schools"].append(school)
        return school

    return {"ok": True, "school": db.transaction(_apply)}


@router.patch("/schools/{school_id}")
def rename_school(school_id: int, item: schemas.SchoolUpdate):
    name = clean_name(item.name, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        school = school_or_404(database, school_id)
        school["name"] = name
        return school

    return {"ok": True, "school": db.transaction(_apply)}


@router.delete("/schools/{school_id}")
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
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    return {"ok": True}


# ── Классы ────────────────────────────────────────────────────────────────────

@router.post("/classes")
def add_class(item: schemas.ClassCreate):
    name = clean_name(item.name, "Введите название класса")

    def _apply(database: Dict[str, Any]):
        school_or_404(database, item.school_id)
        if db.find_class_by_name(database, item.school_id, name):
            raise bad_request("В этой школе такой класс уже есть")
        cls = {"id": db.next_id(database["classes"]), "school_id": item.school_id, "name": name}
        database["classes"].append(cls)
        return cls

    return {"ok": True, "class": db.transaction(_apply)}


@router.patch("/classes/{class_id}")
def update_class(class_id: int, item: schemas.ClassUpdate):
    """Переименовать класс или перенести его в другую школу."""
    def _apply(database: Dict[str, Any]):
        cls = class_or_404(database, class_id)

        if item.school_id is not None:
            school_or_404(database, item.school_id)
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
def delete_class(class_id: int):
    """Удаляет класс вместе с учениками и их фото."""
    removed: Dict[str, list] = {}

    def _apply(database: Dict[str, Any]):
        if not db.find_class(database, class_id):
            return db.NO_SAVE
        removed["photos"] = [images.avatar_upload_url(s) for s in database["students"]
                             if s.get("class_id") == class_id]
        database["students"] = [s for s in database["students"]
                                if s.get("class_id") != class_id]
        database["classes"] = [c for c in database["classes"] if c["id"] != class_id]
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    return {"ok": True}
