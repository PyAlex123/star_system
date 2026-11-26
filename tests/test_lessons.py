"""Уроки и списание звёзд."""
from conftest import load_db, save_db, student_record


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


def test_class_bank_is_per_date(teacher):
    """Главная дыра: «Завершить урок» снова и снова за ту же дату давал новые 10 звёзд."""
    assert lesson(teacher, [{"student_id": 101, "stars": 5},
                            {"student_id": 102, "stars": 5}]).status_code == 200
    before = student_record(102)["total_stars"]
    r = lesson(teacher, [{"student_id": 102, "stars": 1}])
    assert r.status_code == 400
    assert "2026-11-20" in r.json()["detail"]
    assert student_record(102)["total_stars"] == before
    assert len(load_db()["lessons"]) == 1


def test_class_bank_remainder_same_date(teacher):
    """Два урока за день: вместе не больше 10."""
    assert lesson(teacher, [{"student_id": 101, "stars": 4}]).status_code == 200
    assert lesson(teacher, [{"student_id": 102, "stars": 5}]).status_code == 200
    assert lesson(teacher, [{"student_id": 102, "stars": 2}]).status_code == 400  # 4+5+2 = 11
    assert lesson(teacher, [{"student_id": 102, "stars": 0},
                            {"student_id": 101, "stars": 1}]).status_code == 200  # ровно 10


def test_student_limit_is_per_date(teacher):
    """5 одному ученику за урок, потом ещё 5 «вторым уроком» — тоже обход."""
    assert lesson(teacher, [{"student_id": 101, "stars": 5}]).status_code == 200
    r = lesson(teacher, [{"student_id": 101, "stars": 1}])
    assert r.status_code == 400
    assert "Иванов" in r.json()["detail"]


def test_other_date_has_own_bank(teacher):
    assert lesson(teacher, [{"student_id": 101, "stars": 5},
                            {"student_id": 102, "stars": 5}]).status_code == 200
    assert lesson(teacher, [{"student_id": 101, "stars": 5},
                            {"student_id": 102, "stars": 5}], date="2026-11-21").status_code == 200


def test_other_class_has_own_bank(teacher):
    assert lesson(teacher, [{"student_id": 101, "stars": 5},
                            {"student_id": 102, "stars": 5}]).status_code == 200
    assert lesson(teacher, [{"student_id": 103, "stars": 5}], class_id=2).status_code == 200


def test_lesson_record_keeps_awards(teacher):
    lesson(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 102, "stars": 0}])
    assert load_db()["lessons"][0]["awards"] == {"101": 3}


def test_old_lessons_without_awards_still_count(teacher):
    """Уроки, записанные до обновления, не хранят раскладку по ученикам — считаем по истории."""
    lesson(teacher, [{"student_id": 101, "stars": 5}])
    data = load_db()
    del data["lessons"][0]["awards"]
    save_db(data)
    assert lesson(teacher, [{"student_id": 101, "stars": 1}]).status_code == 400
    assert lesson(teacher, [{"student_id": 102, "stars": 5}]).status_code == 200
    assert lesson(teacher, [{"student_id": 102, "stars": 0},
                            {"student_id": 101, "stars": 0}]).status_code == 200


def test_state_reports_used_today(teacher):
    """Панели нужно знать, сколько уже начислено за дату, — иначе банк снова показывает 10."""
    lesson(teacher, [{"student_id": 101, "stars": 3}])
    state = teacher.get("/api/state").json()
    assert state["lessons"][0]["awards"] == {"101": 3}
    assert state["lessons"][0]["class_id"] == 1


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


# ── Общий урок объединённых классов ──────────────────────────────────────────

def joint(teacher, awards, class_ids=(1, 2), date="2026-11-20"):
    return teacher.post("/api/lessons/complete",
                        json={"class_id": class_ids[0], "class_ids": list(class_ids), "date": date, "awards": awards})


def test_joint_lesson_records_per_class(teacher):
    """101, 102 — 5-А; 103 — 5-Б; 104 — 6-А. Запись урока — у каждого класса своя."""
    before = student_record(103)["total_stars"]
    r = joint(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 103, "stars": 4}], class_ids=(1, 2, 3))
    assert r.status_code == 200, r.text
    assert r.json()["students_rewarded"] == 2
    assert student_record(103)["total_stars"] == before + 4
    records = {l["class_id"]: l for l in load_db()["lessons"]}
    assert set(records) == {1, 2, 3}, "у 6-А без звёзд тоже запись — урок у него проведён"
    assert records[1]["awards"] == {"101": 3} and records[1]["total_stars"] == 3
    assert records[2]["awards"] == {"103": 4} and records[2]["total_stars"] == 4
    assert records[3]["awards"] == {} and records[3]["total_stars"] == 0
    assert len({l["group"] for l in records.values()}) == 1 and records[1]["group"] == records[1]["id"]
    assert {l["joint_names"] for l in records.values()} == {"5-А + 5-Б + 6-А"}


def test_joint_lesson_shares_one_bank(teacher):
    r = joint(teacher, [{"student_id": 101, "stars": 5}, {"student_id": 102, "stars": 1},
                        {"student_id": 103, "stars": 5}])
    assert r.status_code == 400 and "10" in r.json()["detail"], "11 звёзд на двоих — больше общего банка"
    assert load_db()["lessons"] == [], "урок не прошёл целиком"
    assert joint(teacher, [{"student_id": 101, "stars": 5}, {"student_id": 103, "stars": 5}]).status_code == 200


def test_joint_bank_counts_earlier_lessons_of_day(teacher):
    assert lesson(teacher, [{"student_id": 101, "stars": 4}]).status_code == 200          # 5-А отдельно
    r = joint(teacher, [{"student_id": 103, "stars": 5}, {"student_id": 102, "stars": 2}])
    assert r.status_code == 400 and "Можно добавить ещё не больше 6" in r.json()["detail"]
    assert joint(teacher, [{"student_id": 103, "stars": 5}, {"student_id": 102, "stars": 1}]).status_code == 200
    assert joint(teacher, [{"student_id": 102, "stars": 1}], date="2026-11-21").status_code == 200, "другая дата — новый банк"


def test_joint_lesson_checks(teacher, school_teacher):
    r = joint(teacher, [{"student_id": 104, "stars": 1}])
    assert r.status_code == 400 and "учится не в классах 5-А + 5-Б" in r.json()["detail"]
    other = school_teacher.other["class"]
    assert joint(school_teacher, [{"student_id": 101, "stars": 1}], class_ids=(1, other)).status_code == 404
    r = joint(teacher, [{"student_id": 101, "stars": 1}], class_ids=(1, other))
    assert r.status_code == 400 and "одной школы" in r.json()["detail"]
    assert joint(teacher, [{"student_id": 101, "stars": 1}], class_ids=(1, 1)).status_code == 200
    assert "group" not in load_db()["lessons"][0], "один и тот же класс дважды — обычный урок"


def test_joint_lesson_undo_one_class(teacher):
    joint(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 103, "stars": 2}])
    first = next(l for l in load_db()["lessons"] if l["class_id"] == 2)
    before = student_record(103)["total_stars"]
    assert teacher.post(f"/api/lessons/{first['id']}/undo", json={}).status_code == 200
    assert student_record(103)["total_stars"] == before - 2
    assert next(l for l in load_db()["lessons"] if l["class_id"] == 1)["total_stars"] == 3, "5-А не тронут"
