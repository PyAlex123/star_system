"""Доменная логика отметок — только чистые функции.

На вход — словари в том виде, в каком они лежат в JSON-базе «Звёзд» и в строках
data/checkin.sqlite, и момент «сейчас»; на выход — решения. Ни Telegram, ни баз,
ни часов здесь нет: сервис (service.py) читает данные, зовёт эти функции
и записывает результат. Поэтому каждое правило проверяется тестом без окружения.

Время уроков — строки ISO в UTC (clock.iso), дата урока — «ГГГГ-ММ-ДД» по часам школ.
"""
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import clock

Lesson = Dict[str, Any]

# ── Статусы урока ─────────────────────────────────────────────────────────────

PLANNED = "planned"            # впереди или идёт, отметки ещё нет
CHECKED_IN = "checked_in"      # отметился вовремя
LATE = "late"                  # отметился с опозданием
NO_CHECKIN = "no_checkin"      # урок начался, отметки нет — ждёт решения
NEEDS_REVIEW = "needs_review"  # отметка спорная (далеко, без видео, после конца урока)
APPROVED = "approved"          # администратор засчитал
REJECTED = "rejected"          # администратор не засчитал
CANCELLED = "cancelled"        # праздник, отмена

STATUSES = (PLANNED, CHECKED_IN, LATE, NO_CHECKIN, NEEDS_REVIEW, APPROVED, REJECTED, CANCELLED)
COUNTED = frozenset({CHECKED_IN, LATE, APPROVED})                    # часы засчитываются
OPEN_FOR_CHECKIN = frozenset({PLANNED, NO_CHECKIN, NEEDS_REVIEW, REJECTED})
AWAITING_DECISION = frozenset({NO_CHECKIN, NEEDS_REVIEW})            # очередь администратора

# Кто отменил урок. Сверка с расписанием трогает только отмены по исключению:
# отмену администратором она не отменит.
BY_EXCEPTION = "exception"
BY_ADMIN = "admin"

# Исключения календаря
HOLIDAY = "holiday"
CANCEL = "cancel"
SUBSTITUTE = "substitute"
EXCEPTION_KINDS = (HOLIDAY, CANCEL, SUBSTITUTE)

# Почему урок на проверке (lesson.flag, через запятую)
FLAG_NO_VIDEO = "no_video"
FLAG_FAR = "far"
FLAG_NO_COORDS = "no_coords"
FLAG_ENDED = "ended"
FLAG_AFTER_REJECT = "after_reject"

# Сверка с картой
GEO_OK = "ok"
GEO_FAR = "far"
GEO_NO_COORDS = "no_coords"

DEFAULT_RADIUS_M = 150
EARTH_RADIUS_M = 6_371_008.8


# ── Мелкие помощники ──────────────────────────────────────────────────────────

def _day(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _start(lesson: Lesson) -> datetime:
    return clock.parse(lesson["starts_at"])


def _end(lesson: Lesson) -> datetime:
    return clock.parse(lesson["ends_at"])


def _id(item: Dict[str, Any]) -> int:
    return int(item.get("id") or 0)


def duration_minutes(lesson: Lesson) -> int:
    return max(0, clock.minutes_between(_start(lesson), _end(lesson)))


# ── Расстояние ────────────────────────────────────────────────────────────────

def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности Земли (формула гаверсинуса), в метрах."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(a)))


def has_coords(school: Optional[Dict[str, Any]]) -> bool:
    return bool(school) and school.get("lat") is not None and school.get("lon") is not None


def geo_verdict(school: Optional[Dict[str, Any]], lat: float, lon: float) -> Tuple[str, Optional[int]]:
    """(ok | far | no_coords, расстояние в метрах или None)."""
    if not has_coords(school):
        return GEO_NO_COORDS, None
    distance = distance_m(lat, lon, float(school["lat"]), float(school["lon"]))
    radius = school.get("radius_m") or DEFAULT_RADIUS_M
    return (GEO_OK if distance <= radius else GEO_FAR), int(round(distance))


# ── Расписание → уроки по датам ───────────────────────────────────────────────

def entry_bounds(entry: Dict[str, Any]) -> Tuple[Optional[date], Optional[date]]:
    """Первый и последний день занятия. valid_from пустое — с того дня, как внесли;
    None — без границы."""
    return (_day(entry.get("valid_from")) or _day(entry.get("created_at")), _day(entry.get("valid_to")))


def entry_active_on(entry: Dict[str, Any], day: date) -> bool:
    """Действует ли занятие в этот день."""
    start, end = entry_bounds(entry)
    return (start is None or start <= day) and (end is None or day <= end)


def ranges_overlap(a: Tuple[Optional[date], Optional[date]], b: Tuple[Optional[date], Optional[date]]) -> bool:
    """Пересекаются ли два срока действия (None — без границы)."""
    a_start, a_end = a
    b_start, b_end = b
    return (a_end is None or b_start is None or b_start <= a_end) and \
           (b_end is None or a_start is None or a_start <= b_end)


def _covers(exception: Dict[str, Any], day: date) -> bool:
    start, end = _day(exception.get("date_from")), _day(exception.get("date_to"))
    return start is not None and end is not None and start <= day <= end


def _matches(exception: Dict[str, Any], entry_id: int, user_ids: Iterable[Optional[int]],
             school_id: int) -> bool:
    """Заданные в исключении фильтры (занятие, преподаватель, школа) совпадают все.
    Пустой фильтр — «любой»: праздник без школы касается всех школ."""
    if exception.get("entry_id") is not None and exception["entry_id"] != entry_id:
        return False
    if exception.get("school_id") is not None and exception["school_id"] != school_id:
        return False
    if exception.get("user_id") is not None and exception["user_id"] not in set(user_ids):
        return False
    return True


def _valid_clock(value: Any) -> bool:
    try:
        datetime.strptime(str(value), "%H:%M")
        return True
    except ValueError:
        return False


def expected_lessons(database: Dict[str, Any], start: date, end: date,
                     exceptions: Iterable[Dict[str, Any]]) -> List[Lesson]:
    """Уроки, которые по расписанию и календарю должны быть с start по end включительно.

    Урок попадает сюда, только если его итоговый преподаватель отмечается в боте
    (checkin_tracked). Отменённые праздником или отменой уроки тоже здесь — со статусом
    cancelled: в «Сегодня» и в отчёте видно, почему урока не было.
    """
    users = {u["id"]: u for u in database.get("users", [])}
    classes = {c["id"]: c for c in database.get("classes", [])}
    schools = {s["id"]: s for s in database.get("schools", [])}
    calendar = sorted(exceptions, key=_id)

    result: List[Lesson] = []
    for entry in database.get("schedule", []):
        cls = classes.get(entry.get("class_id"))
        school = schools.get(cls.get("school_id")) if cls else None
        weekday = entry.get("weekday")
        if not school or not isinstance(weekday, int) or not 1 <= weekday <= 7:
            continue
        if not (_valid_clock(entry.get("start")) and _valid_clock(entry.get("end"))):
            continue
        if entry["end"] <= entry["start"]:
            continue
        day = start + timedelta(days=(weekday - start.isoweekday()) % 7)
        while day <= end:
            if entry_active_on(entry, day):
                lesson = _expected_one(entry, day, cls, school, users, calendar)
                if lesson:
                    result.append(lesson)
            day += timedelta(weeks=1)
    return sorted(_join_classes(result), key=lambda l: (l["starts_at"], l["entry_id"]))


def _join_classes(lessons: List[Lesson]) -> List[Lesson]:
    """Объединённые классы — один урок. В таблице расписания общий урок нескольких классов —
    это занятие у каждого класса с общими учителем и временем; преподаватель ведёт его один раз,
    поэтому и отмечается, и получает часы один раз. Урок держится за занятие с меньшим номером,
    в названии — все классы."""
    groups: Dict[Tuple[Any, ...], List[Lesson]] = {}
    for lesson in lessons:
        key = (lesson["user_id"], lesson["school_id"], lesson["starts_at"], lesson["ends_at"], lesson["status"])
        groups.setdefault(key, []).append(lesson)
    joined: List[Lesson] = []
    for group in groups.values():
        group.sort(key=lambda l: l["entry_id"])
        lesson = dict(group[0])
        if len(group) > 1:
            lesson["class_name"] = ", ".join(l["class_name"] for l in group)
            lesson["room"] = next((l["room"] for l in group if l["room"]), "")
        joined.append(lesson)
    return joined


def _expected_one(entry: Dict[str, Any], day: date, cls: Dict[str, Any], school: Dict[str, Any],
                  users: Dict[int, Dict[str, Any]], calendar: List[Dict[str, Any]]) -> Optional[Lesson]:
    original = entry.get("user_id")
    today_rules = [e for e in calendar if _covers(e, day)]

    teacher_id, original_id, note = original, None, ""
    substitutes = [e for e in today_rules if e.get("kind") == SUBSTITUTE and e.get("substitute_user_id")
                   and _matches(e, entry["id"], [original], school["id"])]
    if substitutes:
        chosen = substitutes[-1]                  # последняя запись — действующая
        if chosen["substitute_user_id"] != original:
            teacher_id, original_id = chosen["substitute_user_id"], original
            note = chosen.get("reason") or ""

    teacher = users.get(teacher_id) if teacher_id is not None else None
    if not teacher or not teacher.get("checkin_tracked"):
        return None

    # Отмена сильнее замены: заболевший заменяющий отменяет и урок, который он заменял
    cancels = [e for e in today_rules if e.get("kind") in (HOLIDAY, CANCEL)
               and _matches(e, entry["id"], [original, teacher_id], school["id"])]
    cancelled = bool(cancels)
    if cancelled:
        note = cancels[-1].get("reason") or ""

    return {
        "entry_id": entry["id"],
        "date": day.isoformat(),
        "user_id": teacher_id,
        "original_user_id": original_id,
        "school_id": school["id"],
        "class_id": cls["id"],
        "teacher_name": teacher.get("name", ""),
        "school_name": school.get("name", ""),
        "class_name": cls.get("name", ""),
        "room": entry.get("room") or "",
        "starts_at": clock.iso(clock.at(day, entry["start"])),
        "ends_at": clock.iso(clock.at(day, entry["end"])),
        "status": CANCELLED if cancelled else PLANNED,
        "cancel_source": BY_EXCEPTION if cancelled else "",
        "note": note,
    }


# Поля урока, которые задаёт календарь (замена, отмена) — меняются до начала урока, даже сегодня
CALENDAR_FIELDS = ("status", "cancel_source", "user_id", "original_user_id", "teacher_name", "note")
# Поля, которые задаёт само занятие — меняются только с завтрашнего дня
SCHEDULE_FIELDS = ("starts_at", "ends_at", "room", "school_id", "class_id", "school_name", "class_name")


def _managed(lesson: Lesson) -> bool:
    """Строка, которую сверка вправе менять: ещё без отметки и без решения человека."""
    if lesson.get("entry_id") is None:
        return False                              # разовый урок ведёт администратор
    return lesson["status"] == PLANNED or (
        lesson["status"] == CANCELLED and lesson.get("cancel_source") == BY_EXCEPTION)


def plan_changes(expected: List[Lesson], existing: List[Lesson], now: datetime,
                 tracked_ids: Set[int]) -> Dict[str, List[Dict[str, Any]]]:
    """Что поменять в planned_lessons, чтобы привести их к расписанию и календарю.

    - начавшиеся уроки, разовые, отмеченные и решённые — не трогаем никогда;
    - замены, отмены и снятые праздники действуют до начала урока, даже сегодня;
    - правка самого занятия (время, кабинет, удалённое занятие) — только с завтрашнего дня:
      сегодняшний урок нельзя «сдвинуть» перед отметкой;
    - снят с отметок или удалён преподаватель — его неначавшиеся уроки уходят;
    - новые уроки появляются, только если ещё не начались.
    """
    today = clock.today(now)
    have_by_key = {(l["entry_id"], l["date"]): l for l in existing if l.get("entry_id") is not None}
    want_by_key = {(l["entry_id"], l["date"]): l for l in expected}

    inserts: List[Dict[str, Any]] = []
    updates: List[Dict[str, Any]] = []
    deletes: List[Dict[str, Any]] = []

    for key, want in want_by_key.items():
        have = have_by_key.get(key)
        if have is None:
            if _start(want) > now:
                inserts.append(dict(want, reason="schedule"))
            continue
        if not _managed(have) or _start(have) <= now:
            continue
        changes = {f: want.get(f) for f in CALENDAR_FIELDS if have.get(f) != want.get(f)}
        schedule_changes = {f: want.get(f) for f in SCHEDULE_FIELDS if have.get(f) != want.get(f)}
        if schedule_changes and _day(have["date"]) > today:
            changes.update(schedule_changes)
        if changes:
            reason = "schedule" if set(changes) & set(SCHEDULE_FIELDS) else "calendar"
            updates.append({"id": have["id"], "set": changes,
                            "before": {f: have.get(f) for f in changes}, "reason": reason})

    for key, have in have_by_key.items():
        if key in want_by_key or not _managed(have) or _start(have) <= now:
            continue
        if have.get("user_id") not in tracked_ids:
            deletes.append({"id": have["id"], "lesson": have, "reason": "untracked"})
        elif _day(have["date"]) > today:
            deletes.append({"id": have["id"], "lesson": have, "reason": "schedule"})
        # Сегодняшний урок исчезнувшего занятия остаётся: решит администратор

    # У преподавателя не бывает двух уроков, начинающихся в одну минуту. Так бывает, когда
    # у объединённого урока сегодня сменился состав классов: сегодняшний урок остался,
    # а новый — лишний.
    gone = {d["id"] for d in deletes}
    busy = {(l.get("user_id"), l.get("starts_at")) for l in existing if l.get("id") not in gone}
    inserts = [i for i in inserts if (i.get("user_id"), i.get("starts_at")) not in busy]
    return {"insert": inserts, "update": updates, "delete": deletes}


# ── Отметка ───────────────────────────────────────────────────────────────────

def blocks(lessons: Iterable[Lesson], merge_gap_min: int) -> List[List[Lesson]]:
    """Уроки одного преподавателя подряд в одной школе с перерывом не больше merge_gap_min.

    Другая школа или перерыв длиннее — новый блок. Дата не важна: урок в 00:30
    и урок в 23:50 накануне — соседи, если между ними 40 минут.
    """
    ordered = sorted(lessons, key=lambda l: (l.get("user_id") or 0, _start(l), _id(l)))
    gap = timedelta(minutes=merge_gap_min)
    groups: List[List[Lesson]] = []
    group_end: Optional[datetime] = None
    for lesson in ordered:
        last = groups[-1][-1] if groups else None
        if (last is not None and last.get("user_id") == lesson.get("user_id")
                and last.get("school_id") == lesson.get("school_id")
                and _start(lesson) - group_end <= gap):
            groups[-1].append(lesson)
            group_end = max(group_end, _end(lesson))
        else:
            groups.append([lesson])
            group_end = _end(lesson)
    return groups


def pick_block(lessons: Iterable[Lesson], at: datetime, lat: float, lon: float,
               schools: Dict[int, Dict[str, Any]], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Какие уроки закрывает геолокация, присланная в момент at.

    Окно блока — от начала первого урока минус checkin_window_before_min до конца
    последнего. Если открыто несколько блоков (две школы), берём школу ближе к точке;
    школы без координат — после школ с координатами, затем — что раньше начинается.

    Результат: {"result": "ok", block, open, school_id, distance_m, geo}
               {"result": "already", block} — в ближайшем блоке всё уже отмечено
               {"result": "no_lesson"}      — подходящего урока нет
    """
    window = timedelta(minutes=settings["checkin_window_before_min"])
    live = [l for l in lessons if l["status"] != CANCELLED]
    candidates = []
    for block in blocks(live, settings["merge_gap_min"]):
        first_start = min(_start(l) for l in block)
        last_end = max(_end(l) for l in block)
        if not first_start - window <= at < last_end:
            continue
        school = schools.get(block[0]["school_id"])
        geo, distance = geo_verdict(school, lat, lon)
        open_lessons = [l for l in block if l["status"] in OPEN_FOR_CHECKIN]
        candidates.append({"block": block, "open": open_lessons, "school_id": block[0]["school_id"],
                           "distance_m": distance, "geo": geo, "_start": first_start})
    if not candidates:
        return {"result": "no_lesson"}

    def order(c: Dict[str, Any]):
        known = c["distance_m"] is not None
        return (not known, c["distance_m"] if known else 0, not c["open"], c["_start"])

    best = min(candidates, key=order)
    best.pop("_start")
    if not best["open"]:
        return {"result": "already", "block": best["block"]}
    return dict(best, result="ok")


def lesson_outcome(lesson: Lesson, at: datetime, geo: str, video_ok: bool,
                   settings: Dict[str, Any]) -> Dict[str, Any]:
    """Статус урока после отметки в момент at (время геолокации).

    Опоздание = время отметки минус начало, не меньше нуля; до grace_minutes — «вовремя».
    Спорная отметка уходит на проверку, опоздание при этом всё равно записывается.
    """
    late = max(0, clock.minutes_between(_start(lesson), at))
    flags = []
    if not video_ok:
        flags.append(FLAG_NO_VIDEO)
    if geo == GEO_FAR:
        flags.append(FLAG_FAR)
    elif geo == GEO_NO_COORDS:
        flags.append(FLAG_NO_COORDS)
    if _end(lesson) <= at:
        flags.append(FLAG_ENDED)
    if lesson["status"] == REJECTED:
        flags.append(FLAG_AFTER_REJECT)
    if flags:
        status = NEEDS_REVIEW
    else:
        status = LATE if late > settings["grace_minutes"] else CHECKED_IN
    return {"status": status, "late_minutes": late, "flag": ",".join(flags)}


def due_for_review(lesson: Lesson, now: datetime, review_after_min: int, pending: bool = False) -> bool:
    """Урок начался review_after_min минут назад, а отметки нет — пора в очередь.
    pending — преподаватель как раз отмечается (ждём кругляшок): не торопимся."""
    if lesson["status"] != PLANNED or pending:
        return False
    return now >= _start(lesson) + timedelta(minutes=review_after_min)


# ── Проверка сообщений Telegram (для бота) ────────────────────────────────────

def location_problem(forwarded: bool, venue: bool, live_period: Optional[int]) -> Optional[str]:
    """Что не так с геолокацией: переслана, выбрана из списка мест или это трансляция."""
    if forwarded:
        return "forwarded"
    if venue:
        return "venue"
    if live_period:
        return "live"
    return None


def video_problem(kind: str, forwarded: bool, received_at: datetime,
                  pending: Optional[Dict[str, Any]], deadline_sec: int, duplicate: bool) -> Optional[str]:
    """Что не так с кругляшком. pending — отметка, ждущая видео (или None)."""
    if pending is None:
        return "no_pending"
    if kind != "video_note":
        return "not_video_note"
    if forwarded:
        return "forwarded"
    if duplicate:
        return "duplicate"
    if (received_at - clock.parse(pending["created_at"])).total_seconds() > deadline_sec:
        return "late"
    return None


# ── Часы и деньги ─────────────────────────────────────────────────────────────

def counted_minutes(lesson: Lesson) -> int:
    """Засчитанные минуты урока. Ручная правка (hours_override) — главнее статуса."""
    if lesson.get("hours_override") is not None:
        return int(round(float(lesson["hours_override"]) * 60))
    return duration_minutes(lesson) if lesson["status"] in COUNTED else 0


def hours(minutes: int) -> float:
    return round(minutes / 60, 2)


def money(minutes: int, rate: Optional[int]) -> Optional[int]:
    """Сумма за минуты по ставке в час, целые сумы. Ставки нет — суммы нет."""
    if rate is None:
        return None
    return int((Decimal(minutes) * Decimal(rate) / Decimal(60)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def report(lessons: Iterable[Lesson], users: Iterable[Dict[str, Any]], start: date, end: date,
           now: datetime, grace_minutes: int) -> Dict[str, Any]:
    """Отчёт за период: по преподавателям (часы и деньги) и по школам (часы для счёта школе).

    Имена — из снимков урока, ставка — текущая у сотрудника.
    """
    people = {u["id"]: u for u in users}
    in_period = [l for l in lessons if (d := _day(l.get("date"))) is not None and start <= d <= end]

    teachers: Dict[Any, Dict[str, Any]] = {}
    schools: Dict[Any, Dict[str, Any]] = {}
    for lesson in sorted(in_period, key=lambda l: (l["starts_at"], _id(l))):
        uid = lesson.get("user_id")
        person = people.get(uid) or {}
        row = teachers.setdefault(uid, {
            "user_id": uid, "name": person.get("name") or lesson.get("teacher_name", ""),
            "rate": person.get("hourly_rate"),
            "planned": 0, "confirmed": 0, "late": 0, "late_minutes": 0, "rejected": 0,
            "pending": 0, "ahead": 0, "cancelled": 0, "minutes": 0,
        })
        status = lesson["status"]
        minutes = counted_minutes(lesson)
        row["minutes"] += minutes
        if status == CANCELLED:
            row["cancelled"] += 1
        else:
            row["planned"] += 1
        if status in COUNTED:
            row["confirmed"] += 1
            late = int(lesson.get("late_minutes") or 0)
            if late > grace_minutes:
                row["late"] += 1
                row["late_minutes"] += late
        elif status == REJECTED:
            row["rejected"] += 1
        elif status in AWAITING_DECISION or (status == PLANNED and _start(lesson) <= now):
            row["pending"] += 1
        elif status == PLANNED:
            row["ahead"] += 1

        if minutes:
            school = schools.setdefault(lesson["school_id"], {
                "school_id": lesson["school_id"], "name": lesson.get("school_name", ""),
                "lessons": 0, "minutes": 0, "teachers": {}})
            school["lessons"] += 1
            school["minutes"] += minutes
            school["teachers"][uid] = school["teachers"].get(uid, 0) + minutes

    teacher_rows = []
    for row in teachers.values():
        row["hours"] = hours(row["minutes"])
        row["amount"] = money(row["minutes"], row["rate"])
        teacher_rows.append(row)
    teacher_rows.sort(key=lambda r: (r["name"] or "").lower())

    school_rows = []
    for row in schools.values():
        row["hours"] = hours(row["minutes"])
        row["teachers"] = sorted(
            ({"user_id": uid, "name": teachers[uid]["name"], "minutes": m, "hours": hours(m)}
             for uid, m in row["teachers"].items()),
            key=lambda t: (t["name"] or "").lower())
        school_rows.append(row)
    school_rows.sort(key=lambda r: (r["name"] or "").lower())

    total_minutes = sum(r["minutes"] for r in teacher_rows)
    amounts = [r["amount"] for r in teacher_rows if r["amount"] is not None]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "teachers": teacher_rows,
        "schools": school_rows,
        "totals": {
            "lessons": sum(r["planned"] for r in teacher_rows),
            "confirmed": sum(r["confirmed"] for r in teacher_rows),
            "late": sum(r["late"] for r in teacher_rows),
            "pending": sum(r["pending"] for r in teacher_rows),
            "minutes": total_minutes,
            "hours": hours(total_minutes),
            "amount": sum(amounts) if amounts else None,
        },
    }


# ── Связь с журналом звёзд ────────────────────────────────────────────────────

def star_days(database: Dict[str, Any]) -> Set[Tuple[int, str]]:
    """(класс, дата), когда в журнале звёзд прошёл урок с начислениями.
    Только подсказка администратору — урок по ней не засчитывается."""
    return {(l.get("class_id"), str(l.get("date", ""))[:10]) for l in database.get("lessons", [])
            if l.get("class_id") is not None and l.get("awards")}
