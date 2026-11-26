"""Панель: снимок журнала, школы и классы.

Школы заводит и удаляет только суперадмин. Учитель работает с классами
в своих школах; чужие для него не существуют (404).
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import db, images, schemas
from .. import themes
from ..monitoring import store as monitoring_store
from ..security import require_panel, require_superadmin, require_teacher
from .common import TEACH, bad_request, class_or_404, clean_name, drop_photos, not_found, school_or_404

router = APIRouter(prefix="/api", tags=["schools"], dependencies=[Depends(require_teacher)])
# Оформление школы меняет и администрация школы — у неё нет прав «учителя» на остальное
theme_router = APIRouter(prefix="/api", tags=["schools"], dependencies=[Depends(require_panel)])


# ── Школы ─────────────────────────────────────────────────────────────────────

@router.post("/schools", dependencies=[Depends(require_superadmin)])
def add_school(item: schemas.SchoolCreate):
    name = clean_name(item.name, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        if any(s["name"].strip().lower() == name.lower() for s in database["schools"]):
            raise bad_request("Такая школа уже есть")
        school = {"id": db.next_counted_id(database, "schools"), "name": name, "theme": themes.DEFAULT_THEME,
                  "bells": db.default_bells(), "days": db.SCHOOL_DAYS[0], **db.SCHOOL_CHECKIN_DEFAULTS}
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


@theme_router.put("/schools/{school_id}/theme")
def set_school_theme(school_id: int, item: schemas.SchoolTheme,
                     user: Dict[str, Any] = Depends(require_panel)):
    """Тема оформления школы. Меняют администратор, учителя и администрация этой школы:
    тему увидят все её ученики, учителя и печатные карточки."""
    def _apply(database: Dict[str, Any]):
        school = school_or_404(database, school_id, user)
        if school.get("theme") == item.theme:
            return db.NO_SAVE
        school["theme"] = item.theme
        return None

    db.transaction(_apply)
    return {"ok": True, "theme": item.theme}


@router.put("/schools/{school_id}/staff/{user_id}")
def set_school_staff(school_id: int, user_id: int, item: schemas.StaffPosts,
                     me: Dict[str, Any] = Depends(require_superadmin)):
    """Раздел «Школы»: должности сотрудника в этой школе. Пустой список — убрать из школы;
    перестал быть учителем — его занятия в школе остаются без учителя."""
    from .users import apply_posts

    def _apply(database: Dict[str, Any]):
        school_or_404(database, school_id)
        user = db.find_user(database, user_id)
        if not user:
            raise not_found("Сотрудник не найден")
        if db.is_superadmin(user):
            raise bad_request("Администратор работает во всех школах — должности ему не нужны")
        posts = dict(user.get("posts") or {})
        posts[str(school_id)] = list(item.posts)
        apply_posts(database, me, user, posts)
        return user

    user = db.transaction(_apply)
    return {"ok": True, "user": db.public_user(user, with_password=True)}


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
        database["schedule"] = [e for e in database.get("schedule", []) if e.get("class_id") not in class_ids]
        database["schedule_log"] = [r for r in database.get("schedule_log", []) if r.get("school_id") != school_id]
        database["schools"] = [s for s in database["schools"] if s["id"] != school_id]
        db.drop_school_posts(database, school_id)
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    monitoring_store.drop_school(school_id)
    return {"ok": True}


# ── Классы ────────────────────────────────────────────────────────────────────

@router.post("/classes")
def add_class(item: schemas.ClassCreate, user: Dict[str, Any] = Depends(require_teacher)):
    name = clean_name(item.name, "Введите название класса")

    def _apply(database: Dict[str, Any]):
        school_or_404(database, item.school_id, user, need=TEACH)
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
        school_or_404(database, item.school_id, user, need=TEACH)
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
        cls = class_or_404(database, class_id, user, need=TEACH)

        if item.school_id is not None:
            school_or_404(database, item.school_id, user, need=TEACH)
            cls["school_id"] = item.school_id

        if item.name is not None:
            name = clean_name(item.name, "Введите название класса")
            twin = db.find_class_by_name(database, cls["school_id"], name)
            if twin and twin["id"] != class_id:
                raise bad_request("В этой школе такой класс уже есть")
            cls["name"] = name
        return cls

    return {"ok": True, "class": db.transaction(_apply)}


@router.delete("/classes/{class_id}/students")
def clear_class(class_id: int, user: Dict[str, Any] = Depends(require_teacher)):
    """Удаляет всех учеников класса с их фото; сам класс и его расписание остаются —
    например, чтобы заново загрузить неправильно заполненный список."""
    removed: Dict[str, list] = {"photos": []}

    def _apply(database: Dict[str, Any]):
        class_or_404(database, class_id, user, need=TEACH)
        gone = [s for s in database["students"] if s.get("class_id") == class_id]
        if not gone:
            return db.NO_SAVE
        removed["photos"] = [images.avatar_upload_url(s) for s in gone]
        database["students"] = [s for s in database["students"] if s.get("class_id") != class_id]
        return len(gone)

    result = db.transaction(_apply)
    if result is db.NO_SAVE:
        return {"ok": True, "removed": 0}
    drop_photos(removed["photos"])
    return {"ok": True, "removed": result}


@router.delete("/classes/{class_id}")
def delete_class(class_id: int, user: Dict[str, Any] = Depends(require_teacher)):
    """Удаляет класс вместе с учениками и их фото."""
    removed: Dict[str, list] = {}

    def _apply(database: Dict[str, Any]):
        if not db.find_class(database, class_id):
            return db.NO_SAVE
        class_or_404(database, class_id, user, need=TEACH)
        removed["photos"] = [images.avatar_upload_url(s) for s in database["students"]
                             if s.get("class_id") == class_id]
        database["students"] = [s for s in database["students"]
                                if s.get("class_id") != class_id]
        database["classes"] = [c for c in database["classes"] if c["id"] != class_id]
        database["schedule"] = [e for e in database.get("schedule", []) if e.get("class_id") != class_id]
        return None

    db.transaction(_apply)
    drop_photos(removed.get("photos"))
    return {"ok": True}
