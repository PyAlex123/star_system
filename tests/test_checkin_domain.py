"""Отметки преподавателей, этап 2: доменная логика — чистые функции, без баз и Telegram.

Даты: 23 ноября 2026 — понедельник. Время уроков — по Ташкенту (UTC+5).
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.checkin import clock, domain as d, store

MON = date(2026, 11, 23)
TUE = MON + timedelta(days=1)
NEXT_MON = MON + timedelta(days=7)
SETTINGS = {key: spec["default"] for key, spec in store.SETTINGS.items()}

SCHOOL_A = {"id": 1, "name": "Школа №1", "lat": 41.311, "lon": 69.279, "radius_m": 150}
SCHOOL_B = {"id": 2, "name": "Школа №2", "lat": 41.350, "lon": 69.200, "radius_m": 150}
SCHOOL_C = {"id": 3, "name": "Школа без координат", "lat": None, "lon": None, "radius_m": 150}
SCHOOLS = {s["id"]: s for s in (SCHOOL_A, SCHOOL_B, SCHOOL_C)}
METRE = 1 / 111_195        # градус широты ≈ 111,2 км


def at(day: date, hhmm: str) -> datetime:
    return clock.at(day, hhmm)


def entry(entry_id, class_id=1, weekday=1, start="09:00", end="09:45", user_id=2, **extra):
    return dict({"id": entry_id, "class_id": class_id, "weekday": weekday, "start": start, "end": end,
                 "room": "12", "user_id": user_id, "created_at": "2026-11-01",
                 "valid_from": None, "valid_to": None}, **extra)


def database(*schedule):
    return {
        "schools": [SCHOOL_A, SCHOOL_B, SCHOOL_C],
        "classes": [{"id": 1, "school_id": 1, "name": "5-А"}, {"id": 2, "school_id": 2, "name": "7-Б"},
                    {"id": 3, "school_id": 3, "name": "3-В"}],
        "users": [
            {"id": 1, "name": "Администратор", "role": "superadmin", "checkin_tracked": False},
            {"id": 2, "name": "Каримова Д.", "role": "teacher", "checkin_tracked": True, "hourly_rate": 90000},
            {"id": 3, "name": "Юсупов Б.", "role": "teacher", "checkin_tracked": True},
            {"id": 4, "name": "Петров А.", "role": "teacher", "checkin_tracked": False},
        ],
        "schedule": list(schedule),
        "lessons": [],
    }


def exception(exc_id, kind, date_from, date_to=None, **extra):
    return dict({"id": exc_id, "kind": kind, "date_from": date_from.isoformat(),
                 "date_to": (date_to or date_from).isoformat(), "school_id": None, "entry_id": None,
                 "user_id": None, "substitute_user_id": None, "reason": ""}, **extra)


def materialize(lessons, start_id=1):
    """Ожидаемые уроки → строки planned_lessons."""
    return [dict(l, id=i, checkin_id=None, late_minutes=None, hours_override=None, flag="")
            for i, l in enumerate(lessons, start_id)]


def lesson(lesson_id, day, start, end, school_id=1, user_id=2, status=d.PLANNED, **extra):
    return dict({"id": lesson_id, "entry_id": lesson_id, "date": day.isoformat(), "user_id": user_id,
                 "school_id": school_id, "class_id": school_id, "school_name": SCHOOLS[school_id]["name"],
                 "teacher_name": "Каримова Д.", "starts_at": clock.iso(at(day, start)),
                 "ends_at": clock.iso(at(day, end)), "status": status, "late_minutes": None,
                 "hours_override": None, "cancel_source": "", "flag": ""}, **extra)


def test_base_date_is_monday():
    assert MON.isoweekday() == 1


# ── Расстояние ────────────────────────────────────────────────────────────────

def test_distance_one_degree_of_latitude():
    assert d.distance_m(41, 69, 42, 69) == pytest.approx(111_195, abs=50)
    assert d.distance_m(41.311, 69.279, 41.311, 69.279) == 0


def test_geo_verdict_radius_boundary():
    lat, lon = SCHOOL_A["lat"], SCHOOL_A["lon"]
    assert d.geo_verdict(SCHOOL_A, lat + 100 * METRE, lon) == ("ok", 100)
    assert d.geo_verdict(SCHOOL_A, lat + 149 * METRE, lon)[0] == "ok"
    assert d.geo_verdict(SCHOOL_A, lat + 151 * METRE, lon)[0] == "far"
    assert d.geo_verdict(SCHOOL_C, lat, lon) == ("no_coords", None)
    no_radius = dict(SCHOOL_A, radius_m=None)
    assert d.geo_verdict(no_radius, lat + 140 * METRE, lon)[0] == "ok", "радиус по умолчанию — 150 м"


# ── Срок действия занятия ─────────────────────────────────────────────────────

def test_entry_active_on():
    e = entry(1, created_at="2026-11-10")
    assert not d.entry_active_on(e, date(2026, 11, 9)), "до того дня, как внесли, — нет"
    assert d.entry_active_on(e, date(2026, 11, 10))
    e = entry(1, created_at="2026-11-10", valid_from="2026-11-01", valid_to="2026-11-30")
    assert d.entry_active_on(e, date(2026, 11, 1)), "явный valid_from главнее даты внесения"
    assert d.entry_active_on(e, date(2026, 11, 30))
    assert not d.entry_active_on(e, date(2026, 12, 1))
    assert d.entry_active_on({"weekday": 1}, MON), "без дат — всегда"


# ── Расписание → уроки ────────────────────────────────────────────────────────

def test_weekly_lessons_in_tashkent_time():
    lessons = d.expected_lessons(database(entry(1)), MON, NEXT_MON + timedelta(days=6), [])
    assert [l["date"] for l in lessons] == ["2026-11-23", "2026-11-30"]
    first = lessons[0]
    assert first["starts_at"] == "2026-11-23T04:00:00+00:00" and first["ends_at"] == "2026-11-23T04:45:00+00:00"
    assert first["status"] == "planned" and first["user_id"] == 2 and first["original_user_id"] is None
    assert (first["teacher_name"], first["school_name"], first["class_name"], first["room"]) == \
        ("Каримова Д.", "Школа №1", "5-А", "12")


def test_only_tracked_teachers_get_lessons():
    """Учитель занятия по умолчанию — тот, кто его внёс; без флажка он не отмечается."""
    db = database(entry(1, user_id=1), entry(2, user_id=4), entry(3, user_id=None), entry(4, user_id=99))
    assert d.expected_lessons(db, MON, MON, []) == []


def test_lessons_respect_created_at_and_validity():
    db = database(entry(1, created_at="2026-11-25"))
    assert d.expected_lessons(db, MON, NEXT_MON, [])[0]["date"] == "2026-11-30"


def test_schedule_change_from_date():
    """Смена расписания с даты: старое занятие закрыто, новое действует с понедельника."""
    db = database(entry(1, valid_to="2026-11-29"), entry(2, start="10:00", end="10:45", valid_from="2026-11-30"))
    lessons = d.expected_lessons(db, MON, NEXT_MON, [])
    assert [(l["entry_id"], l["date"]) for l in lessons] == [(1, "2026-11-23"), (2, "2026-11-30")]


def test_broken_entries_are_skipped():
    db = database(entry(1, start="10:00", end="09:00"), entry(2, class_id=99), entry(3, weekday=9),
                  entry(4, start="25:00"))
    assert d.expected_lessons(db, MON, MON, []) == []


def test_lesson_after_midnight_belongs_to_tashkent_date():
    """Урок во вторник в 00:30 по Ташкенту — это понедельник 19:30 UTC, но дата урока — вторник."""
    lessons = d.expected_lessons(database(entry(1, weekday=2, start="00:30", end="01:15")), MON, TUE, [])
    assert len(lessons) == 1
    assert lessons[0]["date"] == "2026-11-24"
    assert lessons[0]["starts_at"] == "2026-11-23T19:30:00+00:00"


def test_holiday_for_all_schools_over_a_range():
    db = database(entry(1), entry(2, class_id=2))
    calendar = [exception(1, "holiday", MON, MON + timedelta(days=3), reason="Каникулы")]
    lessons = d.expected_lessons(db, MON, NEXT_MON, calendar)
    this_week = [l for l in lessons if l["date"] == "2026-11-23"]
    assert len(this_week) == 2
    assert all(l["status"] == "cancelled" and l["cancel_source"] == "exception" for l in this_week)
    assert this_week[0]["note"] == "Каникулы"
    assert all(l["status"] == "planned" for l in lessons if l["date"] == "2026-11-30")


def test_holiday_for_one_school():
    db = database(entry(1), entry(2, class_id=2))
    calendar = [exception(1, "holiday", MON, school_id=2)]
    by_school = {l["school_id"]: l["status"] for l in d.expected_lessons(db, MON, MON, calendar)}
    assert by_school == {1: "planned", 2: "cancelled"}


def test_cancel_one_entry_or_one_teacher():
    db = database(entry(1), entry(2, start="11:00", end="11:45"), entry(3, class_id=2, user_id=3))
    by_entry = [exception(1, "cancel", MON, entry_id=2)]
    statuses = {l["entry_id"]: l["status"] for l in d.expected_lessons(db, MON, MON, by_entry)}
    assert statuses == {1: "planned", 2: "cancelled", 3: "planned"}

    sick = [exception(1, "cancel", MON, MON + timedelta(days=2), user_id=2, reason="Болеет")]
    statuses = {l["entry_id"]: l["status"] for l in d.expected_lessons(db, MON, MON, sick)}
    assert statuses == {1: "cancelled", 2: "cancelled", 3: "planned"}


def test_substitute_teacher():
    db = database(entry(1))
    calendar = [exception(1, "substitute", MON, entry_id=1, substitute_user_id=3, reason="Замена")]
    lessons = d.expected_lessons(db, MON, NEXT_MON, calendar)
    today, next_week = lessons
    assert (today["user_id"], today["original_user_id"], today["teacher_name"]) == (3, 2, "Юсупов Б.")
    assert today["note"] == "Замена"
    assert (next_week["user_id"], next_week["original_user_id"]) == (2, None), "замена только на свою дату"


def test_latest_substitute_wins_and_untracked_substitute_drops_the_lesson():
    db = database(entry(1))
    calendar = [exception(1, "substitute", MON, entry_id=1, substitute_user_id=4),
                exception(2, "substitute", MON, user_id=2, substitute_user_id=3)]
    assert d.expected_lessons(db, MON, MON, calendar)[0]["user_id"] == 3
    only_untracked = calendar[:1]
    assert d.expected_lessons(db, MON, MON, only_untracked) == [], "заменяющий без флажка не отмечается"


def test_cancel_beats_substitute():
    db = database(entry(1))
    calendar = [exception(1, "substitute", MON, entry_id=1, substitute_user_id=3),
                exception(2, "cancel", MON, user_id=3, reason="Заменяющий тоже заболел")]
    only = d.expected_lessons(db, MON, MON, calendar)[0]
    assert only["status"] == "cancelled" and only["user_id"] == 3


# ── Сверка с уже разложенными уроками ─────────────────────────────────────────

def _plan(db, existing, now, calendar=()):
    expected = d.expected_lessons(db, clock.today(now), clock.today(now) + timedelta(days=13), calendar)
    tracked = {u["id"] for u in db["users"] if u.get("checkin_tracked")}
    return d.plan_changes(expected, existing, now, tracked)


def _apply(existing, changes, next_id=100):
    rows = {l["id"]: dict(l) for l in existing}
    for item in changes["update"]:
        rows[item["id"]].update(item["set"])
    for item in changes["delete"]:
        rows.pop(item["id"])
    for i, item in enumerate(changes["insert"], next_id):
        new = dict(item, id=i, checkin_id=None, late_minutes=None, hours_override=None, flag="")
        new.pop("reason")
        rows[i] = new
    return list(rows.values())


def _joint_db(*schedule):
    """Школа №1 с двумя классами: объединённый урок — по занятию у каждого класса."""
    db = database(*schedule)
    db["classes"].append({"id": 4, "school_id": 1, "name": "5-Б"})
    return db


def test_combined_classes_make_one_lesson():
    """Объединённые классы в таблице — два занятия с общим учителем и временем,
    но урок у преподавателя один: одна отметка и одни часы."""
    db = _joint_db(entry(1, room=""), entry(2, class_id=4, room="14"), entry(3, start="10:00", end="10:45"))
    lessons = d.expected_lessons(db, MON, MON, [])
    assert [(l["entry_id"], l["class_name"], l["room"]) for l in lessons] == [(1, "5-А, 5-Б", "14"), (3, "5-А", "12")]
    rows = materialize(lessons)
    for row in rows:
        row["status"] = d.CHECKED_IN
    assert d.report(rows, db["users"], MON, MON, at(TUE, "08:00"), 5)["totals"]["hours"] == 1.5


def test_combined_classes_split_by_teacher_or_cancel():
    """Разные учителя или отмена одного класса — уже не общий урок."""
    db = _joint_db(entry(1), entry(2, class_id=4, user_id=3))
    assert len(d.expected_lessons(db, MON, MON, [])) == 2
    db = _joint_db(entry(1), entry(2, class_id=4))
    calendar = [exception(1, d.CANCEL, MON, entry_id=2)]
    lessons = d.expected_lessons(db, MON, MON, calendar)
    assert sorted((l["entry_id"], l["status"]) for l in lessons) == [(1, d.PLANNED), (2, d.CANCELLED)]


def test_combined_lesson_change_today_keeps_one_lesson():
    """Сегодня из объединённого урока убрали первый класс: сегодняшний урок остаётся,
    второй на то же время не появляется; со следующей недели урок держится за оставшееся занятие."""
    now = at(MON, "08:00")
    rows = _apply([], _plan(_joint_db(entry(1), entry(2, class_id=4)), [], now))
    assert len(rows) == 2, "сегодня и через неделю"
    changes = _plan(_joint_db(entry(2, class_id=4)), rows, now)
    assert [(l["entry_id"], l["date"]) for l in changes["insert"]] == [(2, "2026-11-30")]
    assert [x["lesson"]["date"] for x in changes["delete"]] == ["2026-11-30"]
    rows = _apply(rows, changes, next_id=200)
    assert sorted((l["entry_id"], l["date"]) for l in rows) == [(1, "2026-11-23"), (2, "2026-11-30")]


def test_first_run_inserts_only_lessons_not_started():
    db = database(entry(1, start="07:00", end="07:45"), entry(2))
    changes = _plan(db, [], at(MON, "08:00"))
    keys = sorted((l["entry_id"], l["date"]) for l in changes["insert"])
    assert keys == [(1, "2026-11-30"), (2, "2026-11-23"), (2, "2026-11-30")], "07:00 сегодня уже прошёл"
    assert not changes["update"] and not changes["delete"]


def test_reconcile_is_idempotent():
    db = database(entry(1), entry(2, class_id=2, user_id=3))
    now = at(MON, "08:00")
    rows = _apply([], _plan(db, [], now))
    assert _plan(db, rows, now) == {"insert": [], "update": [], "delete": []}


def test_schedule_edit_moves_only_future_lessons():
    """Время занятия сдвинули в день урока — сегодняшний урок остаётся как был."""
    now = at(MON, "08:00")
    rows = _apply([], _plan(database(entry(1)), [], now))
    changes = _plan(database(entry(1, start="10:00", end="10:45")), rows, now)
    assert len(changes["update"]) == 1
    update = changes["update"][0]
    moved = next(l for l in rows if l["id"] == update["id"])
    assert moved["date"] == "2026-11-30" and update["reason"] == "schedule"
    assert update["set"]["starts_at"] == "2026-11-30T05:00:00+00:00"
    assert update["before"]["starts_at"] == "2026-11-30T04:00:00+00:00"


def test_deleted_entry_removes_future_lessons_but_not_today():
    now = at(MON, "08:00")
    rows = _apply([], _plan(database(entry(1)), [], now))
    changes = _plan(database(), rows, now)
    assert [x["lesson"]["date"] for x in changes["delete"]] == ["2026-11-30"]
    assert changes["delete"][0]["reason"] == "schedule"


def test_holiday_added_today_cancels_today_and_removing_it_brings_lesson_back():
    now = at(MON, "08:00")
    db = database(entry(1))
    rows = _apply([], _plan(db, [], now))
    holiday = [exception(1, "holiday", MON, reason="Школа закрыта")]
    changes = _plan(db, rows, now, holiday)
    assert len(changes["update"]) == 1 and changes["update"][0]["reason"] == "calendar"
    assert changes["update"][0]["set"]["status"] == "cancelled"
    rows = _apply(rows, changes)
    revived = _plan(db, rows, now)
    assert revived["update"][0]["set"] == {"status": "planned", "cancel_source": "", "note": ""}


def test_substitute_applies_to_todays_lesson_before_it_starts():
    now = at(MON, "08:00")
    db = database(entry(1))
    rows = _apply([], _plan(db, [], now))
    changes = _plan(db, rows, now, [exception(1, "substitute", MON, entry_id=1, substitute_user_id=3)])
    assert changes["update"][0]["set"]["user_id"] == 3
    started = _plan(db, rows, at(MON, "09:01"), [exception(1, "substitute", MON, entry_id=1, substitute_user_id=3)])
    assert started["update"] == [], "начавшийся урок не переназначается"


def test_untracked_teacher_loses_not_started_lessons_including_today():
    now = at(MON, "08:00")
    db = database(entry(1))
    rows = _apply([], _plan(db, [], now))
    db["users"][1]["checkin_tracked"] = False
    changes = _plan(db, rows, now)
    assert sorted(x["lesson"]["date"] for x in changes["delete"]) == ["2026-11-23", "2026-11-30"]
    assert {x["reason"] for x in changes["delete"]} == {"untracked"}


def test_reconcile_never_touches_decided_started_or_one_off_lessons():
    now = at(MON, "08:00")
    db = database(entry(1), entry(2, start="11:00", end="11:45"))
    rows = _apply([], _plan(db, [], now))
    today_1 = next(l for l in rows if l["entry_id"] == 1 and l["date"] == "2026-11-23")
    today_2 = next(l for l in rows if l["entry_id"] == 2 and l["date"] == "2026-11-23")
    today_1["status"] = "checked_in"
    today_2.update(status="cancelled", cancel_source="admin")
    one_off = dict(today_1, id=500, entry_id=None, status="planned")
    rows.append(one_off)
    holiday = [exception(1, "holiday", MON, MON + timedelta(days=13))]
    changes = _plan(db, rows, now, holiday)
    touched = {u["id"] for u in changes["update"]} | {x["id"] for x in changes["delete"]}
    assert today_1["id"] not in touched and today_2["id"] not in touched and 500 not in touched
    assert len(changes["update"]) == 2, "праздник отменил только уроки следующей недели"

    later = _plan(database(), rows, at(MON, "09:30"))
    assert today_1["id"] not in {x["id"] for x in later["delete"]}


# ── Блоки ─────────────────────────────────────────────────────────────────────

def test_two_lessons_in_a_row_make_one_block():
    first = lesson(1, MON, "09:00", "09:45")
    assert len(d.blocks([first, lesson(2, MON, "10:15", "11:00")], 30)) == 1, "перерыв 30 минут — один блок"
    assert len(d.blocks([first, lesson(2, MON, "10:16", "11:00")], 30)) == 2, "31 минута — два"


def test_blocks_split_by_school_and_teacher():
    lessons = [lesson(1, MON, "09:00", "09:45", school_id=1), lesson(2, MON, "09:50", "10:35", school_id=2),
               lesson(3, MON, "10:40", "11:25", school_id=1)]
    assert [[l["id"] for l in b] for b in d.blocks(lessons, 30)] == [[1], [2], [3]], "A-B-A — три блока"
    other = lesson(4, MON, "09:50", "10:35", user_id=3)
    assert len(d.blocks([lessons[0], other], 30)) == 2, "у разных преподавателей — разные блоки"


def test_block_across_midnight():
    late_evening = lesson(1, MON, "23:00", "23:45")
    after_midnight = lesson(2, TUE, "00:15", "01:00")
    assert len(d.blocks([after_midnight, late_evening], 30)) == 1


# ── Выбор урока для отметки ───────────────────────────────────────────────────

A_LAT, A_LON = SCHOOL_A["lat"], SCHOOL_A["lon"]
B_LAT, B_LON = SCHOOL_B["lat"], SCHOOL_B["lon"]


def test_window_opens_60_minutes_before():
    lessons = [lesson(1, MON, "09:00", "09:45")]
    assert d.pick_block(lessons, at(MON, "07:59"), A_LAT, A_LON, SCHOOLS, SETTINGS) == {"result": "no_lesson"}
    picked = d.pick_block(lessons, at(MON, "08:00"), A_LAT, A_LON, SCHOOLS, SETTINGS)
    assert picked["result"] == "ok" and [l["id"] for l in picked["open"]] == [1]
    assert picked["geo"] == "ok" and picked["distance_m"] == 0 and picked["school_id"] == 1


def test_window_closes_at_end_of_last_lesson():
    lessons = [lesson(1, MON, "09:00", "09:45"), lesson(2, MON, "09:55", "10:40")]
    assert d.pick_block(lessons, at(MON, "10:39"), A_LAT, A_LON, SCHOOLS, SETTINGS)["result"] == "ok"
    assert d.pick_block(lessons, at(MON, "10:40"), A_LAT, A_LON, SCHOOLS, SETTINGS)["result"] == "no_lesson"


def test_two_schools_in_one_day_pick_the_nearest():
    lessons = [lesson(1, MON, "09:00", "09:45", school_id=1), lesson(2, MON, "10:30", "11:15", school_id=2)]
    from_b = d.pick_block(lessons, at(MON, "09:40"), B_LAT, B_LON, SCHOOLS, SETTINGS)
    assert from_b["school_id"] == 2 and from_b["geo"] == "ok"
    from_a = d.pick_block(lessons, at(MON, "09:40"), A_LAT, A_LON, SCHOOLS, SETTINGS)
    assert from_a["school_id"] == 1


def test_far_from_school_and_school_without_coords():
    lessons = [lesson(1, MON, "09:00", "09:45")]
    far = d.pick_block(lessons, at(MON, "08:55"), A_LAT + 2000 * METRE, A_LON, SCHOOLS, SETTINGS)
    assert far["result"] == "ok" and far["geo"] == "far" and far["distance_m"] == 2000
    no_coords = d.pick_block([lesson(1, MON, "09:00", "09:45", school_id=3)], at(MON, "08:55"),
                             A_LAT, A_LON, SCHOOLS, SETTINGS)
    assert no_coords["geo"] == "no_coords" and no_coords["distance_m"] is None


def test_already_checked_in_and_cancelled():
    done = [lesson(1, MON, "09:00", "09:45", status="checked_in")]
    assert d.pick_block(done, at(MON, "09:10"), A_LAT, A_LON, SCHOOLS, SETTINGS)["result"] == "already"
    cancelled = [lesson(1, MON, "09:00", "09:45", status="cancelled")]
    assert d.pick_block(cancelled, at(MON, "09:10"), A_LAT, A_LON, SCHOOLS, SETTINGS)["result"] == "no_lesson"


def test_done_school_nearby_beats_open_school_far_away():
    """Отметился в школе A и прислал точку ещё раз, а у школы B уже открылось окно:
    отвечаем «уже отмечено», а не записываем B «далеко от школы»."""
    lessons = [lesson(1, MON, "09:00", "09:45", school_id=1, status="checked_in"),
               lesson(2, MON, "10:30", "11:15", school_id=2)]
    assert d.pick_block(lessons, at(MON, "09:40"), A_LAT, A_LON, SCHOOLS, SETTINGS)["result"] == "already"


def test_block_partly_done_gives_only_open_lessons():
    lessons = [lesson(1, MON, "09:00", "09:45", status="checked_in"), lesson(2, MON, "09:55", "10:40")]
    picked = d.pick_block(lessons, at(MON, "09:50"), A_LAT, A_LON, SCHOOLS, SETTINGS)
    assert [l["id"] for l in picked["open"]] == [2] and len(picked["block"]) == 2


def test_midnight_checkin_in_utc_and_tashkent():
    """Урок вторника в 00:30: точка в 23:45 понедельника (18:45 UTC) и в 00:40 (19:40 UTC)."""
    lessons = [lesson(1, TUE, "00:30", "01:15")]
    evening = datetime(2026, 11, 23, 18, 45, tzinfo=timezone.utc)
    picked = d.pick_block(lessons, evening, A_LAT, A_LON, SCHOOLS, SETTINGS)
    assert picked["result"] == "ok"
    assert d.lesson_outcome(picked["open"][0], evening, "ok", True, SETTINGS)["status"] == "checked_in"
    after = datetime(2026, 11, 23, 19, 40, tzinfo=timezone.utc)
    outcome = d.lesson_outcome(lessons[0], after, "ok", True, SETTINGS)
    assert outcome == {"status": "late", "late_minutes": 10, "flag": ""}


# ── Исход отметки ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hhmm,status,late", [
    ("08:30", "checked_in", 0), ("09:00", "checked_in", 0), ("09:05", "checked_in", 5),
    ("09:06", "late", 6), ("09:44", "late", 44),
])
def test_lateness_and_grace(hhmm, status, late):
    outcome = d.lesson_outcome(lesson(1, MON, "09:00", "09:45"), at(MON, hhmm), "ok", True, SETTINGS)
    assert (outcome["status"], outcome["late_minutes"], outcome["flag"]) == (status, late, "")


def test_checkin_after_lesson_ended_goes_to_review():
    outcome = d.lesson_outcome(lesson(1, MON, "09:00", "09:45"), at(MON, "09:45"), "ok", True, SETTINGS)
    assert outcome == {"status": "needs_review", "late_minutes": 45, "flag": "ended"}


@pytest.mark.parametrize("geo,video_ok,flag", [
    ("far", True, "far"), ("no_coords", True, "no_coords"), ("ok", False, "no_video"), ("far", False, "no_video,far"),
])
def test_disputed_checkins_go_to_review_with_lateness_kept(geo, video_ok, flag):
    outcome = d.lesson_outcome(lesson(1, MON, "09:00", "09:45"), at(MON, "09:12"), geo, video_ok, SETTINGS)
    assert outcome == {"status": "needs_review", "late_minutes": 12, "flag": flag}


def test_checkin_after_no_checkin_is_late_and_after_reject_is_review():
    no_checkin = lesson(1, MON, "09:00", "09:45", status="no_checkin")
    assert d.lesson_outcome(no_checkin, at(MON, "09:20"), "ok", True, SETTINGS)["status"] == "late"
    rejected = lesson(1, MON, "09:00", "09:45", status="rejected")
    outcome = d.lesson_outcome(rejected, at(MON, "09:20"), "ok", True, SETTINGS)
    assert outcome["status"] == "needs_review" and outcome["flag"] == "after_reject"


def test_one_checkin_closes_the_whole_block():
    block = [lesson(1, MON, "09:00", "09:45"), lesson(2, MON, "09:55", "10:40")]
    moment = at(MON, "09:20")
    picked = d.pick_block(block, moment, A_LAT, A_LON, SCHOOLS, SETTINGS)
    outcomes = [d.lesson_outcome(l, moment, picked["geo"], True, SETTINGS) for l in picked["open"]]
    assert [(o["status"], o["late_minutes"]) for o in outcomes] == [("late", 20), ("checked_in", 0)]


def test_grace_comes_from_settings():
    outcome = d.lesson_outcome(lesson(1, MON, "09:00", "09:45"), at(MON, "09:08"), "ok", True,
                               dict(SETTINGS, grace_minutes=10))
    assert outcome["status"] == "checked_in"


# ── Нет отметки → очередь ─────────────────────────────────────────────────────

def test_due_for_review():
    planned = lesson(1, MON, "09:00", "09:45")
    assert not d.due_for_review(planned, at(MON, "09:14"), 15)
    assert d.due_for_review(planned, at(MON, "09:15"), 15)
    assert not d.due_for_review(planned, at(MON, "09:15"), 15, pending=True), "ждём кругляшок — не торопимся"
    assert not d.due_for_review(dict(planned, status="late"), at(MON, "10:00"), 15)
    assert not d.due_for_review(dict(planned, status="cancelled"), at(MON, "10:00"), 15)


# ── Проверка сообщений ────────────────────────────────────────────────────────

def test_location_problems():
    assert d.location_problem(False, False, None) is None
    assert d.location_problem(True, False, None) == "forwarded"
    assert d.location_problem(False, True, None) == "venue"
    assert d.location_problem(False, False, 900) == "live"


def test_video_problems():
    pending = {"created_at": clock.iso(at(MON, "08:55"))}
    ok = at(MON, "08:57")
    assert d.video_problem("video_note", False, ok, pending, 120, False) is None
    assert d.video_problem("video_note", False, ok, None, 120, False) == "no_pending"
    assert d.video_problem("video", False, ok, pending, 120, False) == "not_video_note"
    assert d.video_problem("video_note", True, ok, pending, 120, False) == "forwarded"
    assert d.video_problem("video_note", False, ok, pending, 120, True) == "duplicate"
    assert d.video_problem("video_note", False, at(MON, "08:58"), pending, 120, False) == "late", "через 3 минуты"


# ── Часы и деньги ─────────────────────────────────────────────────────────────

def test_counted_minutes_and_hours():
    assert d.counted_minutes(lesson(1, MON, "09:00", "09:45", status="checked_in")) == 45
    assert d.hours(45) == 0.75
    fifty = lesson(1, MON, "09:00", "09:50", status="late")
    assert d.counted_minutes(fifty) == 50 and d.hours(50) == 0.83, "опоздание часы не уменьшает"
    assert d.counted_minutes(lesson(1, MON, "09:00", "09:45", status="approved")) == 45
    for status in ("planned", "no_checkin", "needs_review", "rejected", "cancelled"):
        assert d.counted_minutes(lesson(1, MON, "09:00", "09:45", status=status)) == 0, status
    override = lesson(1, MON, "09:00", "09:45", status="rejected", hours_override=1.5)
    assert d.counted_minutes(override) == 90, "ручная правка главнее статуса"


def test_money():
    assert d.money(50, 90000) == 75000
    assert d.money(45, None) is None
    assert d.money(1, 100) == 2, "1,67 сума — округление до целых"
    assert d.money(0, 90000) == 0


# ── Отчёт ─────────────────────────────────────────────────────────────────────

def test_monthly_report_by_teacher_and_school():
    users = database()["users"]
    lessons = [
        lesson(1, MON, "09:00", "09:45", status="checked_in", late_minutes=2),
        lesson(2, MON, "10:00", "10:50", status="late", late_minutes=12),
        lesson(3, TUE, "09:00", "09:45", status="approved", late_minutes=30),
        lesson(4, TUE, "10:00", "10:45", status="rejected"),
        lesson(5, date(2026, 11, 25), "09:00", "09:45", status="no_checkin"),
        lesson(6, date(2026, 11, 26), "09:00", "09:45", status="cancelled"),
        lesson(7, date(2026, 11, 27), "09:00", "09:45"),
        lesson(8, MON, "12:00", "12:45", school_id=2, user_id=3, teacher_name="Юсупов Б.", status="checked_in"),
        lesson(9, date(2026, 12, 1), "09:00", "09:45", status="checked_in"),    # уже декабрь
    ]
    now = at(date(2026, 11, 26), "12:00")
    rep = d.report(lessons, users, date(2026, 11, 1), date(2026, 11, 30), now, 5)
    karimova, yusupov = rep["teachers"]
    assert karimova["name"] == "Каримова Д." and yusupov["name"] == "Юсупов Б."
    assert {k: karimova[k] for k in ("planned", "confirmed", "late", "late_minutes", "rejected",
                                     "pending", "ahead", "cancelled")} == \
        {"planned": 6, "confirmed": 3, "late": 2, "late_minutes": 42, "rejected": 1,
         "pending": 1, "ahead": 1, "cancelled": 1}
    assert karimova["minutes"] == 45 + 50 + 45 and karimova["hours"] == 2.33
    assert karimova["amount"] == 210000, "140 минут × 90 000 в час"
    assert yusupov["amount"] is None and yusupov["hours"] == 0.75, "без ставки — только часы"

    school_1, school_2 = rep["schools"]
    assert (school_1["name"], school_1["lessons"], school_1["minutes"]) == ("Школа №1", 3, 140)
    assert school_2["teachers"] == [{"user_id": 3, "name": "Юсупов Б.", "minutes": 45, "hours": 0.75}]
    assert rep["totals"]["minutes"] == 185 and rep["totals"]["amount"] == 210000
    assert rep["totals"]["lessons"] == 7


def test_planned_lesson_already_started_counts_as_pending():
    rep = d.report([lesson(1, MON, "09:00", "09:45")], [], MON, MON, at(MON, "09:05"), 5)
    assert rep["teachers"][0]["pending"] == 1 and rep["teachers"][0]["ahead"] == 0


# ── Журнал звёзд ──────────────────────────────────────────────────────────────

def test_star_days_only_lessons_with_awards():
    db = database()
    db["lessons"] = [{"class_id": 1, "date": "2026-11-23", "awards": {"101": 3}},
                     {"class_id": 2, "date": "2026-11-23", "awards": {}},
                     {"class_id": 1, "date": "2026-11-24T10:00", "awards": {"102": 1}}]
    assert d.star_days(db) == {(1, "2026-11-23"), (1, "2026-11-24")}
