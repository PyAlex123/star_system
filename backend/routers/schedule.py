"""Расписание занятий: класс, день недели, время, кабинет, учитель.

Читают все роли (расписание приходит в /api/state и в кабинет ученика).
Добавляют, меняют и удаляют — учитель своей школы и администратор.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from .. import db, schemas
from ..security import require_teacher
from .common import bad_request, class_or_404, not_found

router = APIRouter(prefix="/api/schedule", tags=["schedule"], dependencies=[Depends(require_teacher)])


def _entry_or_404(database: Dict[str, Any], entry_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    entry = next((e for e in database.get("schedule", []) if e.get("id") == entry_id), None)
    if not entry:
        raise not_found("Занятие не найдено")
    class_or_404(database, entry["class_id"], user)      # чужая школа — как несуществующее
    return entry


def _check_teacher(database: Dict[str, Any], user_id: Optional[int], school_id: int) -> Optional[int]:
    """Учитель занятия должен работать в этой школе (или быть администратором)."""
    if user_id is None:
        return None
    teacher = db.find_user(database, user_id)
    if not teacher or not (db.is_superadmin(teacher) or school_id in (teacher.get("school_ids") or [])):
        raise bad_request("Этот учитель не работает в этой школе")
    return user_id


def _check_time(start: str, end: str) -> None:
    if end <= start:
        raise bad_request("Занятие должно заканчиваться позже, чем начинается")


@router.post("")
def add_lessons(item: schemas.ScheduleCreate, user: Dict[str, Any] = Depends(require_teacher)):
    """Занятие сразу на несколько дней недели. Учитель по умолчанию — тот, кто добавил."""
    _check_time(item.start, item.end)

    def _apply(database: Dict[str, Any]):
        cls = class_or_404(database, item.class_id, user)
        teacher = _check_teacher(database, item.user_id if item.user_id is not None else user["id"],
                                 cls["school_id"])
        schedule = database.setdefault("schedule", [])
        created = []
        for weekday in sorted(set(item.weekdays)):
            clash = next((e for e in schedule if e["class_id"] == cls["id"] and e["weekday"] == weekday
                          and e["start"] < item.end and item.start < e["end"]), None)
            if clash:
                raise bad_request(f"У класса {cls['name']} в этот день уже есть занятие "
                                  f"{clash['start']}–{clash['end']}")
            entry = {"id": db.next_counted_id(database, "schedule"), "class_id": cls["id"], "weekday": weekday,
                     "start": item.start, "end": item.end, "room": item.room.strip(),
                     "user_id": teacher, "created_at": db.today(),
                     "valid_from": None, "valid_to": None}
            schedule.append(entry)
            created.append(entry)
        return created

    return {"ok": True, "created": db.transaction(_apply)}


@router.patch("/{entry_id}")
def update_lesson(entry_id: int, item: schemas.ScheduleUpdate, user: Dict[str, Any] = Depends(require_teacher)):
    def _apply(database: Dict[str, Any]):
        entry = _entry_or_404(database, entry_id, user)
        cls = db.find_class(database, entry["class_id"])
        if item.weekday is not None:
            entry["weekday"] = item.weekday
        if item.start is not None:
            entry["start"] = item.start
        if item.end is not None:
            entry["end"] = item.end
        _check_time(entry["start"], entry["end"])
        if item.room is not None:
            entry["room"] = item.room.strip()
        if "user_id" in item.model_fields_set:
            entry["user_id"] = _check_teacher(database, item.user_id, cls["school_id"])
        return entry

    return {"ok": True, "entry": db.transaction(_apply)}


@router.delete("/{entry_id}")
def delete_lesson(entry_id: int, user: Dict[str, Any] = Depends(require_teacher)):
    def _apply(database: Dict[str, Any]):
        _entry_or_404(database, entry_id, user)
        database["schedule"] = [e for e in database["schedule"] if e.get("id") != entry_id]
        return None

    db.transaction(_apply)
    return {"ok": True}
