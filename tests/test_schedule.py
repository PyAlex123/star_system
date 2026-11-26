"""Расписание занятий: права, пересечения, каскад, расписание в кабинете ученика."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend import db, stats
from backend.main import app
from conftest import load_db


def add(client, **kw):
    body = {"class_id": 1, "weekdays": [1, 3], "start": "14:00", "end": "14:45", "room": "12"}
    body.update(kw)
    return client.post("/api/schedule", json=body)


def test_add_on_several_days(teacher):
    r = add(teacher)
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert [e["weekday"] for e in created] == [1, 3]
    schedule = teacher.get("/api/state").json()["schedule"]
    assert len(schedule) == 2 and schedule[0]["room"] == "12"
    assert schedule[0]["user_id"] is not None, "учитель по умолчанию — кто добавил"


def test_overlap_rejected(teacher):
    add(teacher, weekdays=[2])
    r = add(teacher, weekdays=[2], start="14:30", end="15:15")
    assert r.status_code == 400 and "уже есть занятие" in r.json()["detail"]
    assert add(teacher, weekdays=[2], start="14:45", end="15:30").status_code == 200, "встык — можно"


def test_bad_time(teacher):
    assert add(teacher, start="15:00", end="14:00").status_code == 400
    assert add(teacher, start="25:00", end="26:00").status_code == 422
    assert add(teacher, weekdays=[8]).status_code == 422


def test_teacher_of_other_school_404(school_teacher, teacher):
    r = add(school_teacher, class_id=school_teacher.other["class"])
    assert r.status_code == 404
    entry = add(teacher, class_id=school_teacher.other["class"]).json()["created"][0]
    assert school_teacher.delete(f"/api/schedule/{entry['id']}").status_code == 404
    assert school_teacher.get("/api/state").json()["schedule"] == []


def test_update_and_delete(teacher):
    entry = add(teacher, weekdays=[4]).json()["created"][0]
    r = teacher.patch(f"/api/schedule/{entry['id']}", json={"start": "10:00", "end": "10:45", "room": "Актовый"})
    assert r.status_code == 200 and r.json()["entry"]["room"] == "Актовый"
    assert teacher.delete(f"/api/schedule/{entry['id']}").status_code == 200
    assert teacher.get("/api/state").json()["schedule"] == []


def test_class_delete_removes_schedule(teacher):
    add(teacher, class_id=3)
    teacher.delete("/api/classes/3")
    assert load_db()["schedule"] == []


def test_deleted_teacher_leaves_lessons_without_teacher(teacher):
    r = teacher.post("/api/users", json={"login": "petrova", "name": "Петрова", "school_ids": [1]})
    uid = r.json()["user"]["id"]
    add(teacher, user_id=uid)
    teacher.delete(f"/api/users/{uid}")
    assert all(e["user_id"] is None for e in load_db()["schedule"])


def test_teacher_must_work_in_school(teacher, school_teacher):
    stranger = teacher.post("/api/users", json={"login": "chuzhoi", "name": "Чужой",
                                                "school_ids": [school_teacher.other["school"]]}).json()["user"]
    assert add(teacher, user_id=stranger["id"]).status_code == 400


def test_student_sees_class_schedule(teacher, student_headers):
    add(teacher, weekdays=[5], start="09:00", end="09:45")
    add(teacher, class_id=2, weekdays=[5])
    me = TestClient(app).get("/api/me", headers=student_headers(101)).json()
    assert [(e["weekday"], e["start"]) for e in me["schedule"]] == [(5, "09:00")]
    assert me["schedule"][0]["teacher"] == "Администратор"


def test_planned_lessons_count(teacher):
    add(teacher, weekdays=[1, 3])                      # пн и ср
    database = load_db()
    for e in database["schedule"]:
        e["created_at"] = "2026-11-02"                 # понедельник
    # 2–15 ноября: две недели → 4 занятия
    assert stats.planned_lessons(database, [1], date(2026, 11, 2), date(2026, 11, 15)) == 4
    assert stats.planned_lessons(database, [1], date(2026, 11, 3), date(2026, 11, 4)) == 1
    assert stats.planned_lessons(database, [2], date(2026, 11, 2), date(2026, 11, 15)) == 0


def test_held_counts_only_scheduled_days(teacher):
    """«Проведено из запланированных» не превышает план: уроки до появления расписания
    и уроки в другие дни недели в «проведено» не попадают."""
    add(teacher, weekdays=[1, 3])                      # пн и ср
    database = load_db()
    for e in database["schedule"]:
        e["created_at"] = "2026-11-02"
    database["lessons"] = [
        {"id": 1, "class_id": 1, "date": "2026-10-26"},  # до расписания
        {"id": 2, "class_id": 1, "date": "2026-11-02"},  # пн — по расписанию
        {"id": 3, "class_id": 1, "date": "2026-11-06"},  # пт — вне расписания
        {"id": 4, "class_id": 1, "date": "2026-11-11"},  # ср — по расписанию
        {"id": 5, "class_id": 2, "date": "2026-11-09"},  # другой класс
    ]
    plan = stats.schedule_plan(database, [1], date(2026, 10, 1), date(2026, 11, 15))
    assert plan == {"planned": 4, "held": 2}


# ── Таблица: время уроков школы и ячейки ─────────────────────────────────────

def cell(client, weekday=1, lesson=1, class_id=1, **kw):
    return client.put("/api/schedule/cell", json={"school_id": 1, "weekday": weekday, "lesson": lesson,
                                                 "class_id": class_id, **kw})


def bells(client, items, days=5, school_id=1):
    return client.put(f"/api/schedule/bells/{school_id}",
                      json={"bells": [{"start": s, "end": e} for s, e in items], "days": days})


def test_school_has_default_bells(teacher):
    school = teacher.get("/api/state").json()["schools"][0]
    assert school["days"] == 5
    assert school["bells"][:2] == [{"start": "08:00", "end": "08:45"}, {"start": "08:55", "end": "09:40"}]
    assert len(school["bells"]) == 7
    new = teacher.post("/api/schools", json={"name": "Новая"}).json()["school"]
    assert new["bells"] == db.default_bells() and new["days"] == 5


def test_cell_puts_class_at_bell_time(teacher):
    r = cell(teacher, weekday=2, lesson=3, class_id=2)
    assert r.status_code == 200, r.text
    entry = r.json()["entry"]
    assert (entry["weekday"], entry["lesson"], entry["start"], entry["end"]) == (2, 3, "09:50", "10:35")
    assert entry["user_id"] is not None, "учитель по умолчанию — кто поставил"
    assert load_db()["schedule"] == [entry]


def test_cell_holds_one_class(teacher):
    cell(teacher, class_id=1)
    r = cell(teacher, class_id=2)
    assert r.status_code == 200
    schedule = load_db()["schedule"]
    assert [(e["class_id"], e["lesson"]) for e in schedule] == [(2, 1)], "класс в ячейке заменён"


def cell_classes(weekday=1, lesson=1):
    return sorted(e["class_id"] for e in load_db()["schedule"]
                  if e["weekday"] == weekday and e.get("lesson") == lesson)


def test_cell_combines_classes(teacher):
    third = teacher.post("/api/classes", json={"school_id": 1, "name": "7-В"}).json()["class"]["id"]
    cell(teacher, class_id=1)
    teacher.patch(f"/api/schedule/{load_db()['schedule'][0]['id']}", json={"room": "12"})
    first_id = load_db()["schedule"][0]["id"]
    r = cell(teacher, class_ids=[1, third])
    assert r.status_code == 200, r.text
    assert [e["class_id"] for e in r.json()["entries"]] == [1, third]
    assert cell_classes() == [1, third]
    schedule = load_db()["schedule"]
    assert first_id in {e["id"] for e in schedule}, "оставшийся класс сохраняет свою запись"
    assert {e["room"] for e in schedule} == {"12"}, "присоединённый класс — в кабинете урока"
    assert {(e["start"], e["end"]) for e in schedule} == {("08:00", "08:45")}

    assert cell(teacher, class_ids=[third, 1]).json().get("unchanged") is True
    cell(teacher, class_ids=[2, third])
    assert cell_classes() == sorted([2, third]), "5-А убран из урока, 6-А добавлен"
    assert cell(teacher, class_ids=[]).json()["entries"] == []
    assert cell_classes() == []


def test_combined_lesson_shares_teacher_and_room(teacher):
    uid = teacher.post("/api/users", json={"login": "petrova", "name": "Петрова", "school_ids": [1]}).json()["user"]["id"]
    cell(teacher, class_ids=[1, 2])
    assert cell(teacher, class_ids=[1, 2], user_id=uid).status_code == 200
    assert {e["user_id"] for e in load_db()["schedule"]} == {uid}, "учитель общий"
    first = load_db()["schedule"][0]
    teacher.patch(f"/api/schedule/{first['id']}", json={"room": "Актовый"})
    assert {e["room"] for e in load_db()["schedule"]} == {"Актовый"}, "кабинет общий"
    # Класс увели в другое время — он больше не в уроке, остальные не меняются
    teacher.patch(f"/api/schedule/{first['id']}", json={"start": "15:00", "end": "15:45", "room": "1"})
    rooms = {e["class_id"]: e["room"] for e in load_db()["schedule"]}
    assert rooms[first["class_id"]] == "1" and list(rooms.values()).count("Актовый") == 1


def test_combined_cell_checks(teacher, school_teacher):
    add(teacher, class_id=2, weekdays=[1], start="08:30", end="09:00")
    r = cell(teacher, class_ids=[1, 2])
    assert r.status_code == 400 and "уже есть занятие 08:30–09:00" in r.json()["detail"]
    assert cell_classes() == [], "ничего не сохранилось"
    assert cell(teacher, class_ids=[1, school_teacher.other["class"]]).status_code == 404
    assert cell(teacher, class_ids=[1, 1]).json()["entries"][0]["class_id"] == 1
    assert cell_classes() == [1], "повтор класса не удваивает занятие"


def test_cell_teacher_kept_and_changed(teacher):
    uid = teacher.post("/api/users", json={"login": "petrova", "name": "Петрова", "school_ids": [1]}).json()["user"]["id"]
    cell(teacher, class_id=1, user_id=uid)
    assert cell(teacher, class_id=2).json()["entry"]["user_id"] == uid, "новый класс — прежний учитель ячейки"
    assert cell(teacher, class_id=2, user_id=None).json()["entry"]["user_id"] is None
    assert cell(teacher, class_id=2, user_id=None).json().get("unchanged") is True


def test_cell_clear(teacher):
    cell(teacher)
    r = cell(teacher, class_id=None)
    assert r.status_code == 200 and r.json()["entry"] is None
    assert load_db()["schedule"] == []
    assert cell(teacher, class_id=None).json().get("unchanged") is True


def test_cell_checks(teacher, school_teacher):
    assert cell(teacher, lesson=8).status_code == 400, "у школы 7 уроков"
    assert cell(teacher, lesson=13).status_code == 422
    assert cell(teacher, class_id=school_teacher.other["class"]).status_code == 404, "класс другой школы"
    assert cell(school_teacher, class_id=1).status_code == 200
    r = school_teacher.put("/api/schedule/cell", json={"school_id": school_teacher.other["school"], "weekday": 1,
                                                       "lesson": 1, "class_id": school_teacher.other["class"]})
    assert r.status_code == 404, "чужая школа — как несуществующая"


def test_cell_clashes_with_lesson_at_other_time(teacher):
    add(teacher, weekdays=[1], start="08:30", end="09:00")             # «в другое время»
    r = cell(teacher, weekday=1, lesson=1, class_id=1)                 # 08:00–08:45
    assert r.status_code == 400 and "уже есть занятие 08:30–09:00" in r.json()["detail"]


def test_form_entry_lands_in_grid_or_aside(teacher):
    on_bell = add(teacher, weekdays=[1], start="08:55", end="09:40").json()["created"][0]
    aside = add(teacher, weekdays=[1], start="14:00", end="14:45").json()["created"][0]
    assert on_bell["lesson"] == 2 and aside["lesson"] is None
    joined = add(teacher, class_id=2, weekdays=[1], start="08:55", end="09:40").json()["created"][0]
    assert joined["lesson"] == 2, "второй класс в то же время — объединённый урок"
    moved = teacher.patch(f"/api/schedule/{aside['id']}", json={"start": "08:00", "end": "08:45"}).json()["entry"]
    assert moved["lesson"] == 1, "новое время совпало с уроком — встало в таблицу"
    gone = teacher.patch(f"/api/schedule/{on_bell['id']}", json={"start": "15:00", "end": "15:45"}).json()["entry"]
    assert gone["lesson"] is None


def test_patch_rejects_overlap(teacher):
    add(teacher, weekdays=[3], start="14:00", end="14:45")
    other = add(teacher, weekdays=[3], start="15:00", end="15:45").json()["created"][0]
    r = teacher.patch(f"/api/schedule/{other['id']}", json={"start": "14:30", "end": "15:15"})
    assert r.status_code == 400


def test_bells_move_lessons(teacher, student_headers):
    cell(teacher, weekday=5, lesson=2, class_id=1)
    r = bells(teacher, [("09:00", "09:40"), ("09:50", "10:30"), ("10:40", "11:20")], days=6)
    assert r.status_code == 200, r.text
    assert r.json()["days"] == 6
    entry = load_db()["schedule"][0]
    assert (entry["lesson"], entry["start"], entry["end"]) == (2, "09:50", "10:30")
    me = TestClient(app).get("/api/me", headers=student_headers(101)).json()
    assert me["schedule"][0]["start"] == "09:50", "ученик видит новое время"


def test_bells_link_matching_lessons(teacher):
    aside = add(teacher, weekdays=[2], start="14:00", end="14:45").json()["created"][0]
    assert aside["lesson"] is None
    bells(teacher, [("13:10", "13:55"), ("14:00", "14:45")])
    assert load_db()["schedule"][0]["lesson"] == 2, "время совпало с новым уроком — занятие в таблице"


def test_bells_checks(teacher, school_teacher):
    assert bells(teacher, [("09:00", "08:00")]).status_code == 400
    r = bells(teacher, [("08:00", "08:45"), ("08:30", "09:15")])
    assert r.status_code == 400 and "2-й урок начинается раньше" in r.json()["detail"]
    assert bells(teacher, []).status_code == 422
    assert bells(teacher, [("08:00", "08:45")] * 13).status_code == 422
    assert bells(teacher, [("08:00", "08:45")], days=7).status_code == 422
    cell(teacher, weekday=1, lesson=3, class_id=1)
    r = bells(teacher, [("08:00", "08:45"), ("08:55", "09:40")])
    assert r.status_code == 400 and "5-А" in r.json()["detail"], "нельзя убрать урок, на котором стоят занятия"
    assert bells(school_teacher, [("08:00", "08:45")], school_id=school_teacher.other["school"]).status_code == 404


def test_bells_refuse_overlap_with_other_time(teacher):
    cell(teacher, weekday=1, lesson=1, class_id=1)                          # 08:00–08:45
    add(teacher, weekdays=[1], start="09:00", end="09:30")
    r = bells(teacher, [("08:30", "09:15")])
    assert r.status_code == 400 and "пересечётся" in r.json()["detail"]
    assert load_db()["schedule"][0]["start"] == "08:00", "ничего не поменялось"


# ── Таблица и срок действия занятия (отметки преподавателей) ────────────────

def _monday(weeks=1):
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=weeks)


def test_cell_on_future_week_changes_from_that_date(teacher):
    """Правка ячейки на будущей неделе действует с её даты: прежний класс стоит до неё,
    новый — с неё; эта неделя не меняется."""
    monday = _monday(1)
    first = cell(teacher, class_id=1).json()["entry"]
    r = cell(teacher, class_id=2, date=monday.isoformat())
    assert r.status_code == 200, r.text
    second = r.json()["entry"]
    assert second["valid_from"] == monday.isoformat() and second["lesson"] == 1
    saved = {e["id"]: e for e in load_db()["schedule"]}
    assert saved[first["id"]]["valid_to"] == (monday - timedelta(days=1)).isoformat(), "5-А — до будущей недели"
    assert cell(teacher, class_id=1).json().get("unchanged") is True, "на этой неделе в ячейке по-прежнему 5-А"
    assert cell(teacher, class_id=2, date=monday.isoformat()).json().get("unchanged") is True

    assert cell(teacher, class_id=None, date=monday.isoformat()).status_code == 200
    assert [e["id"] for e in load_db()["schedule"]] == [first["id"]], "убранный с будущей недели класс удалён"


def test_cell_future_week_keeps_current_class_until_then(teacher):
    """Убрать урок на будущей неделе — занятие заканчивается накануне, а не исчезает."""
    monday = _monday(2)
    entry = cell(teacher, class_id=1).json()["entry"]
    assert cell(teacher, class_id=None, date=monday.isoformat()).status_code == 200
    saved = load_db()["schedule"]
    assert [(e["id"], e["valid_to"]) for e in saved] == [(entry["id"], (monday - timedelta(days=1)).isoformat())]


def test_cell_date_checks(teacher):
    monday = _monday(1)
    r = cell(teacher, weekday=2, class_id=1, date=monday.isoformat())
    assert r.status_code == 400 and "днём недели" in r.json()["detail"]
    assert cell(teacher, class_id=1, date="31.12.2026").status_code == 422
    past = cell(teacher, class_id=1, date=_monday(-2).isoformat()).json()["entry"]
    assert past["valid_from"] is None, "прошлую неделю не переписать — правка действует с сегодня"


def test_closed_entry_frees_the_cell(teacher):
    """Закончившееся занятие класса не мешает поставить тот же класс в то же время позже."""
    monday = _monday(1)
    first = cell(teacher, class_id=1).json()["entry"]
    sunday = (monday - timedelta(days=1)).isoformat()
    assert teacher.patch(f"/api/schedule/{first['id']}", json={"valid_to": sunday}).status_code == 200
    r = cell(teacher, class_id=1, date=monday.isoformat())
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["id"] != first["id"] and r.json()["entry"]["valid_from"] == monday.isoformat()
    r = add(teacher, class_id=1, weekdays=[1], start="08:00", end="08:45")
    assert r.status_code == 400 and "уже есть занятие" in r.json()["detail"], "без срока — пересекается с обоими"


def test_bells_ignore_ended_entries(teacher):
    """Закончившееся занятие на уроке, которого больше нет, — история: уходит «в другое время»."""
    entry = cell(teacher, weekday=1, lesson=3, class_id=1).json()["entry"]
    old = (date.today() - timedelta(days=30)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert teacher.patch(f"/api/schedule/{entry['id']}",
                         json={"valid_from": old, "valid_to": yesterday}).status_code == 200
    r = bells(teacher, [("08:00", "08:45"), ("08:55", "09:40")])
    assert r.status_code == 200, r.text
    saved = load_db()["schedule"][0]
    assert saved["lesson"] is None and saved["start"] == "09:50", "время прошлых уроков не меняется"
