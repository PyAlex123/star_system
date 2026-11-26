"""Отметки преподавателей, этап 1: часы, хранилище SQLite, схема 9 основной базы."""
import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from backend import config, db
from backend.checkin import clock, store
from conftest import load_db


# ── Часы ──────────────────────────────────────────────────────────────────────

def test_lesson_time_is_tashkent():
    """09:00 в Ташкенте — это 04:00 UTC: пояс +5 без летнего времени."""
    moment = clock.at(date(2026, 11, 23), "09:00")
    assert moment == datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
    assert clock.at(date(2026, 7, 1), "09:00").hour == 4, "летом тоже +5"
    assert clock.hhmm(moment) == "09:00"


def test_today_follows_school_clock_not_utc():
    """В 20:30 UTC в Ташкенте уже следующий день."""
    clock.freeze(datetime(2026, 11, 23, 20, 30, tzinfo=timezone.utc))
    assert clock.today() == date(2026, 11, 24)
    clock.freeze(datetime(2026, 11, 23, 18, 59, tzinfo=timezone.utc))
    assert clock.today() == date(2026, 11, 23)


def test_iso_round_trip_and_naive_is_utc():
    moment = datetime(2026, 11, 23, 4, 7, 30, tzinfo=timezone.utc)
    assert clock.parse(clock.iso(moment)) == moment
    assert clock.parse("2026-11-23T04:07:30") == moment
    assert clock.parse(None) is None


def test_minutes_between_rounds_down():
    start = clock.at(date(2026, 11, 23), "09:00")
    assert clock.minutes_between(start, clock.at(date(2026, 11, 23), "09:07")) == 7
    assert clock.minutes_between(start, start.replace(second=59)) == 0
    assert clock.minutes_between(start, clock.at(date(2026, 11, 23), "08:50")) == -10


def test_month_bounds():
    assert clock.month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert clock.month_bounds(2026, 12) == (date(2026, 12, 1), date(2026, 12, 31))


# ── Хранилище ─────────────────────────────────────────────────────────────────

def test_fresh_store_has_schema_and_default_settings():
    assert not config.CHECKIN_DB_FILE.exists()
    assert store.schema_version() == len(store.MIGRATIONS)
    settings = store.get_settings()
    assert settings == {
        "grace_minutes": 5, "checkin_window_before_min": 60, "video_note_deadline_sec": 120,
        "review_after_min": 15, "merge_gap_min": 30, "lesson_horizon_days": 14,
        "notify_teacher_late": True, "report_chat_id": 0,
    }
    with store.tx() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"planned_lessons", "checkins", "exceptions", "notifications", "audit_log",
            "events", "settings", "pending_actions", "jobs"} <= tables


def test_store_lives_in_data_dir():
    """Один каталог данных — один том в Docker и одна папка в бэкапе."""
    assert config.CHECKIN_DB_FILE.parent == config.DATA_DIR


def test_changed_settings_survive_reopen():
    with store.tx() as conn:
        store.set_setting(conn, "grace_minutes", 10)
    store._ready_for = None                      # как после перезапуска сервера
    assert store.get_settings()["grace_minutes"] == 10
    assert store.schema_version() == len(store.MIGRATIONS)


@pytest.mark.parametrize("key,value", [
    ("grace_minutes", -1), ("grace_minutes", 61), ("grace_minutes", "5"), ("grace_minutes", 2.5),
    ("grace_minutes", True), ("notify_teacher_late", 1), ("no_such_setting", 1),
])
def test_bad_settings_are_rejected(key, value):
    with pytest.raises(ValueError):
        store.clean_setting(key, value)


def test_transaction_is_all_or_nothing():
    with pytest.raises(RuntimeError):
        with store.tx() as conn:
            store.set_setting(conn, "grace_minutes", 30)
            raise RuntimeError("сбой посреди записи")
    assert store.get_settings()["grace_minutes"] == 5


def test_lesson_per_entry_and_date_is_unique():
    """Один урок занятия на дату — повторная раскладка не плодит дублей.
    Разовых уроков (без занятия) в один день может быть несколько."""
    row = ("2026-11-23", 1, "2026-11-23T04:00:00+00:00", "2026-11-23T04:45:00+00:00", store.now(), store.now())
    insert = ("INSERT INTO planned_lessons (entry_id, date, school_id, starts_at, ends_at, created_at, updated_at) "
              "VALUES (?, ?, ?, ?, ?, ?, ?)")
    with store.tx() as conn:
        conn.execute(insert, (7,) + row)
        conn.execute(insert, (None,) + row)
        conn.execute(insert, (None,) + row)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (7,) + row)


def test_audit_and_events_keep_json():
    with store.tx() as conn:
        store.audit(conn, {"id": 1, "name": "Администратор"}, "lesson", 5, "approve",
                    before={"status": "no_checkin"}, after={"status": "approved"}, reason="был у директора")
        store.log_event(conn, "location", user_id=2, tg_id=900, payload={"lat": 41.3, "lon": 69.2})
        audit = store.row(conn.execute("SELECT * FROM audit_log").fetchone())
        event = store.row(conn.execute("SELECT * FROM events").fetchone())
    assert audit["user_name"] == "Администратор" and audit["reason"] == "был у директора"
    assert store.loads(audit["after"]) == {"status": "approved"}
    assert store.loads(event["payload"])["lat"] == 41.3


def test_snapshot_is_a_readable_copy(tmp_path):
    with store.tx() as conn:
        store.set_setting(conn, "merge_gap_min", 45)
    copy = store.snapshot(tmp_path / "copy.sqlite")
    conn = sqlite3.connect(str(copy))
    try:
        value = conn.execute("SELECT value FROM settings WHERE key = 'merge_gap_min'").fetchone()[0]
    finally:
        conn.close()
    assert value == "45"


# ── Основная база: схема 9 ────────────────────────────────────────────────────

V8 = {
    "version": 8,
    "schools": [{"id": 1, "name": "Школа №1", "theme": "quest"}],
    "classes": [{"id": 1, "school_id": 1, "name": "5-А"}],
    "students": [],
    "lessons": [],
    "schedule": [
        {"id": 1, "class_id": 1, "weekday": 1, "start": "09:00", "end": "09:45", "room": "",
         "user_id": 2, "created_at": "2026-10-05"},
        {"id": 2, "class_id": 1, "weekday": 3, "start": "09:00", "end": "09:45", "room": "", "user_id": 2},
    ],
    "users": [
        {"id": 1, "login": "admin", "name": "Администратор", "role": "superadmin", "school_ids": [],
         "password_hash": "x", "session_key": "a"},
        {"id": 2, "login": "teacher", "name": "Учитель", "role": "teacher", "school_ids": [1],
         "password_hash": "x", "session_key": "b", "password_shown": "provod4696"},
    ],
    "settings": {},
}


def test_v8_gets_checkin_fields_everywhere():
    config.DB_FILE.write_text(json.dumps(V8, ensure_ascii=False), encoding="utf-8")
    data = db.load_db()
    assert data["version"] == db.DB_VERSION == 11
    school = data["schools"][0]
    assert school["theme"] == "quest", "тема не сбрасывается"
    assert school["lat"] is None and school["lon"] is None and school["radius_m"] == 150
    for user in data["users"]:
        assert set(db.STAFF_CHECKIN_DEFAULTS) <= set(user), user["login"]
        assert user["tg_id"] is None and user["lang"] == "ru" and "checkin_manager" not in user
        assert user["checkin_tracked"] is False, "в отметках только те, кого включили явно"
    assert data["users"][1]["password_shown"] == "provod4696", "выданный пароль не теряется"
    for entry in data["schedule"]:
        assert entry["valid_from"] is None and entry["valid_to"] is None, "None — с того дня, как внесли"
    assert data["schedule"][0]["created_at"] == "2026-10-05"
    assert data["settings"]["last_user_id"] == 2 and data["settings"]["last_schedule_id"] == 2
    assert data["settings"]["last_school_id"] == 1


def test_v9_with_bells_gets_checkin_fields():
    """Сервер мог уже перейти на схему 9 из main (время уроков) — поля отметок
    добавляются по наличию ключей, время уроков и места в таблице не трогаются."""
    v9 = json.loads(json.dumps(V8, ensure_ascii=False))
    v9["version"] = 9
    bells = [{"start": "08:30", "end": "09:15"}, {"start": "09:25", "end": "10:10"}]
    v9["schools"][0].update(bells=bells, days=6)
    for entry in v9["schedule"]:
        entry["lesson"] = 2
    config.DB_FILE.write_text(json.dumps(v9, ensure_ascii=False), encoding="utf-8")
    data = db.load_db()
    assert data["version"] == db.DB_VERSION
    school = data["schools"][0]
    assert school["bells"] == bells and school["days"] == 6
    assert school["radius_m"] == 150 and school["lat"] is None
    assert all(e["lesson"] == 2 and e["valid_from"] is None for e in data["schedule"])
    assert all(set(db.STAFF_CHECKIN_DEFAULTS) <= set(u) for u in data["users"])


def test_v9_migration_is_idempotent():
    config.DB_FILE.write_text(json.dumps(V8, ensure_ascii=False), encoding="utf-8")
    first = db.load_db()
    mtime = config.DB_FILE.stat().st_mtime_ns
    assert db.load_db() == first
    assert config.DB_FILE.stat().st_mtime_ns == mtime


def test_invite_code_is_admin_only():
    user = dict(V8["users"][1], invite_code="INVITE123")
    assert "invite_code" not in db.public_user(user)
    assert "password_shown" not in db.public_user(user)
    assert db.public_user(user, with_password=True)["invite_code"] == "INVITE123"


def test_new_school_and_staff_and_schedule_get_fields(teacher):
    school = teacher.post("/api/schools", json={"name": "Школа на Чиланзаре"}).json()["school"]
    assert school["radius_m"] == 150 and school["lat"] is None
    r = teacher.post("/api/users", json={"login": "karimova", "name": "Каримова Д.",
                                         "school_ids": [1], "role": "teacher"})
    user = r.json()["user"]
    assert user["tg_id"] is None and user["hourly_rate"] is None and user["posts"] == {"1": ["teacher"]}
    r = teacher.post("/api/schedule", json={"class_id": 1, "weekdays": [2], "start": "10:00", "end": "10:45"})
    entry = r.json()["created"][0]
    assert entry["valid_from"] is None and entry["valid_to"] is None
    saved = load_db()
    assert saved["version"] == db.DB_VERSION


def test_ids_are_never_reused(teacher):
    """К номерам сотрудника, школы и занятия привязаны отметки в checkin.sqlite:
    удалённый последним не отдаёт свой номер новичку."""
    first = teacher.post("/api/users", json={"login": "first.t", "name": "Первый",
                                             "school_ids": [1]}).json()["user"]["id"]
    assert teacher.delete(f"/api/users/{first}").status_code == 200
    second = teacher.post("/api/users", json={"login": "second.t", "name": "Второй",
                                              "school_ids": [1]}).json()["user"]["id"]
    assert second == first + 1

    entry = teacher.post("/api/schedule", json={"class_id": 1, "weekdays": [2], "start": "10:00",
                                                "end": "10:45"}).json()["created"][0]["id"]
    assert teacher.delete(f"/api/schedule/{entry}").status_code == 200
    again = teacher.post("/api/schedule", json={"class_id": 1, "weekdays": [2], "start": "10:00",
                                                "end": "10:45"}).json()["created"][0]["id"]
    assert again == entry + 1

    school = teacher.post("/api/schools", json={"name": "Временная"}).json()["school"]["id"]
    assert teacher.delete(f"/api/schools/{school}").status_code == 200
    other = teacher.post("/api/schools", json={"name": "Новая"}).json()["school"]["id"]
    assert other == school + 1


def test_invite_code_never_reaches_non_admins(teacher, school_teacher):
    """Код приглашения — ключ к привязке Telegram: учитель не видит его ни у себя, ни у других."""
    def _apply(database):
        for user in database["users"]:
            user["invite_code"] = f"INV{user['id']}SECRET"
            user["hourly_rate"] = 90000
    db.transaction(_apply)
    state = school_teacher.get("/api/state").json()
    text = json.dumps(state, ensure_ascii=False)
    assert "SECRET" not in text
    assert "users" not in state
    assert all(set(s) <= {"id", "name", "role", "avatar", "posts"} for s in state["staff"]), \
        "о коллегах — только имя, роль, должности и фото: без ставки, Telegram и кода приглашения"
    assert "hourly_rate" not in state["me"], "свою ставку сотрудник не видит — деньги только у администратора"
    admin_state = teacher.get("/api/state").json()
    assert any(u.get("invite_code") for u in admin_state["users"]), "администратору — видно"
