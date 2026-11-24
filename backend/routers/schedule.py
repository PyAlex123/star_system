"""Расписание занятий: класс, день недели, время, кабинет, учитель.

Читают все роли (расписание приходит в /api/state и в кабинет ученика).
Добавляют, меняют и удаляют — учитель своей школы и администратор.
"""
from datetime import date
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends

from .. import db, schemas
from ..checkin import domain
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


def _check_dates(bounds: Tuple[Optional[date], Optional[date]]) -> None:
    start, end = bounds
    if start and end and end < start:
        raise bad_request("Занятие должно действовать хотя бы один день: «по» не раньше «с»")


def _period(bounds: Tuple[Optional[date], Optional[date]]) -> str:
    start, end = bounds
    if end:
        return f" (действует по {end.strftime('%d.%m.%Y')})"
    if start and start > date.fromisoformat(db.today()):
        return f" (с {start.strftime('%d.%m.%Y')})"
    return ""


def _clash(schedule: list, cls: Dict[str, Any], weekday: int, start: str, end: str,
           bounds: Tuple[Optional[date], Optional[date]], skip_id: Optional[int] = None) -> None:
    """Два занятия класса в одно время — ошибка, если их сроки действия пересекаются.
    Закрытое занятие (valid_to в прошлом) не мешает новому в том же месте сетки."""
    for other in schedule:
        if other.get("id") == skip_id or other["class_id"] != cls["id"] or other["weekday"] != weekday:
            continue
        if not (other["start"] < end and start < other["end"]):
            continue
        if domain.ranges_overlap(domain.entry_bounds(other), bounds):
            raise bad_request(f"У класса {cls['name']} в этот день уже есть занятие "
                              f"{other['start']}–{other['end']}{_period(domain.entry_bounds(other))}")


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
        draft = {"created_at": db.today(), "valid_from": item.valid_from, "valid_to": item.valid_to}
        bounds = domain.entry_bounds(draft)
        _check_dates(bounds)
        for weekday in sorted(set(item.weekdays)):
            _clash(schedule, cls, weekday, item.start, item.end, bounds)
            entry = {"id": db.next_counted_id(database, "schedule"), "class_id": cls["id"], "weekday": weekday,
                     "start": item.start, "end": item.end, "room": item.room.strip(),
                     "user_id": teacher, "created_at": db.today(),
                     "valid_from": item.valid_from, "valid_to": item.valid_to}
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
        for field in ("valid_from", "valid_to"):
            if field in item.model_fields_set:
                entry[field] = getattr(item, field)
        bounds = domain.entry_bounds(entry)
        _check_dates(bounds)
        _clash(database["schedule"], cls, entry["weekday"], entry["start"], entry["end"], bounds,
               skip_id=entry["id"])
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
