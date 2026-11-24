"""Сценарии отметок над двумя базами: JSON «Звёзд» (школы, сотрудники, расписание)
и data/checkin.sqlite (уроки по датам, отметки, журналы).

Правило: JSON-база читается копией (db.load_db()) и только потом открывается
транзакция SQLite. Одна блокировка никогда не берётся внутри другой — сайт,
бот и планировщик работают из разных потоков.

Решения принимает domain.py; здесь — чтение, запись и журнал. Тексты ошибок
(CheckinError) показываются человеку как есть.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from .. import config, db
from . import clock, domain as d, store

log = logging.getLogger("stars.checkin")

EVENTS_KEEP_DAYS = 180       # журнал шагов бота дольше не нужен; решения — в audit_log навсегда
DAILY_JOBS_HOUR = 3          # снимок базы для бэкапа и чистка — после 03:00 по Ташкенту
QUEUE_LIMIT = 500


class CheckinError(ValueError):
    """Ошибка, понятная человеку: показывается в панели или в боте как есть."""


class NotFound(CheckinError):
    pass


STATUS_LABELS = {
    d.PLANNED: "впереди", d.CHECKED_IN: "отметился", d.LATE: "опоздал", d.NO_CHECKIN: "нет отметки",
    d.NEEDS_REVIEW: "на проверке", d.APPROVED: "засчитан", d.REJECTED: "не засчитан", d.CANCELLED: "отменён",
}
FLAG_LABELS = {
    d.FLAG_NO_VIDEO: "без кругляшка", d.FLAG_FAR: "далеко от школы", d.FLAG_NO_COORDS: "у школы нет координат",
    d.FLAG_ENDED: "отметка после конца урока", d.FLAG_AFTER_REJECT: "отметка после «Не засчитывать»",
}

# Поля урока в журнале правок — было/стало. Белый список: ничего лишнего в историю
LESSON_AUDIT_FIELDS = ("status", "user_id", "late_minutes", "hours_override", "flag", "cancel_source",
                       "note", "checkin_id")
INSERT_FIELDS = ("entry_id", "date", "user_id", "original_user_id", "school_id", "class_id", "teacher_name",
                 "school_name", "class_name", "room", "starts_at", "ends_at", "status", "cancel_source", "note")
RECONCILE_FIELDS = frozenset(d.CALENDAR_FIELDS + d.SCHEDULE_FIELDS)


def _snap(lesson: Dict[str, Any]) -> Dict[str, Any]:
    return {f: lesson.get(f) for f in LESSON_AUDIT_FIELDS}


def _need_reason(reason: Optional[str]) -> str:
    text = (reason or "").strip()
    if not text:
        raise CheckinError("Укажите причину — она попадёт в историю правок")
    return text


def tracked_ids(database: Dict[str, Any]) -> set:
    return {u["id"] for u in database.get("users", []) if u.get("checkin_tracked")}


def _lesson(conn, lesson_id: int) -> Dict[str, Any]:
    found = store.row(conn.execute("SELECT * FROM planned_lessons WHERE id = ?", (lesson_id,)).fetchone())
    if not found:
        raise NotFound("Урок не найден")
    return found


def _update_lesson(conn, lesson_id: int, fields: Dict[str, Any]) -> None:
    fields = dict(fields, updated_at=store.now())
    columns = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE planned_lessons SET {columns} WHERE id = ?", (*fields.values(), lesson_id))


def _enqueue(conn, lesson_id: int, kind: str) -> None:
    """В очередь исходящих: карточка в чат «Отметки» или напоминание преподавателю.
    Повтор той же пары (урок, вид) молча игнорируется — это и есть защита от дублей."""
    conn.execute("INSERT OR IGNORE INTO notifications (lesson_id, kind, status, created_at) "
                 "VALUES (?, ?, 'pending', ?)", (lesson_id, kind, store.now()))


# ── Раскладка расписания по датам ─────────────────────────────────────────────

def reconcile(now: Optional[datetime] = None, database: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Привести уроки на горизонт вперёд к расписанию и календарю (domain.plan_changes)."""
    now = now or clock.now_utc()
    database = database if database is not None else db.load_db()
    tracked = tracked_ids(database)
    with store.tx() as conn:
        settings = store.get_settings(conn)
        start = clock.today(now)
        end = start + timedelta(days=settings["lesson_horizon_days"] - 1)
        exceptions = store.rows(conn.execute(
            "SELECT * FROM exceptions WHERE date_to >= ? AND date_from <= ?", (start.isoformat(), end.isoformat())))
        expected = d.expected_lessons(database, start, end, exceptions)
        existing = store.rows(conn.execute(
            "SELECT * FROM planned_lessons WHERE date >= ? AND entry_id IS NOT NULL", (start.isoformat(),)))
        changes = d.plan_changes(expected, existing, now, tracked)
        stamp = store.now()
        for item in changes["insert"]:
            values = [item.get(f) for f in INSERT_FIELDS]
            conn.execute(f"INSERT INTO planned_lessons ({', '.join(INSERT_FIELDS)}, created_at, updated_at) "
                         f"VALUES ({', '.join('?' * len(INSERT_FIELDS))}, ?, ?)", (*values, stamp, stamp))
            store.log_event(conn, "reconcile_insert", item.get("user_id"),
                            payload={"entry_id": item["entry_id"], "date": item["date"], "status": item["status"]})
        for item in changes["update"]:
            fields = {k: v for k, v in item["set"].items() if k in RECONCILE_FIELDS}
            _update_lesson(conn, item["id"], fields)
            store.log_event(conn, "reconcile_update", payload={"lesson_id": item["id"], "reason": item["reason"],
                                                               "before": item["before"], "set": fields})
        for item in changes["delete"]:
            conn.execute("DELETE FROM planned_lessons WHERE id = ?", (item["id"],))
            lesson = item["lesson"]
            store.log_event(conn, "reconcile_delete", lesson.get("user_id"),
                            payload={"lesson_id": item["id"], "reason": item["reason"],
                                     "entry_id": lesson.get("entry_id"), "date": lesson.get("date")})
    return {k: len(v) for k, v in changes.items()}


# ── Отметка: геолокация → кругляшок ───────────────────────────────────────────

def _lessons_near(conn, user_id: int, at: datetime) -> List[Dict[str, Any]]:
    """Уроки преподавателя вокруг момента отметки: сутки назад и сутки вперёд —
    чтобы в блок попали и уроки после полуночи, и поздние уроки длинного блока."""
    low, high = clock.iso(at - timedelta(days=1)), clock.iso(at + timedelta(days=1))
    return store.rows(conn.execute(
        "SELECT * FROM planned_lessons WHERE user_id = ? AND starts_at <= ? AND ends_at > ? ORDER BY starts_at",
        (user_id, high, low)))


def _summary(lesson: Dict[str, Any]) -> Dict[str, Any]:
    start, end = clock.parse(lesson["starts_at"]), clock.parse(lesson["ends_at"])
    return {"id": lesson["id"], "date": lesson["date"], "start": clock.hhmm(start), "end": clock.hhmm(end),
            "school": lesson.get("school_name", ""), "class": lesson.get("class_name", ""),
            "status": lesson["status"]}


def start_checkin(user_id: int, lat: float, lon: float, at: datetime, accuracy: Optional[float] = None,
                  live_period: Optional[int] = None, venue: bool = False, forwarded: bool = False,
                  message_id: Optional[int] = None) -> Dict[str, Any]:
    """Пришла геолокация (at — время сообщения в Telegram, а не сервера).

    Итог: not_tracked | problem | no_lesson | already | await_video.
    """
    database = db.load_db()
    user = db.find_user(database, user_id)
    if not user or not user.get("checkin_tracked"):
        return {"result": "not_tracked"}
    problem = d.location_problem(forwarded, venue, live_period)
    if problem:
        with store.tx() as conn:
            store.log_event(conn, "location_rejected", user_id, user.get("tg_id"), {"problem": problem})
        return {"result": "problem", "problem": problem}

    reconcile(database=database)
    schools = {s["id"]: s for s in database.get("schools", [])}
    with store.tx() as conn:
        settings = store.get_settings(conn)
        stamp = store.now()
        # Новая точка заменяет прежнюю, если кругляшок ещё не пришёл
        conn.execute("UPDATE checkins SET status = 'replaced', updated_at = ? "
                     "WHERE user_id = ? AND status = 'await_video'", (stamp, user_id))
        picked = d.pick_block(_lessons_near(conn, user_id, at), at, lat, lon, schools, settings)
        base = {"user_id": user_id, "created_at": clock.iso(at), "lat": lat, "lon": lon, "accuracy_m": accuracy,
                "tg_message_id": message_id, "updated_at": stamp}

        if picked["result"] == "no_lesson":
            checkin_id = _insert_checkin(conn, dict(base, status="no_lesson"))
            store.log_event(conn, "no_lesson", user_id, user.get("tg_id"), {"checkin_id": checkin_id})
            return {"result": "no_lesson", "checkin_id": checkin_id}
        if picked["result"] == "already":
            store.log_event(conn, "already", user_id, user.get("tg_id"),
                            {"lessons": [l["id"] for l in picked["block"]]})
            return {"result": "already", "lessons": [_summary(l) for l in picked["block"]]}

        open_ids = [l["id"] for l in picked["open"]]
        checkin_id = _insert_checkin(conn, dict(
            base, status="await_video", school_id=picked["school_id"], distance_m=picked["distance_m"],
            geo=picked["geo"], lesson_ids=store.dumps(open_ids)))
        store.log_event(conn, "location_accepted", user_id, user.get("tg_id"),
                        {"checkin_id": checkin_id, "geo": picked["geo"], "distance_m": picked["distance_m"],
                         "lessons": open_ids})
        school = schools.get(picked["school_id"]) or {}
        return {
            "result": "await_video", "checkin_id": checkin_id, "geo": picked["geo"],
            "distance_m": picked["distance_m"], "school": school.get("name", ""),
            "lessons": [_summary(l) for l in picked["open"]],
            "deadline": clock.iso(at + timedelta(seconds=settings["video_note_deadline_sec"])),
            "deadline_sec": settings["video_note_deadline_sec"],
        }


def _insert_checkin(conn, fields: Dict[str, Any]) -> int:
    columns = ", ".join(fields)
    cursor = conn.execute(f"INSERT INTO checkins ({columns}) VALUES ({', '.join('?' * len(fields))})",
                          tuple(fields.values()))
    return cursor.lastrowid


def _close_checkin(conn, checkin: Dict[str, Any], video_ok: bool, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Статусы уроков блока по итогу отметки. Урок, который за это время решил
    администратор (или он уже отмечен), не трогаем."""
    at = clock.parse(checkin["created_at"])
    results = []
    for lesson_id in store.loads(checkin["lesson_ids"]) or []:
        found = store.row(conn.execute("SELECT * FROM planned_lessons WHERE id = ?", (lesson_id,)).fetchone())
        if not found or found["status"] not in d.OPEN_FOR_CHECKIN:
            continue
        outcome = d.lesson_outcome(found, at, checkin["geo"], video_ok, settings)
        _update_lesson(conn, lesson_id, dict(outcome, checkin_id=checkin["id"]))
        if outcome["status"] == d.NEEDS_REVIEW:
            _enqueue(conn, lesson_id, "review")
        results.append(dict(_summary(found), **outcome))
    return results


def _pending(conn, user_id: int) -> Optional[Dict[str, Any]]:
    return store.row(conn.execute("SELECT * FROM checkins WHERE user_id = ? AND status = 'await_video' "
                                  "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone())


def finish_checkin(user_id: int, kind: str, forwarded: bool, file_id: Optional[str], unique_id: Optional[str],
                   received_at: datetime, message_id: Optional[int] = None) -> Dict[str, Any]:
    """Пришёл кругляшок (или что-то вместо него). Итог: problem | accepted."""
    with store.tx() as conn:
        settings = store.get_settings(conn)
        pending = _pending(conn, user_id)
        duplicate = bool(unique_id) and conn.execute(
            "SELECT 1 FROM checkins WHERE video_unique_id = ?", (unique_id,)).fetchone() is not None
        problem = d.video_problem(kind, forwarded, received_at, pending, settings["video_note_deadline_sec"], duplicate)
        if problem == "late":
            conn.execute("UPDATE checkins SET status = 'no_video', updated_at = ? WHERE id = ?",
                         (store.now(), pending["id"]))
            lessons = _close_checkin(conn, pending, False, settings)
            store.log_event(conn, "video_late", user_id, payload={"checkin_id": pending["id"]})
            return {"result": "problem", "problem": "late", "checkin_id": pending["id"], "lessons": lessons}
        if problem:
            store.log_event(conn, "video_rejected", user_id,
                            payload={"problem": problem, "checkin_id": (pending or {}).get("id")})
            return {"result": "problem", "problem": problem}

        conn.execute("UPDATE checkins SET status = 'accepted', video_file_id = ?, video_unique_id = ?, "
                     "video_received_at = ?, updated_at = ? WHERE id = ?",
                     (file_id, unique_id, clock.iso(received_at), store.now(), pending["id"]))
        lessons = _close_checkin(conn, pending, True, settings)
        store.log_event(conn, "video_accepted", user_id,
                        payload={"checkin_id": pending["id"], "message_id": message_id})
        return {"result": "accepted", "checkin_id": pending["id"], "geo": pending["geo"],
                "distance_m": pending["distance_m"], "lessons": lessons}


# ── Планировщик ───────────────────────────────────────────────────────────────

def expire_videos(now: Optional[datetime] = None) -> int:
    """Кругляшок не пришёл в срок — отметка без видео, уроки на проверку."""
    now = now or clock.now_utc()
    count = 0
    with store.tx() as conn:
        settings = store.get_settings(conn)
        limit = timedelta(seconds=settings["video_note_deadline_sec"])
        for checkin in store.rows(conn.execute("SELECT * FROM checkins WHERE status = 'await_video'")):
            if now - clock.parse(checkin["created_at"]) <= limit:
                continue
            conn.execute("UPDATE checkins SET status = 'no_video', updated_at = ? WHERE id = ?",
                         (store.now(), checkin["id"]))
            _close_checkin(conn, checkin, False, settings)
            store.log_event(conn, "video_expired", checkin["user_id"], payload={"checkin_id": checkin["id"]})
            count += 1
    return count


def mark_no_checkin(now: Optional[datetime] = None) -> int:
    """Урок идёт review_after_min минут, а отметки нет — в очередь администратору."""
    now = now or clock.now_utc()
    count = 0
    with store.tx() as conn:
        settings = store.get_settings(conn)
        waiting = set()
        for checkin in store.rows(conn.execute("SELECT lesson_ids FROM checkins WHERE status = 'await_video'")):
            waiting.update(store.loads(checkin["lesson_ids"]) or [])
        border = clock.iso(now - timedelta(minutes=settings["review_after_min"]))
        for lesson in store.rows(conn.execute(
                "SELECT * FROM planned_lessons WHERE status = 'planned' AND starts_at <= ?", (border,))):
            if not d.due_for_review(lesson, now, settings["review_after_min"], pending=lesson["id"] in waiting):
                continue
            _update_lesson(conn, lesson["id"], {"status": d.NO_CHECKIN})
            _enqueue(conn, lesson["id"], "review")
            if settings["notify_teacher_late"]:
                _enqueue(conn, lesson["id"], "late_teacher")
            count += 1
    return count


def _daily_jobs(now: datetime) -> bool:
    """Раз в сутки после 03:00: снимок базы для бэкапа и чистка старого журнала шагов."""
    local = clock.local(now)
    today = local.date().isoformat()
    if local.hour < DAILY_JOBS_HOUR:
        return False
    with store.tx() as conn:
        if store.job_value(conn, "daily") == today:
            return False
        border = clock.iso(now - timedelta(days=EVENTS_KEEP_DAYS))
        conn.execute("DELETE FROM events WHERE created_at < ?", (border,))
        store.set_job_value(conn, "daily", today)
    store.snapshot(config.CHECKIN_BACKUP_FILE)
    return True


def tick(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Один шаг планировщика. Статусы меняются и без бота; отправка — отдельно (очередь)."""
    now = now or clock.now_utc()
    database = db.load_db()
    return {
        "reconcile": reconcile(now, database),
        "expired": expire_videos(now),
        "no_checkin": mark_no_checkin(now),
        "daily": _daily_jobs(now),
    }


# ── Решения администратора ────────────────────────────────────────────────────

ACTIONS = ("approve", "reject", "cancel", "reopen")


def _target(lesson: Dict[str, Any], action: str, now: datetime) -> Optional[Dict[str, Any]]:
    """Что станет с уроком, или CheckinError — почему нельзя. None — уже так."""
    status = lesson["status"]
    started = clock.parse(lesson["starts_at"]) <= now
    title = f"{lesson.get('teacher_name', '')}, {_summary(lesson)['date']} {_summary(lesson)['start']}"
    if action in ("approve", "reject"):
        if status == d.CANCELLED:
            raise CheckinError(f"{title}: урок отменён — сначала верните его")
        if not started:
            raise CheckinError(f"{title}: урок ещё не начался")
        target = d.APPROVED if action == "approve" else d.REJECTED
        return None if status == target else {"status": target}
    if action == "cancel":
        if status == d.CANCELLED:
            return None
        return {"status": d.CANCELLED, "cancel_source": d.BY_ADMIN}
    # reopen
    if status != d.CANCELLED:
        return None
    if lesson.get("cancel_source") != d.BY_ADMIN:
        raise CheckinError(f"{title}: урок отменён праздником или отменой — уберите их в календаре")
    return {"status": d.NO_CHECKIN if started else d.PLANNED, "cancel_source": ""}


def decide(user: Dict[str, Any], lesson_ids: Iterable[int], action: str, reason: str,
           now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Засчитать / не засчитывать / отменить / вернуть — один урок или пачку.
    Пачка проходит целиком или никак: сначала проверяем все уроки, потом пишем."""
    if action not in ACTIONS:
        raise CheckinError("Неизвестное действие")
    reason = _need_reason(reason)
    now = now or clock.now_utc()
    ids = list(dict.fromkeys(lesson_ids))
    with store.tx() as conn:
        lessons = [_lesson(conn, i) for i in ids]
        plan = [(lesson, _target(lesson, action, now)) for lesson in lessons]
        stamp = store.now()
        result = []
        for lesson, change in plan:
            if change is None:
                result.append(lesson)
                continue
            fields = dict(change, reviewed_by=user.get("id"), reviewed_at=stamp)
            if action == "cancel":
                fields["note"] = reason
            _update_lesson(conn, lesson["id"], fields)
            if change["status"] == d.NO_CHECKIN:
                _enqueue(conn, lesson["id"], "review")
            after = dict(lesson, **fields)
            store.audit(conn, user, "lesson", lesson["id"], action, _snap(lesson), _snap(after), reason)
            result.append(after)
    return result


def set_hours(user: Dict[str, Any], lesson_id: int, hours: Optional[float], reason: str) -> Dict[str, Any]:
    """Ручные часы урока (главнее статуса). None — вернуть часы по расписанию."""
    reason = _need_reason(reason)
    if hours is not None:
        if not 0 <= hours <= 12 or round(hours * 4) != hours * 4:
            raise CheckinError("Часы — от 0 до 12, шагом в четверть часа")
    with store.tx() as conn:
        lesson = _lesson(conn, lesson_id)
        if lesson.get("hours_override") == hours:
            return lesson
        _update_lesson(conn, lesson_id, {"hours_override": hours, "reviewed_by": user.get("id"),
                                         "reviewed_at": store.now()})
        after = dict(lesson, hours_override=hours)
        store.audit(conn, user, "lesson", lesson_id, "hours", _snap(lesson), _snap(after), reason)
        return after


def add_lesson(user: Dict[str, Any], teacher_id: int, class_id: int, day: str, start: str, end: str,
               room: str, reason: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Разовый урок вне расписания: перенос, дополнительное занятие. Сверка его не трогает."""
    reason = _need_reason(reason)
    if end <= start:
        raise CheckinError("Урок должен заканчиваться позже, чем начинается")
    now = now or clock.now_utc()
    database = db.load_db()
    teacher = db.find_user(database, teacher_id)
    if not teacher:
        raise NotFound("Сотрудник не найден")
    if not teacher.get("checkin_tracked"):
        raise CheckinError("Сотрудник не отмечается в боте — сначала включите ему отметки")
    cls = db.find_class(database, class_id)
    school = db.find_school(database, cls["school_id"]) if cls else None
    if not cls or not school:
        raise NotFound("Класс не найден")
    when = date.fromisoformat(day)
    starts_at, ends_at = clock.at(when, start), clock.at(when, end)
    lesson = {
        "entry_id": None, "date": when.isoformat(), "user_id": teacher_id, "original_user_id": None,
        "school_id": school["id"], "class_id": cls["id"], "teacher_name": teacher.get("name", ""),
        "school_name": school.get("name", ""), "class_name": cls.get("name", ""), "room": (room or "").strip(),
        "starts_at": clock.iso(starts_at), "ends_at": clock.iso(ends_at),
        "status": d.PLANNED if starts_at > now else d.NO_CHECKIN, "cancel_source": "", "note": reason,
    }
    with store.tx() as conn:
        stamp = store.now()
        cursor = conn.execute(f"INSERT INTO planned_lessons ({', '.join(INSERT_FIELDS)}, created_at, updated_at) "
                              f"VALUES ({', '.join('?' * len(INSERT_FIELDS))}, ?, ?)",
                              (*[lesson[f] for f in INSERT_FIELDS], stamp, stamp))
        lesson["id"] = cursor.lastrowid
        if lesson["status"] == d.NO_CHECKIN:
            _enqueue(conn, lesson["id"], "review")
        store.audit(conn, user, "lesson", lesson["id"], "create", None, _snap(lesson), reason)
    return lesson


# ── Календарь: праздники, отмены, замены ──────────────────────────────────────

MAX_EXCEPTION_DAYS = 366


def add_exception(user: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    kind = data.get("kind")
    if kind not in d.EXCEPTION_KINDS:
        raise CheckinError("Неизвестный вид исключения")
    reason = _need_reason(data.get("reason"))
    first = date.fromisoformat(data["date_from"])
    last = date.fromisoformat(data.get("date_to") or data["date_from"])
    if last < first:
        raise CheckinError("Дата «по» раньше даты «с»")
    if (last - first).days >= MAX_EXCEPTION_DAYS:
        raise CheckinError("Слишком длинный период — не больше года")

    database = db.load_db()
    school_id, entry_id = data.get("school_id"), data.get("entry_id")
    user_id, substitute = data.get("user_id"), data.get("substitute_user_id")
    if school_id is not None and not db.find_school(database, school_id):
        raise NotFound("Школа не найдена")
    if entry_id is not None and not any(e.get("id") == entry_id for e in database.get("schedule", [])):
        raise NotFound("Занятие не найдено")
    for uid in (user_id, substitute):
        if uid is not None and not db.find_user(database, uid):
            raise NotFound("Сотрудник не найден")

    if kind == d.HOLIDAY and (entry_id or user_id or substitute):
        raise CheckinError("Праздник — для всех школ или одной школы")
    if kind == d.CANCEL:
        if substitute is not None:
            raise CheckinError("Для замены выберите вид «Замена»")
        if entry_id is None and user_id is None and school_id is None:
            raise CheckinError("Выберите, что отменить: занятие, преподавателя или школу")
    if kind == d.SUBSTITUTE:
        if substitute is None:
            raise CheckinError("Выберите, кто заменяет")
        if entry_id is None and user_id is None:
            raise CheckinError("Выберите занятие или преподавателя, которого заменяют")
        if substitute == user_id:
            raise CheckinError("Преподаватель не может заменять сам себя")
        if not db.find_user(database, substitute).get("checkin_tracked"):
            raise CheckinError("Заменяющий не отмечается в боте — сначала включите ему отметки")

    row = {"kind": kind, "date_from": first.isoformat(), "date_to": last.isoformat(), "school_id": school_id,
           "entry_id": entry_id, "user_id": user_id,
           "substitute_user_id": substitute if kind == d.SUBSTITUTE else None,
           "reason": reason, "created_by": user.get("id"), "created_at": store.now()}
    with store.tx() as conn:
        cursor = conn.execute(f"INSERT INTO exceptions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                              tuple(row.values()))
        row["id"] = cursor.lastrowid
        store.audit(conn, user, "exception", row["id"], "create", None, row, reason)
    reconcile(database=database)
    return row


def delete_exception(user: Dict[str, Any], exception_id: int, reason: str = "") -> None:
    with store.tx() as conn:
        found = store.row(conn.execute("SELECT * FROM exceptions WHERE id = ?", (exception_id,)).fetchone())
        if not found:
            raise NotFound("Исключение не найдено")
        conn.execute("DELETE FROM exceptions WHERE id = ?", (exception_id,))
        store.audit(conn, user, "exception", exception_id, "delete", found, None, (reason or "").strip())
    reconcile()


def list_exceptions(start: Optional[date] = None, end: Optional[date] = None) -> List[Dict[str, Any]]:
    database = db.load_db()
    names = {u["id"]: u.get("name", "") for u in database.get("users", [])}
    schools = {s["id"]: s.get("name", "") for s in database.get("schools", [])}
    entries = {e["id"]: e for e in database.get("schedule", [])}
    query, args = "SELECT * FROM exceptions", []
    if start and end:
        query += " WHERE date_to >= ? AND date_from <= ?"
        args = [start.isoformat(), end.isoformat()]
    with store.tx() as conn:
        rows = store.rows(conn.execute(query + " ORDER BY date_from DESC, id DESC", args))
    for row in rows:
        entry = entries.get(row.get("entry_id"))
        row["school"] = schools.get(row.get("school_id"), "")
        row["teacher"] = names.get(row.get("user_id"), "")
        row["substitute"] = names.get(row.get("substitute_user_id"), "")
        row["entry"] = (f"{db.class_name(database, entry['class_id'])}, {entry['start']}–{entry['end']}"
                        if entry else "")
        row["author"] = names.get(row.get("created_by"), "")
    return rows


# ── Просмотр для панели ───────────────────────────────────────────────────────

def _state(lesson: Dict[str, Any], now: datetime) -> str:
    """Цвет строки в «Сегодня»: пришёл / опоздал / нет отметки / на проверке / идёт / впереди / отменён."""
    status = lesson["status"]
    if status in (d.CHECKED_IN, d.APPROVED):
        return "ok"
    if status == d.LATE:
        return "late"
    if status in (d.NO_CHECKIN, d.REJECTED):
        return "missing"
    if status == d.NEEDS_REVIEW:
        return "review"
    if status == d.CANCELLED:
        return "cancelled"
    return "now" if clock.parse(lesson["starts_at"]) <= now else "ahead"


def present(lesson: Dict[str, Any], checkin: Optional[Dict[str, Any]], names: Dict[int, str],
            star_days: set, now: datetime) -> Dict[str, Any]:
    """Урок для панели и Excel: время по Ташкенту строками, подписи по-русски."""
    flags = [f for f in (lesson.get("flag") or "").split(",") if f]
    minutes = d.counted_minutes(lesson)
    item = dict(_summary(lesson))
    item.update({
        "status_label": STATUS_LABELS.get(lesson["status"], lesson["status"]),
        "state": _state(lesson, now),
        "user_id": lesson.get("user_id"), "teacher": lesson.get("teacher_name", ""),
        "original_teacher": names.get(lesson.get("original_user_id"), "") if lesson.get("original_user_id") else "",
        "school_id": lesson.get("school_id"), "class_id": lesson.get("class_id"), "room": lesson.get("room", ""),
        "entry_id": lesson.get("entry_id"), "one_off": lesson.get("entry_id") is None,
        "late_minutes": lesson.get("late_minutes"),
        "flags": flags, "flag_labels": [FLAG_LABELS.get(f, f) for f in flags],
        "note": lesson.get("note", ""), "cancel_source": lesson.get("cancel_source", ""),
        "minutes": minutes, "hours": d.hours(minutes), "hours_override": lesson.get("hours_override"),
        "duration_minutes": d.duration_minutes(lesson),
        "stars_given": (lesson.get("class_id"), lesson["date"]) in star_days,
        "reviewed_by": names.get(lesson.get("reviewed_by"), "") if lesson.get("reviewed_by") else "",
        "reviewed_at": lesson.get("reviewed_at"),
        "checkin": None,
    })
    if checkin:
        item["checkin"] = {
            "id": checkin["id"], "at": clock.hhmm(clock.parse(checkin["created_at"])),
            "created_at": checkin["created_at"], "status": checkin["status"], "geo": checkin.get("geo"),
            "distance_m": checkin.get("distance_m"), "video": bool(checkin.get("video_file_id")),
        }
    return item


def _present_many(conn, lessons: List[Dict[str, Any]], database: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    names = {u["id"]: u.get("name", "") for u in database.get("users", [])}
    stars = d.star_days(database)
    ids = sorted({l["checkin_id"] for l in lessons if l.get("checkin_id")})
    checkins = {}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        for row in conn.execute(f"SELECT * FROM checkins WHERE id IN ({', '.join('?' * len(part))})", part):
            checkins[row["id"]] = dict(row)
    return [present(l, checkins.get(l.get("checkin_id")), names, stars, now) for l in lessons]


def day_view(day: date, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or clock.now_utc()
    database = db.load_db()
    reconcile(now, database)
    with store.tx() as conn:
        lessons = store.rows(conn.execute("SELECT * FROM planned_lessons WHERE date = ? ORDER BY starts_at, id",
                                          (day.isoformat(),)))
        items = _present_many(conn, lessons, database, now)
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    return {"date": day.isoformat(), "today": clock.today(now).isoformat(), "now": clock.hhmm(now),
            "lessons": items, "counts": counts}


def queue(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Ждут решения: нет отметки и спорные — от старых к новым."""
    now = now or clock.now_utc()
    database = db.load_db()
    with store.tx() as conn:
        lessons = store.rows(conn.execute(
            "SELECT * FROM planned_lessons WHERE status IN (?, ?) ORDER BY starts_at, id LIMIT ?",
            (d.NO_CHECKIN, d.NEEDS_REVIEW, QUEUE_LIMIT)))
        return _present_many(conn, lessons, database, now)


def _audit_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for row in rows:
        row["before"] = store.loads(row.get("before"))
        row["after"] = store.loads(row.get("after"))
    return rows


def lesson_detail(lesson_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or clock.now_utc()
    database = db.load_db()
    with store.tx() as conn:
        lesson = _lesson(conn, lesson_id)
        item = _present_many(conn, [lesson], database, now)[0]
        if lesson.get("checkin_id"):
            checkin = store.row(conn.execute("SELECT * FROM checkins WHERE id = ?", (lesson["checkin_id"],)).fetchone())
            if checkin:
                item["checkin"].update({"lat": checkin["lat"], "lon": checkin["lon"],
                                        "accuracy_m": checkin["accuracy_m"],
                                        "video_received_at": checkin["video_received_at"],
                                        "report_chat_id": checkin["report_chat_id"],
                                        "report_message_id": checkin["report_message_id"]})
        item["history"] = _audit_rows(store.rows(conn.execute(
            "SELECT * FROM audit_log WHERE entity = 'lesson' AND entity_id = ? ORDER BY id", (lesson_id,))))
    return item


def audit_page(before_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    with store.tx() as conn:
        if before_id:
            rows = conn.execute("SELECT * FROM audit_log WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit))
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return _audit_rows(store.rows(rows))


def _invite_state(user: Dict[str, Any], now: datetime) -> str:
    if user.get("tg_id"):
        return "bound"
    if not user.get("invite_code"):
        return "none"
    created = clock.parse(user.get("invite_created_at"))
    if created is None or now - created > timedelta(days=config.CHECKIN_INVITE_DAYS):
        return "expired"
    return "pending"


def teachers(database: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Сотрудники для раздела «Отметки» — белый список полей: без паролей и кода приглашения."""
    database = database if database is not None else db.load_db()
    now = now or clock.now_utc()
    rows = []
    for user in database.get("users", []):
        rows.append({
            "id": user["id"], "name": user.get("name", ""), "role": user.get("role"),
            "school_ids": list(user.get("school_ids") or []),
            "checkin_tracked": bool(user.get("checkin_tracked")),
            "checkin_manager": bool(user.get("checkin_manager")),
            "hourly_rate": user.get("hourly_rate"), "lang": user.get("lang") or "ru",
            "tg_bound": bool(user.get("tg_id")), "tg_username": user.get("tg_username"),
            "tg_blocked": bool(user.get("tg_blocked_at")), "consent": bool(user.get("consent_at")),
            "invite": _invite_state(user, now),
        })
    return sorted(rows, key=lambda r: (r["name"] or "").lower())


def readiness(database: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Что мешает отметкам работать: показываем администратору, а не молчим."""
    tracked = tracked_ids(database)
    classes = {c["id"]: c for c in database.get("classes", [])}
    users = {u["id"]: u for u in database.get("users", [])}
    today = clock.today(now)
    active = [e for e in database.get("schedule", []) if d.entry_active_on(e, today) or
              (d.entry_bounds(e)[0] or today) > today]
    # Школы, где кто-то отмечается: по его занятиям и по школам в карточке сотрудника
    tracked_schools = {classes[e["class_id"]]["school_id"] for e in active
                       if e.get("user_id") in tracked and e.get("class_id") in classes}
    for uid in tracked:
        tracked_schools.update((users.get(uid) or {}).get("school_ids") or [])
    with_lessons = {e.get("user_id") for e in active}
    schools = [s for s in database.get("schools", []) if s["id"] in tracked_schools and not d.has_coords(s)]
    without_teacher = [e for e in active if e.get("class_id") in classes
                       and classes[e["class_id"]]["school_id"] in tracked_schools
                       and e.get("user_id") not in tracked]
    staff = [users[i] for i in tracked if i in users]
    return {
        "schools_without_coords": [{"id": s["id"], "name": s.get("name", "")} for s in schools],
        "tracked_without_telegram": [{"id": u["id"], "name": u.get("name", ""), "invite": _invite_state(u, now)}
                                     for u in staff if not u.get("tg_id")],
        "blocked_bot": [{"id": u["id"], "name": u.get("name", "")} for u in staff if u.get("tg_blocked_at")],
        "tracked_without_lessons": [{"id": u["id"], "name": u.get("name", "")} for u in staff
                                    if u["id"] not in with_lessons],
        "entries_without_tracked_teacher": [
            {"id": e["id"], "class": classes[e["class_id"]].get("name", ""), "weekday": e.get("weekday"),
             "start": e.get("start"), "teacher": (users.get(e.get("user_id")) or {}).get("name", "")}
            for e in without_teacher],
    }


def settings_view() -> Dict[str, Any]:
    values = store.get_settings()
    return {"values": {k: v for k, v in values.items() if not store.SETTINGS[k].get("hidden")},
            "fields": [{"key": k, "label": spec["label"], "description": spec["description"],
                        "type": "bool" if spec["type"] is bool else "int",
                        "min": spec.get("min"), "max": spec.get("max")}
                       for k, spec in store.SETTINGS.items() if not spec.get("hidden")]}


def update_settings(user: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    clean = {}
    for key, value in values.items():
        if key not in store.SETTINGS or store.SETTINGS[key].get("hidden"):
            raise CheckinError("Такой настройки нет")
        try:
            clean[key] = store.clean_setting(key, value)
        except ValueError as exc:
            raise CheckinError(str(exc))
    with store.tx() as conn:
        before = store.get_settings(conn)
        changed = {k: v for k, v in clean.items() if before.get(k) != v}
        for key, value in changed.items():
            store.set_setting(conn, key, value)
        if changed:
            store.audit(conn, user, "settings", None, "update", {k: before[k] for k in changed}, changed)
    if "lesson_horizon_days" in changed:
        reconcile()
    return settings_view()


def overview(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or clock.now_utc()
    database = db.load_db()
    with store.tx() as conn:
        settings = store.get_settings(conn)
        waiting = conn.execute("SELECT COUNT(*) FROM planned_lessons WHERE status IN (?, ?)",
                               (d.NO_CHECKIN, d.NEEDS_REVIEW)).fetchone()[0]
        today = conn.execute("SELECT COUNT(*) FROM planned_lessons WHERE date = ? AND status != ?",
                             (clock.today(now).isoformat(), d.CANCELLED)).fetchone()[0]
    return {
        "bot": {"enabled": config.CHECKIN_BOT_ENABLED, "username": config.CHECKIN_BOT_USERNAME or None,
                "token_clash": config.CHECKIN_TOKEN_CLASH},
        "chat_connected": bool(settings["report_chat_id"]),
        "today": clock.today(now).isoformat(), "now": clock.hhmm(now),
        "counts": {"queue": waiting, "today": today, "tracked": len(tracked_ids(database))},
        "readiness": readiness(database, now),
    }


# ── Школы и сотрудники (поля отметок) ─────────────────────────────────────────

def set_school_location(user: Dict[str, Any], school_id: int, address: str, lat: Optional[float],
                        lon: Optional[float], radius_m: int) -> Dict[str, Any]:
    if (lat is None) != (lon is None):
        raise CheckinError("Укажите и широту, и долготу — или очистите обе")
    result: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        school = db.find_school(database, school_id)
        if not school:
            raise NotFound("Школа не найдена")
        fields = ("address", "lat", "lon", "radius_m")
        result["before"] = {f: school.get(f) for f in fields}
        school.update(address=(address or "").strip(), lat=lat, lon=lon, radius_m=radius_m)
        result["after"] = {f: school.get(f) for f in fields}
        result["school"] = dict(school)
        return None if result["before"] != result["after"] else db.NO_SAVE

    db.transaction(_apply)
    if result["before"] != result["after"]:
        with store.tx() as conn:
            store.audit(conn, user, "school", school_id, "location", result["before"], result["after"])
    return result["school"]


STAFF_AUDIT_FIELDS = ("hourly_rate", "checkin_tracked", "checkin_manager", "tg_id", "tg_username")


def audit_staff(user: Dict[str, Any], target_id: int, action: str, before: Dict[str, Any],
                after: Dict[str, Any]) -> None:
    """Ставка, флажки отметок и привязка Telegram влияют на деньги — пишем в историю."""
    before = {f: before.get(f) for f in STAFF_AUDIT_FIELDS}
    after = {f: after.get(f) for f in STAFF_AUDIT_FIELDS}
    if before == after:
        return
    with store.tx() as conn:
        store.audit(conn, user, "user", target_id, action, before, after)


# ── Отчёт ─────────────────────────────────────────────────────────────────────

def month_report(year: int, month: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or clock.now_utc()
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise CheckinError("Неверный месяц")
    first, last = clock.month_bounds(year, month)
    database = db.load_db()
    with store.tx() as conn:
        settings = store.get_settings(conn)
        lessons = store.rows(conn.execute(
            "SELECT * FROM planned_lessons WHERE date >= ? AND date <= ? ORDER BY starts_at, id",
            (first.isoformat(), last.isoformat())))
        items = _present_many(conn, lessons, database, now)
    report = d.report(lessons, database.get("users", []), first, last, now, settings["grace_minutes"])
    report["month"] = f"{year:04d}-{month:02d}"
    report["lessons"] = items
    return report
