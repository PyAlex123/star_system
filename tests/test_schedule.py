"""Расписание занятий: права, пересечения, каскад, расписание в кабинете ученика."""
from datetime import date

from fastapi.testclient import TestClient

from backend import stats
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
