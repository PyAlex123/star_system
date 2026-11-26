"""Расписание занятий: класс, день недели, время, кабинет, учитель.

Читают все роли (расписание приходит в /api/state и в кабинет ученика).
Добавляют, меняют и удаляют — учитель своей школы и администратор.

Панель показывает расписание таблицей: столбцы — дни недели, строки — уроки школы
(school.bells). Занятие, время которого совпадает с уроком, стоит в его ячейке
(entry.lesson = номер урока). В ячейке один урок: один класс или несколько объединённых —
тогда это несколько занятий (по одному на класс) с общими учителем и кабинетом. Так кабинет
ученика и журнал работают с объединённым уроком как с обычным. Остальные занятия — «в другое время»
(lesson = None). Время занятия (start, end) хранится всегда: по нему живут кабинет
ученика, полоска «сегодня» в журнале и статистика.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends

from .. import db, schemas
from ..security import require_panel
from .common import bad_request, class_or_404, not_found, school_or_404

# Расписание меняют учитель, администратор и администрация школы (свои школы).
# Каждая правка подписана (updated_by у занятия) и попадает в журнал изменений школы.
router = APIRouter(prefix="/api/schedule", tags=["schedule"], dependencies=[Depends(require_panel)])

WEEKDAY_IN = ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье")
WEEKDAY_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
LOG_KEEP = 2000             # записей журнала изменений на все школы — старые отбрасываются


def _author(user: Dict[str, Any]) -> Dict[str, Any]:
    # Время — с часовым поясом (UTC): сервер в контейнере живёт по Гринвичу, а панель
    # покажет время по часам учителя
    return {"updated_by": user["id"], "updated_by_name": user.get("name") or user.get("login", ""),
            "updated_by_role": user.get("role"),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _stamp(entries: Iterable[Dict[str, Any]], user: Dict[str, Any]) -> None:
    """Подпись «кто и когда менял» у занятия — видна в окошке клетки и в окне занятия."""
    who = _author(user)
    for entry in entries:
        entry.update(who)


def _log(database: Dict[str, Any], school_id: int, user: Dict[str, Any], text: str) -> None:
    """Журнал изменений расписания: кто, когда, что было и что стало."""
    log = database.setdefault("schedule_log", [])
    who = _author(user)
    log.insert(0, {"school_id": school_id, "at": who["updated_at"], "user_id": user["id"],
                   "user_name": who["updated_by_name"], "role": who["updated_by_role"], "text": text})
    del log[LOG_KEEP:]


def _teacher_name(database: Dict[str, Any], user_id: Optional[int]) -> str:
    teacher = db.find_user(database, user_id) if user_id else None
    return (teacher or {}).get("name") or "без учителя"


def _names(database: Dict[str, Any], class_ids: Iterable[int]) -> str:
    return " + ".join(_class_name(database, c) for c in class_ids)


def _default_teacher(user: Dict[str, Any]) -> Optional[int]:
    """Учитель нового занятия по умолчанию — тот, кто добавил; администрация школы уроков не ведёт."""
    return None if db.is_school_admin(user) else user["id"]


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


def _cell_entries(database: Dict[str, Any], school_id: int, weekday: int, lesson: int,
                  skip_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Занятия в ячейке таблицы школы (день + номер урока): один класс или объединённые."""
    return [e for e in _school_entries(database, school_id)
            if e["weekday"] == weekday and e.get("lesson") == lesson and e.get("id") != skip_id]


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
    Если в ячейке уже есть другой класс, классы объединяются в один урок."""
    bells = db.school_bells(db.find_school(database, school_id))
    entry["lesson"] = db.bell_number(bells, entry["start"], entry["end"])


@router.post("")
def add_lessons(item: schemas.ScheduleCreate, user: Dict[str, Any] = Depends(require_panel)):
    """Занятие сразу на несколько дней недели. Учитель по умолчанию — тот, кто добавил."""
    _check_time(item.start, item.end)

    def _apply(database: Dict[str, Any]):
        cls = class_or_404(database, item.class_id, user)
        teacher = _check_teacher(database, item.user_id if item.user_id is not None else _default_teacher(user),
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
        _stamp(created, user)
        days = ", ".join(WEEKDAY_SHORT[w - 1] for w in sorted(set(item.weekdays)))
        _log(database, cls["school_id"], user,
             f"Добавлено занятие {cls['name']}: {days} {item.start}–{item.end}"
             + (f", каб. {item.room.strip()}" if item.room.strip() else "")
             + f", учитель — {_teacher_name(database, teacher)}")
        return created

    return {"ok": True, "created": db.transaction(_apply)}


@router.put("/bells/{school_id}")
def set_bells(school_id: int, item: schemas.SchoolBells, user: Dict[str, Any] = Depends(require_panel)):
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

        before = (school.get("bells"), school.get("days"))
        school["bells"] = bells
        school["days"] = item.days
        for entry in entries:
            if entry["id"] in moved:
                entry.update(moved[entry["id"]])
        if before != (bells, item.days):
            _log(database, school_id, user,
                 f"Время уроков: {len(bells)} {'урок' if len(bells) == 1 else 'урока' if len(bells) < 5 else 'уроков'}, "
                 f"первый {bells[0]['start']}–{bells[0]['end']}"
                 + ("; суббота — учебный день" if item.days >= 6 else "; без субботы"))
        db.link_schedule_to_bells(database, school_id)
        return {"bells": school["bells"], "days": school["days"]}

    return {"ok": True, **db.transaction(_apply)}


@router.put("/cell")
def set_cell(item: schemas.ScheduleCell, user: Dict[str, Any] = Depends(require_panel)):
    """Ячейка таблицы: поставить класс, заменить его другим, объединить несколько классов
    в один урок или убрать урок. Учитель — переданный, иначе прежний учитель ячейки,
    иначе тот, кто нажал; у всех классов урока он общий.
    Классы, которые остаются в уроке, сохраняют свои записи (кабинет, дату добавления);
    присоединённые получают кабинет урока. Один новый класс вместо прежнего — кабинет пустой."""
    def _apply(database: Dict[str, Any]):
        school = school_or_404(database, item.school_id, user)
        bells = db.school_bells(school)
        if item.lesson > len(bells):
            raise bad_request("Такого урока нет во времени уроков школы")
        current = _cell_entries(database, item.school_id, item.weekday, item.lesson)
        schedule = database.setdefault("schedule", [])
        if item.class_ids is not None:
            wanted_ids = list(dict.fromkeys(item.class_ids))
        else:
            wanted_ids = [] if item.class_id is None else [item.class_id]

        where = f"{WEEKDAY_SHORT[item.weekday - 1]}, {item.lesson}-й урок"
        if not wanted_ids:
            if not current:
                return db.NO_SAVE
            gone = {e["id"] for e in current}
            database["schedule"] = [e for e in schedule if e.get("id") not in gone]
            _log(database, item.school_id, user, f"{where}: убран {_names(database, [e['class_id'] for e in current])}")
            return {"entries": []}

        for class_id in wanted_ids:
            if class_or_404(database, class_id, user)["school_id"] != item.school_id:
                raise not_found("Класс не найден")
        wanted = (item.user_id if "user_id" in item.model_fields_set
                  else current[0]["user_id"] if current else _default_teacher(user))
        teacher = _check_teacher(database, wanted, item.school_id)

        keep = [e for e in current if e["class_id"] in wanted_ids]
        drop = [e for e in current if e["class_id"] not in wanted_ids]
        kept_ids = {e["class_id"] for e in keep}
        new_ids = [c for c in wanted_ids if c not in kept_ids]
        if not drop and not new_ids and all(e.get("user_id") == teacher for e in keep):
            return db.NO_SAVE

        bell = bells[item.lesson - 1]
        for class_id in new_ids:
            clash = _class_clash(schedule, class_id, item.weekday, bell["start"], bell["end"],
                                 skip_ids=[e["id"] for e in current])
            if clash:
                raise _clash_error(database, class_id, clash)
        room = keep[0].get("room", "") if keep else ""
        before_names = _names(database, [e["class_id"] for e in current])
        before_teacher = current[0].get("user_id") if current else None
        for entry in drop:
            schedule.remove(entry)
        for entry in keep:
            entry["user_id"] = teacher
        for class_id in new_ids:
            entry = {"id": db.next_id(schedule), "class_id": class_id, "weekday": item.weekday,
                     "start": bell["start"], "end": bell["end"], "room": room, "user_id": teacher,
                     "created_at": db.today(), "lesson": item.lesson}
            schedule.append(entry)
            keep.append(entry)
        order = {c: i for i, c in enumerate(wanted_ids)}
        keep.sort(key=lambda e: order[e["class_id"]])
        _stamp(keep, user)
        after_names = _names(database, wanted_ids)
        if not current:
            text = f"{where}: поставлен {after_names}, учитель — {_teacher_name(database, teacher)}"
        elif drop or new_ids:
            text = f"{where}: {before_names} → {after_names}"
            if before_teacher != teacher:
                text += f"; учитель {_teacher_name(database, before_teacher)} → {_teacher_name(database, teacher)}"
        else:
            text = f"{where}, {after_names}: учитель {_teacher_name(database, before_teacher)} → {_teacher_name(database, teacher)}"
        _log(database, item.school_id, user, text)
        return {"entries": keep}

    result = db.transaction(_apply)
    if result is db.NO_SAVE:
        return {"ok": True, "entry": None, "entries": [], "unchanged": True}
    entries = result["entries"]
    return {"ok": True, "entries": entries, "entry": entries[0] if entries else None}


@router.patch("/{entry_id}")
def update_lesson(entry_id: int, item: schemas.ScheduleUpdate, user: Dict[str, Any] = Depends(require_panel)):
    def _apply(database: Dict[str, Any]):
        entry = _entry_or_404(database, entry_id, user)
        cls = db.find_class(database, entry["class_id"])
        before = dict(entry)
        cell = (entry["weekday"], entry.get("lesson"))
        partners = (_cell_entries(database, cls["school_id"], entry["weekday"], entry["lesson"], skip_id=entry_id)
                    if entry.get("lesson") else [])
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
        # Объединённый урок остался в своей ячейке — кабинет и учитель у классов общие.
        if (entry["weekday"], entry.get("lesson")) == cell:
            for partner in partners:
                if item.room is not None:
                    partner["room"] = entry["room"]
                if "user_id" in item.model_fields_set:
                    partner["user_id"] = entry["user_id"]
            touched = [entry, *partners] if (item.room is not None or "user_id" in item.model_fields_set) else [entry]
        else:
            touched = [entry]
        changes = []
        if (before["weekday"], before["start"], before["end"]) != (entry["weekday"], entry["start"], entry["end"]):
            changes.append(f"{WEEKDAY_SHORT[before['weekday'] - 1]} {before['start']}–{before['end']} → "
                           f"{WEEKDAY_SHORT[entry['weekday'] - 1]} {entry['start']}–{entry['end']}")
        if (before.get("room") or "") != (entry.get("room") or ""):
            changes.append(f"кабинет {before.get('room') or '—'} → {entry.get('room') or '—'}")
        if before.get("user_id") != entry.get("user_id"):
            changes.append(f"учитель {_teacher_name(database, before.get('user_id'))} → "
                           f"{_teacher_name(database, entry.get('user_id'))}")
        if changes:
            _stamp(touched, user)
            _log(database, cls["school_id"], user, f"Занятие {cls['name']}: " + "; ".join(changes))
        return entry

    return {"ok": True, "entry": db.transaction(_apply)}


@router.delete("/{entry_id}")
def delete_lesson(entry_id: int, user: Dict[str, Any] = Depends(require_panel)):
    def _apply(database: Dict[str, Any]):
        entry = _entry_or_404(database, entry_id, user)
        cls = db.find_class(database, entry["class_id"])
        database["schedule"] = [e for e in database["schedule"] if e.get("id") != entry_id]
        _log(database, cls["school_id"], user,
             f"Удалено занятие {cls['name']}: {WEEKDAY_SHORT[entry['weekday'] - 1]} {entry['start']}–{entry['end']}")
        return None

    db.transaction(_apply)
    return {"ok": True}
