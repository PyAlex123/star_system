"""Отметки преподавателей, этап 3: сценарии над обеими базами — без Telegram.

Понедельник 23 ноября 2026, время по Ташкенту. Преподаватель — учитель из .env (id 2),
школа 1 с координатами, занятие по понедельникам 09:00–09:45.
"""
import sqlite3
from datetime import date, timedelta

import pytest
from openpyxl import load_workbook

from backend import config, db, excel
from backend.checkin import clock, service, store
from backend.checkin import domain as d

MON = date(2026, 11, 23)
NEXT_MON = MON + timedelta(days=7)
LAT, LON = 41.311, 69.279
METRE = 1 / 111_195
ADMIN = {"id": 1, "name": "Администратор"}


def entry(entry_id, start="09:00", end="09:45", weekday=1, user_id=2, class_id=1, **extra):
    return dict({"id": entry_id, "class_id": class_id, "weekday": weekday, "start": start, "end": end,
                 "room": "12", "user_id": user_id, "created_at": "2026-01-01",
                 "valid_from": None, "valid_to": None}, **extra)


def prepare(*entries, coords=True, tracked=True):
    def _apply(database):
        school = database["schools"][0]
        school.update(lat=LAT if coords else None, lon=LON if coords else None, radius_m=150)
        teacher = db.find_user(database, 2)
        teacher.update(checkin_tracked=tracked, hourly_rate=90000, tg_id=900)
        database["schedule"] = list(entries)
    db.transaction(_apply)


def now_at(day, hhmm):
    moment = clock.at(day, hhmm)
    clock.freeze(moment)
    return moment


def lessons(day=MON):
    with store.tx() as conn:
        return store.rows(conn.execute("SELECT * FROM planned_lessons WHERE date = ? ORDER BY starts_at",
                                       (day.isoformat(),)))


def notifications():
    with store.tx() as conn:
        return [(r["lesson_id"], r["kind"], r["status"]) for r in
                conn.execute("SELECT * FROM notifications ORDER BY id")]


def morning(*entries, **kwargs):
    """Утро понедельника: планировщик уже разложил уроки (урок, который начался,
    в базе не появляется — его нужно разложить заранее)."""
    prepare(*entries, **kwargs)
    now_at(MON, "07:00")
    service.tick()


def checkin(at, lat=LAT, lon=LON, **extra):
    return service.start_checkin(2, lat, lon, at, **extra)


def video(at, unique="vid-1", kind="video_note", forwarded=False):
    return service.finish_checkin(2, kind, forwarded, "file-" + unique, unique, at)


# ── Раскладка ────────────────────────────────────────────────────────────────

def test_reconcile_creates_lessons_once():
    prepare(entry(1))
    now_at(MON, "07:00")
    assert service.reconcile() == {"insert": 2, "update": 0, "delete": 0}
    assert service.reconcile() == {"insert": 0, "update": 0, "delete": 0}
    (only,) = lessons()
    assert only["status"] == "planned" and only["teacher_name"] == "Учитель" and only["school_name"]
    assert [l["date"] for l in lessons(NEXT_MON)] == ["2026-11-30"]
    with store.tx() as conn:
        events = [r["event_type"] for r in conn.execute("SELECT event_type FROM events")]
    assert events.count("reconcile_insert") == 2, "каждый шаг сверки — в журнале"


def test_untracked_teacher_gets_nothing():
    prepare(entry(1), tracked=False)
    now_at(MON, "07:00")
    service.tick()
    assert lessons() == []


def test_holiday_cancels_and_removing_it_brings_lesson_back():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.reconcile()
    holiday = service.add_exception(ADMIN, {"kind": "holiday", "date_from": MON.isoformat(), "reason": "Навруз"})
    view = service.day_view(MON)
    assert view["lessons"][0]["status"] == "cancelled" and view["lessons"][0]["note"] == "Навруз"
    service.delete_exception(ADMIN, holiday["id"])
    assert lessons()[0]["status"] == "planned"
    with store.tx() as conn:
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE entity = 'exception'")]
    assert actions == ["create", "delete"]


@pytest.mark.parametrize("data,message", [
    ({"kind": "holiday", "date_from": "2026-11-23", "user_id": 2, "reason": "x"}, "Праздник"),
    ({"kind": "cancel", "date_from": "2026-11-23", "reason": "x"}, "что отменить"),
    ({"kind": "substitute", "date_from": "2026-11-23", "entry_id": 1, "reason": "x"}, "кто заменяет"),
    ({"kind": "substitute", "date_from": "2026-11-23", "entry_id": 1, "substitute_user_id": 1,
      "reason": "x"}, "не отмечается"),
    ({"kind": "holiday", "date_from": "2026-11-23", "date_to": "2026-11-20", "reason": "x"}, "раньше"),
    ({"kind": "holiday", "date_from": "2026-01-01", "date_to": "2027-06-01", "reason": "x"}, "года"),
    ({"kind": "holiday", "date_from": "2026-11-23"}, "причину"),
])
def test_bad_exceptions_are_explained(data, message):
    prepare(entry(1))
    with pytest.raises(service.CheckinError, match=message):
        service.add_exception(ADMIN, data)


# ── Нет отметки → очередь ────────────────────────────────────────────────────

def test_no_checkin_goes_to_queue_once():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.tick()
    now_at(MON, "09:14")
    service.tick()
    assert lessons()[0]["status"] == "planned"
    now_at(MON, "09:15")
    assert service.tick()["no_checkin"] == 1
    lesson_id = lessons()[0]["id"]
    assert lessons()[0]["status"] == "no_checkin"
    service.tick()
    assert notifications() == [(lesson_id, "review", "pending"), (lesson_id, "late_teacher", "pending")]
    assert [l["id"] for l in service.queue()] == [lesson_id]


def test_teacher_reminder_can_be_switched_off():
    prepare(entry(1))
    with store.tx() as conn:
        store.set_setting(conn, "notify_teacher_late", False)
    now_at(MON, "07:00")
    service.tick()
    now_at(MON, "09:20")
    service.tick()
    assert [kind for _, kind, _ in notifications()] == ["review"]


def test_waiting_for_video_holds_back_no_checkin():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.tick()
    assert checkin(now_at(MON, "09:14"))["result"] == "await_video"
    clock.freeze(clock.at(MON, "09:15") + timedelta(seconds=30))
    service.tick()
    assert lessons()[0]["status"] == "planned", "преподаватель как раз отмечается"
    result = video(clock.at(MON, "09:15") + timedelta(seconds=40))
    assert result["result"] == "accepted"
    assert (lessons()[0]["status"], lessons()[0]["late_minutes"]) == ("late", 14)


# ── Отметка ──────────────────────────────────────────────────────────────────

def test_checkin_on_time():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.tick()
    started = checkin(now_at(MON, "08:50"), accuracy=12.0, message_id=77)
    assert started["result"] == "await_video" and started["geo"] == "ok" and started["distance_m"] == 0
    assert started["deadline_sec"] == 120 and [l["start"] for l in started["lessons"]] == ["09:00"]
    finished = video(now_at(MON, "08:51"))
    assert finished["result"] == "accepted"
    lesson = lessons()[0]
    assert (lesson["status"], lesson["late_minutes"], lesson["flag"]) == ("checked_in", 0, "")
    assert lesson["checkin_id"] == started["checkin_id"]
    with store.tx() as conn:
        row = store.row(conn.execute("SELECT * FROM checkins WHERE id = ?", (started["checkin_id"],)).fetchone())
    assert row["status"] == "accepted" and row["video_file_id"] == "file-vid-1" and row["accuracy_m"] == 12.0
    assert notifications() == [], "вовремя — ничего не ждёт решения"


def test_one_checkin_closes_the_block():
    prepare(entry(1), entry(2, start="09:55", end="10:40"))
    now_at(MON, "07:00")
    service.tick()
    checkin(now_at(MON, "09:20"))
    video(now_at(MON, "09:21"))
    assert [(l["status"], l["late_minutes"]) for l in lessons()] == [("late", 20), ("checked_in", 0)]


def test_far_from_school_goes_to_review_with_a_card():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.tick()
    started = checkin(now_at(MON, "08:55"), lat=LAT + 2000 * METRE)
    assert started["geo"] == "far" and started["distance_m"] == 2000
    video(now_at(MON, "08:56"))
    lesson = lessons()[0]
    assert (lesson["status"], lesson["flag"]) == ("needs_review", "far")
    assert notifications() == [(lesson["id"], "review", "pending")]


def test_school_without_coords_goes_to_review():
    prepare(entry(1), coords=False)
    now_at(MON, "07:00")
    service.tick()
    assert checkin(now_at(MON, "08:55"))["geo"] == "no_coords"
    video(now_at(MON, "08:56"))
    assert lessons()[0]["flag"] == "no_coords"


def test_location_without_lesson_is_logged():
    morning(entry(1))
    result = checkin(now_at(MON, "12:00"))
    assert result["result"] == "no_lesson"
    with store.tx() as conn:
        row = store.row(conn.execute("SELECT * FROM checkins").fetchone())
    assert row["status"] == "no_lesson" and row["lat"] == LAT
    assert lessons()[0]["status"] == "planned", "урок не создаётся и не трогается"


def test_already_checked_in():
    prepare(entry(1))
    checkin(now_at(MON, "08:50"))
    video(now_at(MON, "08:51"))
    again = checkin(now_at(MON, "08:58"))
    assert again["result"] == "already" and again["lessons"][0]["status"] == "checked_in"


@pytest.mark.parametrize("extra,problem", [
    ({"forwarded": True}, "forwarded"), ({"venue": True}, "venue"), ({"live_period": 900}, "live"),
])
def test_bad_locations_are_rejected(extra, problem):
    prepare(entry(1))
    result = checkin(now_at(MON, "08:50"), **extra)
    assert result == {"result": "problem", "problem": problem}
    with store.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM checkins").fetchone()[0] == 0


def test_not_tracked_teacher():
    prepare(entry(1), tracked=False)
    assert checkin(now_at(MON, "08:50")) == {"result": "not_tracked"}


def test_bad_videos_are_rejected_and_wait_continues():
    prepare(entry(1))
    assert video(now_at(MON, "08:40"))["problem"] == "no_pending"
    checkin(now_at(MON, "08:50"))
    assert video(now_at(MON, "08:50"), forwarded=True)["problem"] == "forwarded"
    assert video(now_at(MON, "08:50"), kind="video")["problem"] == "not_video_note"
    assert video(now_at(MON, "08:51"))["result"] == "accepted", "после отказа можно записать заново"


def test_same_video_twice_is_rejected():
    prepare(entry(1), entry(2, start="12:00", end="12:45"))
    checkin(now_at(MON, "08:50"))
    video(now_at(MON, "08:51"), unique="same")
    checkin(now_at(MON, "11:50"))
    assert video(now_at(MON, "11:51"), unique="same")["problem"] == "duplicate"


def test_video_three_minutes_late_means_no_video():
    prepare(entry(1))
    checkin(now_at(MON, "08:50"))
    result = video(now_at(MON, "08:53"))
    assert result["result"] == "problem" and result["problem"] == "late"
    lesson = lessons()[0]
    assert (lesson["status"], lesson["flag"]) == ("needs_review", "no_video")


def test_expired_wait_and_new_checkin_resolves_it():
    prepare(entry(1))
    checkin(now_at(MON, "08:50"))
    now_at(MON, "08:53")
    assert service.tick()["expired"] == 1
    assert lessons()[0]["flag"] == "no_video"
    checkin(now_at(MON, "08:55"))
    video(now_at(MON, "08:56"), unique="vid-2")
    assert lessons()[0]["status"] == "checked_in", "свежая честная отметка снимает спорную"


def test_admin_decision_during_wait_is_not_overwritten():
    morning(entry(1))
    checkin(now_at(MON, "09:20"))
    lesson_id = lessons()[0]["id"]
    service.decide(ADMIN, [lesson_id], "approve", "Видел сам")
    assert video(now_at(MON, "09:21"))["lessons"] == []
    assert lessons()[0]["status"] == "approved"


def test_checkin_after_reject_goes_to_review():
    morning(entry(1))
    now_at(MON, "09:20")
    service.tick()
    service.decide(ADMIN, [lessons()[0]["id"]], "reject", "Не приехал")
    checkin(now_at(MON, "09:30"))
    video(now_at(MON, "09:31"))
    assert (lessons()[0]["status"], lessons()[0]["flag"]) == ("needs_review", "after_reject")


def test_second_location_replaces_waiting_one():
    prepare(entry(1))
    first = checkin(now_at(MON, "08:50"))
    second = checkin(now_at(MON, "08:51"))
    with store.tx() as conn:
        statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM checkins")}
    assert statuses == {first["checkin_id"]: "replaced", second["checkin_id"]: "await_video"}


# ── Решения администратора ───────────────────────────────────────────────────

def _missed():
    morning(entry(1), entry(2, start="10:00", end="10:45"))
    now_at(MON, "10:30")
    service.tick()
    return [l["id"] for l in lessons()]


def test_decision_needs_a_reason():
    first, _ = _missed()
    with pytest.raises(service.CheckinError, match="причину"):
        service.decide(ADMIN, [first], "approve", "  ")


def test_approve_and_reject_with_history():
    first, second = _missed()
    service.decide(ADMIN, [first, second], "approve", "Был на месте, телефон сел")
    assert [l["status"] for l in lessons()] == ["approved", "approved"]
    service.decide(ADMIN, [second], "reject", "Ушёл раньше")
    detail = service.lesson_detail(second)
    assert detail["status"] == "rejected" and detail["reviewed_by"] == "Администратор"
    assert [(h["action"], h["before"]["status"], h["after"]["status"], h["reason"]) for h in detail["history"]] == \
        [("approve", "no_checkin", "approved", "Был на месте, телефон сел"),
         ("reject", "approved", "rejected", "Ушёл раньше")]
    assert "password_hash" not in str(detail["history"])


def test_batch_is_all_or_nothing():
    morning(entry(1), entry(2, start="12:00", end="12:45"))
    now_at(MON, "10:00")
    service.tick()
    started, future = [l["id"] for l in lessons()]
    with pytest.raises(service.CheckinError, match="ещё не начался"):
        service.decide(ADMIN, [started, future], "approve", "Пачкой")
    assert lessons()[0]["status"] == "no_checkin", "первый урок пачки не тронут"


def test_cancel_and_reopen():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.tick()
    lesson_id = lessons()[0]["id"]
    service.decide(ADMIN, [lesson_id], "cancel", "Школа на карантине")
    lesson = lessons()[0]
    assert (lesson["status"], lesson["cancel_source"], lesson["note"]) == ("cancelled", "admin", "Школа на карантине")
    service.reconcile()
    assert lessons()[0]["status"] == "cancelled", "сверка не отменяет решение администратора"
    service.decide(ADMIN, [lesson_id], "reopen", "Карантин сняли")
    assert lessons()[0]["status"] == "planned"


def test_reopen_of_holiday_points_to_calendar():
    prepare(entry(1))
    now_at(MON, "07:00")
    service.add_exception(ADMIN, {"kind": "holiday", "date_from": MON.isoformat(), "reason": "Праздник"})
    with pytest.raises(service.CheckinError, match="календаре"):
        service.decide(ADMIN, [lessons()[0]["id"]], "reopen", "Вернуть")


def test_manual_hours():
    first, _ = _missed()
    service.set_hours(ADMIN, first, 1.5, "Провёл сдвоенный урок")
    assert d.counted_minutes(lessons()[0]) == 90
    with pytest.raises(service.CheckinError, match="четверть"):
        service.set_hours(ADMIN, first, 1.3, "x")
    service.set_hours(ADMIN, first, None, "Ошибся")
    assert lessons()[0]["hours_override"] is None
    assert [h["action"] for h in service.lesson_detail(first)["history"]] == ["hours", "hours"]


def test_one_off_lesson_survives_reconcile():
    prepare()
    now_at(MON, "07:00")
    lesson = service.add_lesson(ADMIN, 2, 1, MON.isoformat(), "15:00", "15:45", "", "Перенос с пятницы")
    assert lesson["status"] == "planned" and lesson["entry_id"] is None
    service.tick()
    assert [l["id"] for l in lessons()] == [lesson["id"]]
    past = service.add_lesson(ADMIN, 2, 1, (MON - timedelta(days=3)).isoformat(), "10:00", "10:45", "",
                              "Забыли внести")
    assert past["status"] == "no_checkin" and (past["id"], "review", "pending") in notifications()
    with pytest.raises(service.CheckinError, match="не отмечается"):
        service.add_lesson(ADMIN, 1, 1, MON.isoformat(), "15:00", "15:45", "", "x")


# ── Просмотр ─────────────────────────────────────────────────────────────────

def test_day_view_states_and_stars_hint():
    morning(entry(1), entry(2, start="12:00", end="12:45"))

    def _stars(database):
        database["lessons"].append({"id": 900, "class_id": 1, "date": MON.isoformat(), "awards": {"101": 2},
                                    "undone": {}, "total_stars": 2})
    db.transaction(_stars)
    checkin(now_at(MON, "09:10"))
    video(now_at(MON, "09:11"))
    view = service.day_view(MON)
    first, second = view["lessons"]
    assert (first["state"], first["start"], first["status_label"], first["late_minutes"]) == ("late", "09:00", "опоздал", 10)
    assert first["stars_given"] is True and first["checkin"]["video"] is True and first["checkin"]["at"] == "09:10"
    assert second["state"] == "ahead"
    assert view["counts"] == {"late": 1, "ahead": 1} and view["today"] == "2026-11-23"


def test_overview_shows_what_blocks_checkins():
    prepare(entry(1), entry(2, start="12:00", end="12:45", user_id=None), coords=False)

    def _unbind(database):
        db.find_user(database, 2)["tg_id"] = None
    db.transaction(_unbind)
    ready = service.overview(now_at(MON, "07:00"))["readiness"]
    assert [s["id"] for s in ready["schools_without_coords"]] == [1]
    assert [u["id"] for u in ready["tracked_without_telegram"]] == [2]
    assert [e["id"] for e in ready["entries_without_tracked_teacher"]] == [2]
    assert ready["tracked_without_lessons"] == []


def test_overview_warns_about_tracked_teacher_without_lessons():
    """Занятие внёс администратор и не выбрал учителя — оно записалось на него самого."""
    prepare(entry(1, user_id=1))
    ready = service.overview(now_at(MON, "07:00"))["readiness"]
    assert [u["id"] for u in ready["tracked_without_lessons"]] == [2]
    assert [e["id"] for e in ready["entries_without_tracked_teacher"]] == [1]


# ── Отчёт и Excel ────────────────────────────────────────────────────────────

def test_month_report_and_excel():
    morning(entry(1, end="09:50"), entry(2, start="10:00", end="10:45"))
    checkin(now_at(MON, "08:55"))
    video(now_at(MON, "08:56"))
    now_at(MON, "11:00")
    service.tick()
    second = lessons()[1]["id"]
    service.decide(ADMIN, [second], "reject", "Не был")
    report = service.month_report(2026, 11)
    (row,) = report["teachers"]
    assert (row["confirmed"], row["rejected"], row["minutes"], row["amount"]) == (1, 1, 50, 75000)
    assert report["month"] == "2026-11" and len(report["lessons"]) >= 2

    book = load_workbook(__import__("io").BytesIO(excel.build_hours_report(report, "Часы")))
    assert book.sheetnames == ["Преподаватели", "Школы", "Уроки"]
    teacher_row = [c.value for c in book["Преподаватели"][2]]
    assert teacher_row[0] == "Учитель" and teacher_row[8] == 0.83 and teacher_row[10] == 75000
    lesson_rows = [[c.value for c in r] for r in book["Уроки"].iter_rows(min_row=2)]
    assert ["2026-11-23", "09:00", "09:50"] == lesson_rows[0][:3] and lesson_rows[1][6] == "не засчитан"


def test_report_rejects_bad_month():
    with pytest.raises(service.CheckinError):
        service.month_report(2026, 13)


# ── Бэкап ────────────────────────────────────────────────────────────────────

def test_nightly_snapshot_and_restore():
    prepare(entry(1))
    now_at(MON, "02:59")
    assert service.tick()["daily"] is False, "до 03:00 — рано"
    now_at(MON, "03:30")
    assert service.tick()["daily"] is True
    assert service.tick()["daily"] is False, "раз в сутки"
    assert config.CHECKIN_BACKUP_FILE.exists()
    count = len(lessons())

    config.CHECKIN_DB_FILE.unlink()             # распаковали data/ из архива: живого файла там нет
    assert store.restore_if_missing() is True
    store._ready_for = None
    assert len(lessons()) == count
    assert store.restore_if_missing() is False, "живую базу снимок не перетирает"
    conn = sqlite3.connect(str(config.CHECKIN_DB_FILE))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_lesson_added_after_start_is_not_created():
    """Занятие внесли в 10:30 на 09:00 сегодня — урока нет и в очередь он не падает."""
    prepare(entry(1))
    now_at(MON, "10:30")
    service.tick()
    assert lessons() == [] and notifications() == []


# ── Планировщик ──────────────────────────────────────────────────────────────

def test_scheduler_tick_and_lifecycle(monkeypatch):
    import asyncio

    from backend.checkin import scheduler

    morning(entry(1))
    assert scheduler.run_once()["reconcile"] == {"insert": 0, "update": 0, "delete": 0}
    assert scheduler.status()["last_tick"] == clock.iso(clock.now_utc())

    def _broken():
        raise RuntimeError("диск")
    monkeypatch.setattr(service, "tick", _broken)
    with pytest.raises(RuntimeError):
        scheduler.run_once()
    assert scheduler.status()["last_error"].endswith("RuntimeError")

    async def _start_stop():
        scheduler.start()
        await asyncio.sleep(0)
        running = scheduler.status()["scheduler"]
        await scheduler.stop()
        return running
    assert asyncio.run(_start_stop()) is True
    assert scheduler.status()["scheduler"] is False
