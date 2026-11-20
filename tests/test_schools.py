"""Школы и классы: создание, уникальность названий, каскадное удаление."""
from conftest import load_db


def test_state_has_schema_and_hierarchy(teacher):
    state = teacher.get("/api/state").json()
    assert state["schools"]
    assert all({"id", "school_id", "name"} <= set(c) for c in state["classes"])
    assert all("class_id" in s for s in state["students"])


# ── Школы ────────────────────────────────────────────────────────────────────

def test_create_and_rename_school(teacher):
    r = teacher.post("/api/schools", json={"name": "Гимназия №2"})
    assert r.status_code == 200
    sid = r.json()["school"]["id"]
    assert teacher.patch(f"/api/schools/{sid}", json={"name": "Гимназия № 2"}).status_code == 200
    names = [s["name"] for s in teacher.get("/api/state").json()["schools"]]
    assert "Гимназия № 2" in names


def test_duplicate_school_rejected_case_insensitive(teacher):
    teacher.post("/api/schools", json={"name": "Лицей"})
    assert teacher.post("/api/schools", json={"name": "лицей"}).status_code == 400


def test_empty_school_name_rejected(teacher):
    assert teacher.post("/api/schools", json={"name": "   "}).status_code == 400


def test_rename_missing_school(teacher):
    assert teacher.patch("/api/schools/999", json={"name": "X"}).status_code == 404


def test_cannot_delete_last_school(teacher):
    only = teacher.get("/api/state").json()["schools"][0]["id"]
    assert teacher.delete(f"/api/schools/{only}").status_code == 400


def test_delete_school_cascades(teacher):
    sid = teacher.post("/api/schools", json={"name": "Удаляемая"}).json()["school"]["id"]
    cid = teacher.post("/api/classes", json={"school_id": sid, "name": "7-В"}).json()["class"]["id"]
    st = teacher.post("/api/students", json={"class_id": cid, "name": "Тестов Тест"}).json()["student"]
    teacher.post("/api/lessons/complete", json={
        "class_id": cid, "date": "2026-11-20", "awards": [{"student_id": st["id"], "stars": 2}]})

    assert teacher.delete(f"/api/schools/{sid}").status_code == 200

    data = load_db()
    assert not any(c["school_id"] == sid for c in data["classes"])
    assert not any(s["id"] == st["id"] for s in data["students"])
    assert not any(l.get("class_id") == cid for l in data["lessons"])


# ── Классы ───────────────────────────────────────────────────────────────────

def test_create_class(teacher):
    r = teacher.post("/api/classes", json={"school_id": 1, "name": "7-В"})
    assert r.status_code == 200
    assert r.json()["class"]["school_id"] == 1


def test_duplicate_class_in_same_school_rejected(teacher):
    assert teacher.post("/api/classes", json={"school_id": 1, "name": "5-а"}).status_code == 400


def test_same_class_name_allowed_in_other_school(teacher):
    sid = teacher.post("/api/schools", json={"name": "Вторая"}).json()["school"]["id"]
    assert teacher.post("/api/classes", json={"school_id": sid, "name": "5-А"}).status_code == 200


def test_class_in_missing_school(teacher):
    assert teacher.post("/api/classes", json={"school_id": 999, "name": "1-А"}).status_code == 404


def test_rename_class_conflict(teacher):
    # В школе 1 уже есть 5-А (id 1) и 5-Б (id 2)
    assert teacher.patch("/api/classes/2", json={"name": "5-А"}).status_code == 400
    assert teacher.patch("/api/classes/2", json={"name": "5-В"}).status_code == 200


def test_rename_class_keeps_students(teacher):
    teacher.patch("/api/classes/1", json={"name": "5-А1"})
    names = {s["name"] for s in load_db()["students"] if s["class_id"] == 1}
    assert {"Иванов Артём", "Петрова София"} <= names


def test_move_class_to_other_school(teacher):
    sid = teacher.post("/api/schools", json={"name": "Вторая"}).json()["school"]["id"]
    assert teacher.patch("/api/classes/3", json={"school_id": sid}).status_code == 200
    assert next(c for c in load_db()["classes"] if c["id"] == 3)["school_id"] == sid


def test_delete_class_removes_students(teacher):
    assert teacher.delete("/api/classes/1").status_code == 200
    data = load_db()
    assert not any(c["id"] == 1 for c in data["classes"])
    assert not any(s.get("class_id") == 1 for s in data["students"])
