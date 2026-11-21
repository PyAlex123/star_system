"""Отмена ошибочного начисления, «скрыт из рейтинга» и номер приза в истории."""
from fastapi.testclient import TestClient

from backend.main import app
from conftest import load_db, save_db, student_record


def lesson(teacher, awards, class_id=1, date="2026-11-20"):
    r = teacher.post("/api/lessons/complete",
                     json={"class_id": class_id, "date": date, "awards": awards})
    assert r.status_code == 200, r.text
    return load_db()["lessons"][0]


def test_lesson_gets_id(teacher):
    first = lesson(teacher, [{"student_id": 101, "stars": 2}])
    second = lesson(teacher, [{"student_id": 101, "stars": 1}], date="2026-11-21")
    assert second["id"] == first["id"] + 1
    assert first["undone"] == {}


def test_undo_one_student(teacher):
    before = student_record(101)["total_stars"]
    rec = lesson(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 102, "stars": 2}])
    r = teacher.post(f"/api/lessons/{rec['id']}/undo", json={"student_id": 101})
    assert r.status_code == 200, r.text
    s = student_record(101)
    assert s["total_stars"] == before
    assert not any(e.get("reason") == "Урок (2026-11-20)" for e in s["history"])
    after = load_db()["lessons"][0]
    assert after["awards"] == {"102": 2} and after["undone"] == {"101": 3}
    assert after["total_stars"] == 2 and after["students_rewarded"] == 1


def test_undo_frees_daily_limit(teacher):
    rec = lesson(teacher, [{"student_id": 101, "stars": 5}, {"student_id": 102, "stars": 5}])
    teacher.post(f"/api/lessons/{rec['id']}/undo", json={})
    lesson(teacher, [{"student_id": 101, "stars": 5}, {"student_id": 102, "stars": 5}])


def test_undo_whole_lesson_and_restore(teacher):
    b101, b102 = student_record(101)["total_stars"], student_record(102)["total_stars"]
    rec = lesson(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 102, "stars": 2}])
    assert teacher.post(f"/api/lessons/{rec['id']}/undo", json={}).json()["stars"] == 5
    assert student_record(101)["total_stars"] == b101
    assert student_record(102)["total_stars"] == b102
    r = teacher.post(f"/api/lessons/{rec['id']}/restore", json={"student_id": 102})
    assert r.status_code == 200
    assert student_record(102)["total_stars"] == b102 + 2
    assert student_record(102)["history"][0]["reason"] == "Урок (2026-11-20)"
    assert load_db()["lessons"][0]["awards"] == {"102": 2}


def test_undo_twice_is_error(teacher):
    rec = lesson(teacher, [{"student_id": 101, "stars": 3}])
    teacher.post(f"/api/lessons/{rec['id']}/undo", json={"student_id": 101})
    r = teacher.post(f"/api/lessons/{rec['id']}/undo", json={"student_id": 101})
    assert r.status_code == 400


def test_undo_refused_when_stars_spent(teacher):
    data = load_db()
    next(s for s in data["students"] if s["id"] == 101)["total_stars"] = 0
    save_db(data)
    rec = lesson(teacher, [{"student_id": 101, "stars": 4}])
    teacher.post("/api/students/deduct", json={"student_id": 101, "amount": 3, "reason": "Приз"})
    r = teacher.post(f"/api/lessons/{rec['id']}/undo", json={"student_id": 101})
    assert r.status_code == 400
    assert "потрачена" in r.json()["detail"]
    assert student_record(101)["total_stars"] == 1


def test_restore_respects_daily_limits(teacher):
    rec = lesson(teacher, [{"student_id": 101, "stars": 5}])
    teacher.post(f"/api/lessons/{rec['id']}/undo", json={})
    lesson(teacher, [{"student_id": 101, "stars": 4}])
    r = teacher.post(f"/api/lessons/{rec['id']}/restore", json={})
    assert r.status_code == 400


def test_old_lesson_without_awards_cannot_be_undone(teacher):
    data = load_db()
    data["lessons"].insert(0, {"id": 50, "class_id": 1, "date": "2026-10-01",
                               "total_stars": 3, "students_rewarded": 1, "undone": {}})
    save_db(data)
    r = teacher.post("/api/lessons/50/undo", json={})
    assert r.status_code == 400 and "до обновления" in r.json()["detail"]


def test_other_school_lesson_is_404(school_teacher, teacher):
    r = teacher.post("/api/lessons/complete", json={
        "class_id": school_teacher.other["class"], "date": "2026-11-20",
        "awards": [{"student_id": school_teacher.other["student"], "stars": 2}]})
    assert r.status_code == 200
    lesson_id = load_db()["lessons"][0]["id"]
    assert school_teacher.post(f"/api/lessons/{lesson_id}/undo", json={}).status_code == 404
    assert school_teacher.post("/api/lessons/9999/undo", json={}).status_code == 404


# ── Скрыт из рейтинга ─────────────────────────────────────────────────────────

def test_hidden_student_not_in_rating(teacher, student_headers):
    headers = student_headers(101)
    r = teacher.patch("/api/students/102", json={"rating_hidden": True})
    assert r.status_code == 200 and r.json()["student"]["rating_hidden"] is True
    me = TestClient(app).get("/api/me", headers=headers).json()
    assert all(row["id"] != 102 for row in me["leaderboard"]["top"])


def test_hidden_student_has_no_place(teacher, student_headers):
    headers = student_headers(101)
    teacher.patch("/api/students/101", json={"rating_hidden": True})
    me = TestClient(app).get("/api/me", headers=headers).json()
    assert me["leaderboard"]["my_place"] is None
    assert me["student"]["rating_hidden"] is True
    teacher.patch("/api/students/101", json={"rating_hidden": False})
    me = TestClient(app).get("/api/me", headers=headers).json()
    assert me["leaderboard"]["my_place"] is not None


# ── Выдача приза помнит, какой приз ───────────────────────────────────────────

def test_deduct_with_prize_records_it(teacher, student_headers):
    headers = student_headers(101)
    r = teacher.post("/api/students/deduct",
                     json={"student_id": 101, "amount": 12, "reason": "Приз: светодиоды", "prize_id": 8})
    assert r.status_code == 200, r.text
    assert student_record(101)["history"][0]["prize_id"] == 8
    me = TestClient(app).get("/api/me", headers=headers).json()
    assert me["history"][0]["prize"]["name"] == "Набор светодиодов / Брелок"


def test_deduct_with_unknown_prize(teacher):
    r = teacher.post("/api/students/deduct",
                     json={"student_id": 101, "amount": 1, "reason": "x", "prize_id": 999})
    assert r.status_code == 400


def test_prize_categories_are_sizes(teacher):
    assert teacher.post("/api/prizes", json={"name": "A", "cost": 5, "category": "medium"}).status_code == 200
    assert teacher.post("/api/prizes", json={"name": "B", "cost": 5, "category": "gadgets"}).status_code == 422
