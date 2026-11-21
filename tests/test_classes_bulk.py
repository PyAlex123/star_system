"""Сетка классов: несколько классов одним действием."""
from conftest import load_db


def test_creates_several(teacher):
    r = teacher.post("/api/classes/bulk", json={"school_id": 1, "names": ["7-А", "7-Б", "Робототехника"]})
    assert r.status_code == 200, r.text
    assert [c["name"] for c in r.json()["created"]] == ["7-А", "7-Б", "Робототехника"]
    names = {c["name"] for c in load_db()["classes"] if c["school_id"] == 1}
    assert {"7-А", "7-Б", "Робототехника"} <= names


def test_existing_and_repeated_skipped(teacher):
    r = teacher.post("/api/classes/bulk", json={"school_id": 1, "names": ["5-а", "8-В", "8-в", " "]})
    assert r.status_code == 200
    assert [c["name"] for c in r.json()["created"]] == ["8-В"]
    assert r.json()["skipped"] == 1
    assert sum(1 for c in load_db()["classes"] if c["name"].lower() == "8-в") == 1


def test_nothing_new_writes_nothing(teacher):
    before = load_db()["classes"]
    r = teacher.post("/api/classes/bulk", json={"school_id": 1, "names": ["5-А"]})
    assert r.json()["created"] == []
    assert load_db()["classes"] == before


def test_empty_list_rejected(teacher):
    assert teacher.post("/api/classes/bulk", json={"school_id": 1, "names": []}).status_code == 400


def test_missing_school(teacher):
    assert teacher.post("/api/classes/bulk", json={"school_id": 999, "names": ["1-А"]}).status_code == 404
