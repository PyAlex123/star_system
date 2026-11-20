"""Уроки и списание звёзд."""
from conftest import load_db, student_record


def lesson(teacher, awards, class_id=1, date="2026-11-20"):
    return teacher.post("/api/lessons/complete",
                        json={"class_id": class_id, "date": date, "awards": awards})


def test_lesson_bank_limit(teacher):
    r = lesson(teacher, [{"student_id": 101, "stars": 5}, {"student_id": 102, "stars": 6}])
    assert r.status_code == 400


def test_per_student_limit(teacher):
    assert lesson(teacher, [{"student_id": 101, "stars": 6}]).status_code == 400
    assert lesson(teacher, [{"student_id": 101, "stars": -1}]).status_code == 400


def test_lesson_awards_stars_and_history(teacher):
    before = student_record(101)["total_stars"]
    r = lesson(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 102, "stars": 0}])
    assert r.json()["students_rewarded"] == 1
    s = student_record(101)
    assert s["total_stars"] == before + 3
    assert s["history"][0] == {"date": "2026-11-20", "delta": 3, "reason": "Урок (2026-11-20)"}


def test_lesson_record_keeps_names(teacher):
    """Класс могут переименовать — история урока должна остаться читаемой."""
    lesson(teacher, [{"student_id": 101, "stars": 2}])
    teacher.patch("/api/classes/1", json={"name": "6-А новый"})
    record = load_db()["lessons"][0]
    assert record["class_name"] == "5-А"
    assert record["school_name"] == "Наша школа"
    assert record["total_stars"] == 2


def test_same_student_twice_cannot_bypass_limit(teacher):
    """5 + 5 одному ученику — обход лимита «не больше 5 за урок»."""
    before = student_record(101)["total_stars"]
    r = lesson(teacher, [{"student_id": 101, "stars": 5}, {"student_id": 101, "stars": 5}])
    assert r.status_code == 400
    assert student_record(101)["total_stars"] == before


def test_student_from_other_class_rejected(teacher):
    """Каримов учится в 5-Б: звёзды за урок 5-А ему не положены."""
    before = student_record(103)["total_stars"]
    r = lesson(teacher, [{"student_id": 103, "stars": 3}], class_id=1)
    assert r.status_code == 400
    assert student_record(103)["total_stars"] == before
    assert load_db()["lessons"] == []


def test_lesson_in_missing_class(teacher):
    assert lesson(teacher, [{"student_id": 101, "stars": 1}], class_id=999).status_code == 404


def test_deduct(teacher):
    before = student_record(102)["total_stars"]
    r = teacher.post("/api/students/deduct",
                     json={"student_id": 102, "amount": 12, "reason": "Набор светодиодов"})
    assert r.status_code == 200
    s = student_record(102)
    assert s["total_stars"] == before - 12
    assert s["history"][0]["delta"] == -12
    assert s["history"][0]["reason"] == "Набор светодиодов"


def test_deduct_not_more_than_balance(teacher):
    balance = student_record(104)["total_stars"]
    r = teacher.post("/api/students/deduct", json={"student_id": 104, "amount": balance + 1})
    assert r.status_code == 400
    assert student_record(104)["total_stars"] == balance


def test_deduct_positive_only(teacher):
    assert teacher.post("/api/students/deduct",
                        json={"student_id": 101, "amount": 0}).status_code == 400
