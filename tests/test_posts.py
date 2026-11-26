"""Должности в школах: у сотрудника в каждой школе свои — директор, завуч начальной / средней
школы, учитель (можно несколько). Права — по должности в той школе, где он сейчас."""
import json

import pytest

from backend import config, db
from conftest import load_db, panel_client

LOGIN, PASSWORD = "alisher", "alisher-2026"


@pytest.fixture
def two_schools(teacher):
    """Вторая школа с классом и учеником; сотрудник — учитель в школе 1 и директор в школе 2."""
    second = teacher.post("/api/schools", json={"name": "Школа на Чиланзаре"}).json()["school"]["id"]
    cls = teacher.post("/api/classes", json={"school_id": second, "name": "9-А"}).json()["class"]["id"]
    student = teacher.post("/api/students", json={"class_id": cls, "name": "Каримов Азиз"}).json()["student"]["id"]
    r = teacher.post("/api/users", json={"login": LOGIN, "name": "Алишер Р.", "password": PASSWORD,
                                         "posts": {"1": ["teacher"], str(second): ["director"]}})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["posts"] == {"1": ["teacher"], str(second): ["director"]}
    assert user["school_ids"] == [1, second] and user["role"] == "teacher"
    client = panel_client(LOGIN, PASSWORD)
    client.ids = {"school": second, "class": cls, "student": student, "user": user["id"]}
    return client


def test_journal_only_where_teacher(two_schools):
    ids = two_schools.ids
    assert two_schools.post("/api/students", json={"class_id": 1, "name": "Новый Ученик"}).status_code == 200
    r = two_schools.post("/api/students", json={"class_id": ids["class"], "name": "Ещё Один"})
    assert r.status_code == 403 and "не ведёте уроки" in r.json()["detail"]
    r = two_schools.post("/api/lessons/complete", json={"class_id": ids["class"], "date": "2026-11-26", "awards": [
        {"student_id": ids["student"], "stars": 2}]})
    assert r.status_code == 403, "звёзды — только учителю этой школы"
    assert two_schools.post("/api/classes", json={"school_id": ids["school"], "name": "9-Б"}).status_code == 403


def test_prizes_and_cards_only_where_management(two_schools):
    ids = two_schools.ids
    r = two_schools.post("/api/prizes", json={"name": "Грамота директора", "cost": 15, "school_id": ids["school"]})
    assert r.status_code == 200 and r.json()["prize"]["school_id"] == ids["school"]
    assert two_schools.post("/api/prizes", json={"name": "Туда", "cost": 5, "school_id": 1}).status_code == 404, \
        "в школе 1 он учитель — призы школы добавляет руководство"
    r = two_schools.post("/api/prizes", json={"name": "Без школы", "cost": 5})
    assert r.status_code == 200 and r.json()["prize"]["school_id"] == ids["school"], "руководит одной школой — приз её"
    page = two_schools.get(f"/cards/{ids['class']}")
    assert page.status_code == 200 and "К классам" in page.text, "директор печатает карточки, журнала нет"
    assert "В журнал" in two_schools.get("/cards/1").text


def test_state_hides_student_logins_where_not_teacher(two_schools):
    state = two_schools.get("/api/state").json()
    by_id = {s["id"]: s for s in state["students"]}
    assert "login" in by_id[101], "в школе, где он учитель, — данные для входа есть"
    assert "login" not in by_id[two_schools.ids["student"]] and "token" not in by_id[two_schools.ids["student"]]
    assert state["me"]["posts"] == {"1": ["teacher"], str(two_schools.ids["school"]): ["director"]}


def test_schedule_default_teacher_by_post(two_schools, teacher):
    ids = two_schools.ids
    mine = two_schools.put("/api/schedule/cell", json={"school_id": 1, "weekday": 1, "lesson": 1, "class_id": 1})
    assert mine.json()["entry"]["user_id"] == ids["user"], "учитель в школе 1 — урок его"
    there = two_schools.put("/api/schedule/cell", json={"school_id": ids["school"], "weekday": 1, "lesson": 1,
                                                       "class_id": ids["class"]})
    assert there.status_code == 200 and there.json()["entry"]["user_id"] is None, "директор уроков не ведёт"
    r = teacher.put("/api/schedule/cell", json={"school_id": ids["school"], "weekday": 2, "lesson": 1,
                                                "class_id": ids["class"], "user_id": ids["user"]})
    assert r.status_code == 400 and "не учитель" in r.json()["detail"]


def test_head_who_teaches_has_both(teacher):
    r = teacher.post("/api/users", json={"login": "zavuch", "name": "Завуч Т.", "password": PASSWORD,
                                         "posts": {"1": ["teacher", "head_primary"]}})
    assert r.json()["user"]["posts"] == {"1": ["head_primary", "teacher"]}, "порядок — как в списке должностей"
    zavuch = panel_client("zavuch", PASSWORD)
    assert zavuch.post("/api/students", json={"class_id": 1, "name": "Ученик Завуча"}).status_code == 200
    assert zavuch.post("/api/prizes", json={"name": "От завуча", "cost": 3}).status_code == 200


def test_school_staff_endpoint(two_schools, teacher):
    ids = two_schools.ids
    teacher.put("/api/schedule/cell", json={"school_id": 1, "weekday": 3, "lesson": 2, "class_id": 1,
                                            "user_id": ids["user"]})
    # Только суперадмин
    assert two_schools.put(f"/api/schools/1/staff/{ids['user']}", json={"posts": ["director"]}).status_code == 403
    # В школе 1 — уже не учитель, а завуч: его занятия там остаются без учителя
    r = teacher.put(f"/api/schools/1/staff/{ids['user']}", json={"posts": ["head_secondary"]})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["posts"]["1"] == ["head_secondary"] and r.json()["user"]["role"] == "school_admin"
    saved = load_db()
    assert all(e["user_id"] != ids["user"] for e in saved["schedule"])
    assert "больше не учитель в школе" in saved["schedule_log"][0]["text"]
    assert two_schools.post("/api/students", json={"class_id": 1, "name": "Уже нельзя"}).status_code == 403
    # Убрать из школы — пустой список
    r = teacher.put(f"/api/schools/{ids['school']}/staff/{ids['user']}", json={"posts": []})
    assert r.json()["user"]["school_ids"] == [1] and str(ids["school"]) not in r.json()["user"]["posts"]
    assert two_schools.get(f"/cards/{ids['class']}").status_code == 404
    # Проверки
    assert teacher.put(f"/api/schools/1/staff/{ids['user']}", json={"posts": ["boss"]}).status_code == 422
    assert teacher.put("/api/schools/1/staff/1", json={"posts": ["teacher"]}).status_code == 400, "суперадмин"
    assert teacher.put("/api/schools/999/staff/2", json={"posts": ["teacher"]}).status_code == 404


def test_legacy_role_and_school_ids_still_work(teacher):
    r = teacher.post("/api/users", json={"login": "staraya", "name": "По-старому", "school_ids": [1],
                                         "role": "school_admin", "password": PASSWORD})
    user = r.json()["user"]
    assert user["posts"] == {"1": ["director"]} and user["role"] == "school_admin"
    second = teacher.post("/api/schools", json={"name": "Вторая"}).json()["school"]["id"]
    r = teacher.patch(f"/api/users/{user['id']}", json={"school_ids": [1, second]})
    assert r.json()["user"]["posts"] == {"1": ["director"], str(second): ["director"]}
    r = teacher.patch(f"/api/users/{user['id']}", json={"role": "teacher"})
    assert r.json()["user"]["posts"] == {"1": ["teacher"], str(second): ["teacher"]}


def test_deleting_school_drops_posts(two_schools, teacher):
    ids = two_schools.ids
    assert teacher.delete(f"/api/schools/{ids['school']}").status_code == 200
    user = db.find_user(load_db(), ids["user"])
    assert user["posts"] == {"1": ["teacher"]} and user["school_ids"] == [1]


def test_v10_users_get_posts():
    data = json.loads(json.dumps(db.DEFAULT_DB))
    data["version"] = 10
    data["users"] = [
        {"id": 1, "login": "admin", "name": "Админ", "role": "superadmin", "school_ids": [],
         "password_hash": "x", "session_key": "k"},
        {"id": 2, "login": "uch", "name": "Учитель", "role": "teacher", "school_ids": [1, 2],
         "password_hash": "x", "session_key": "k", "checkin_manager": True},
        {"id": 3, "login": "dir", "name": "Директор", "role": "school_admin", "school_ids": [1],
         "password_hash": "x", "session_key": "k"},
    ]
    config.DB_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    migrated = db.load_db()
    assert migrated["version"] == db.DB_VERSION == 11
    users = {u["id"]: u for u in migrated["users"]}
    assert users[1]["posts"] == {}
    assert users[2]["posts"] == {"1": ["teacher"], "2": ["teacher"]} and "checkin_manager" not in users[2]
    assert users[3]["posts"] == {"1": ["director"]} and users[3]["role"] == "school_admin"
    mtime = config.DB_FILE.stat().st_mtime_ns
    assert db.load_db() == migrated and config.DB_FILE.stat().st_mtime_ns == mtime, "повторно — без изменений"
