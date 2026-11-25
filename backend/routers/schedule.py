"""Расписание занятий: класс, день недели, время, кабинет, учитель.

Читают все роли (расписание приходит в /api/state и в кабинет ученика).
Добавляют, меняют и удаляют — учитель своей школы и администратор.

Панель показывает расписание таблицей: столбцы — дни недели, строки — уроки школы
(school.bells). Занятие, время которого совпадает с уроком, стоит в его ячейке
(entry.lesson = номер урока); в ячейке один класс. Остальные занятия — «в другое время»
(lesson = None). Время занятия (start, end) хранится всегда: по нему живут кабинет
ученика, полоска «сегодня» в журнале и статистика.
"""
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends

from .. import db, schemas
from ..security import require_teacher
from .common import bad_request, class_or_404, not_found, school_or_404

router = APIRouter(prefix="/api/schedule", tags=["schedule"], dependencies=[Depends(require_teacher)])

WEEKDAY_IN = ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье")


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


def _class_name(database: Dict[str, Any], class_id: int) -> str:
    return (db.find_class(database, class_id) or {}).get("name", "")


def _school_entries(database: Dict[str, Any], school_id: int) -> List[Dict[str, Any]]:
    class_ids = {c["id"] for c in db.school_classes(database, school_id)}
    return [e for e in database.get("schedule", []) if e.get("class_id") in class_ids]


def _cell_entry(database: Dict[str, Any], school_id: int, weekday: int, lesson: int,
                skip_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Занятие, которое стоит в ячейке таблицы школы (день + номер урока)."""
    return next((e for e in _school_entries(database, school_id)
                 if e["weekday"] == weekday and e.get("lesson") == lesson and e.get("id") != skip_id), None)


def _class_clash(schedule: Iterable[Dict[str, Any]], class_id: int, weekday: int, start: str, end: str,
                 skip_ids: Iterable[Optional[int]] = ()) -> Optional[Dict[str, Any]]:
    """Другое занятие этого класса в тот же день, пересекающееся по времени."""
    skip = set(skip_ids)
    return next((e for e in schedule if e.get("id") not in skip and e["class_id"] == class_id
                 and e["weekday"] == weekday and e["start"] < end and start < e["end"]), None)


def _clash_error(database: Dict[str, Any], class_id: int, clash: Dict[str, Any]):
    return bad_request(f"У класса {_class_name(database, class_id)} в этот день уже есть занятие "
                       f"{clash['start']}–{clash['end']}")


def _place(database: Dict[str, Any], entry: Dict[str, Any], school_id: int) -> None:
    """Ставит занятие в ячейку таблицы, если его время совпало с уроком школы.
    В ячейке один класс: занятая другим классом ячейка — ошибка."""
    bells = db.school_bells(db.find_school(database, school_id))
    number = db.bell_number(bells, entry["start"], entry["end"])
    if number:
        other = _cell_entry(database, school_id, entry["weekday"], number, skip_id=entry.get("id"))
        if other:
            raise bad_request(f"{number}-й урок {WEEKDAY_IN[entry['weekday'] - 1]} уже занят: "
                              f"{_class_name(database, other['class_id'])}")
    entry["lesson"] = number


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
            clash = _class_clash(schedule, cls["id"], weekday, item.start, item.end)
            if clash:
                raise _clash_error(database, cls["id"], clash)
            entry = {"id": db.next_id(schedule), "class_id": cls["id"], "weekday": weekday,
                     "start": item.start, "end": item.end, "room": item.room.strip(),
                     "user_id": teacher, "created_at": db.today(), "lesson": None}
            _place(database, entry, cls["school_id"])
            schedule.append(entry)
            created.append(entry)
        return created

    return {"ok": True, "created": db.transaction(_apply)}


@router.put("/bells/{school_id}")
def set_bells(school_id: int, item: schemas.SchoolBells, user: Dict[str, Any] = Depends(require_teacher)):
    """Время уроков школы и учебные дни. Занятия в таблице переезжают на новое время
    своего урока; занятия «в другое время», совпавшие с уроком, встают в таблицу."""
    bells = [{"start": b.start, "end": b.end} for b in item.bells]
    for number, bell in enumerate(bells, 1):
        if bell["end"] <= bell["start"]:
            raise bad_request(f"{number}-й урок должен заканчиваться позже, чем начинается")
        if number > 1 and bell["start"] < bells[number - 2]["end"]:
            raise bad_request(f"{number}-й урок начинается раньше, чем закончится {number - 1}-й")

    def _apply(database: Dict[str, Any]):
        school = school_or_404(database, school_id, user)
        entries = _school_entries(database, school_id)
        beyond = [e for e in entries if (e.get("lesson") or 0) > len(bells)]
        if beyond:
            names = ", ".join(sorted({_class_name(database, e["class_id"]) for e in beyond}))
            raise bad_request(f"На уроках после {len(bells)}-го стоят занятия ({names}) — сначала уберите их")

        moved = {e["id"]: bells[e["lesson"] - 1] for e in entries if e.get("lesson")}
        timed = [dict(e, **moved.get(e["id"], {})) for e in entries]
        for entry in timed:
            if entry["id"] in moved:
                clash = _class_clash(timed, entry["class_id"], entry["weekday"], entry["start"], entry["end"],
                                     skip_ids=[entry["id"]])
                if clash:
                    raise bad_request(f"{_class_name(database, entry['class_id'])}: {entry['lesson']}-й урок "
                                      f"{WEEKDAY_IN[entry['weekday'] - 1]} пересечётся с занятием "
                                      f"{clash['start']}–{clash['end']}")

        school["bells"] = bells
        school["days"] = item.days
        for entry in entries:
            if entry["id"] in moved:
                entry.update(moved[entry["id"]])
        db.link_schedule_to_bells(database, school_id)
        return {"bells": school["bells"], "days": school["days"]}

    return {"ok": True, **db.transaction(_apply)}


@router.put("/cell")
def set_cell(item: schemas.ScheduleCell, user: Dict[str, Any] = Depends(require_teacher)):
    """Ячейка таблицы: поставить класс, заменить его другим или убрать (class_id = null).
    Учитель — переданный, иначе прежний учитель ячейки, иначе тот, кто нажал."""
    def _apply(database: Dict[str, Any]):
        school = school_or_404(database, item.school_id, user)
        bells = db.school_bells(school)
        if item.lesson > len(bells):
            raise bad_request("Такого урока нет во времени уроков школы")
        current = _cell_entry(database, item.school_id, item.weekday, item.lesson)
        schedule = database.setdefault("schedule", [])

        if item.class_id is None:
            if not current:
                return db.NO_SAVE
            database["schedule"] = [e for e in schedule if e.get("id") != current["id"]]
            return {"entry": None}

        cls = class_or_404(database, item.class_id, user)
        if cls["school_id"] != item.school_id:
            raise not_found("Класс не найден")
        wanted = (item.user_id if "user_id" in item.model_fields_set
                  else current["user_id"] if current else user["id"])
        teacher = _check_teacher(database, wanted, item.school_id)

        if current and current["class_id"] == cls["id"]:
            if current.get("user_id") == teacher:
                return db.NO_SAVE
            current["user_id"] = teacher
            return {"entry": current}

        bell = bells[item.lesson - 1]
        clash = _class_clash(schedule, cls["id"], item.weekday, bell["start"], bell["end"],
                             skip_ids=[current["id"]] if current else [])
        if clash:
            raise _clash_error(database, cls["id"], clash)
        if current:
            schedule.remove(current)
        entry = {"id": db.next_id(schedule), "class_id": cls["id"], "weekday": item.weekday,
                 "start": bell["start"], "end": bell["end"], "room": "", "user_id": teacher,
                 "created_at": db.today(), "lesson": item.lesson}
        schedule.append(entry)
        return {"entry": entry}

    result = db.transaction(_apply)
    return {"ok": True, **({"entry": None, "unchanged": True} if result is db.NO_SAVE else result)}


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
        clash = _class_clash(database["schedule"], entry["class_id"], entry["weekday"], entry["start"], entry["end"],
                             skip_ids=[entry["id"]])
        if clash:
            raise _clash_error(database, entry["class_id"], clash)
        _place(database, entry, cls["school_id"])
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
