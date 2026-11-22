"""Рейтинги класса, параллели и школы по заработанным звёздам; активность; статистика школы."""
from datetime import date

from fastapi.testclient import TestClient

from backend import stats
from backend.main import app
from conftest import load_db, save_db, student_record


def me(headers):
    return TestClient(app).get("/api/me", headers=headers).json()


def lesson(teacher, awards, class_id=1, day="2026-11-20"):
    r = teacher.post("/api/lessons/complete", json={"class_id": class_id, "date": day, "awards": awards})
    assert r.status_code == 200, r.text


def test_parallel_of():
    assert stats.parallel_of("5-А") == 5
    assert stats.parallel_of("10-Ф1") == 10
    assert stats.parallel_of(" 7 Б") == 7
    assert stats.parallel_of("Робототехника") is None
    assert stats.parallel_of("123") is None


def test_rank_by_earned_not_balance(teacher, student_headers):
    """Потратил на приз — место не падает."""
    headers = student_headers(101)                      # Иванов, 34 звезды
    teacher.post("/api/students/deduct", json={"student_id": 102, "amount": 30, "reason": "Приз"})
    board = me(headers)["leaderboard"]
    assert board["top"][0]["id"] == 102, "Петрова заработала 52 — первая, хоть и потратила"
    assert board["top"][0]["score"] == 52


def test_three_levels(teacher, student_headers):
    boards = me(student_headers(101))["leaderboards"]
    assert boards["class"]["class_size"] == 2           # 101, 102 в 5-А
    assert boards["parallel"]["class_size"] == 3        # + 103 из 5-Б
    assert boards["school"]["class_size"] == 4          # + 104 из 6-А
    assert {r["class_name"] for r in boards["school"]["top"]} == {"5-А", "5-Б", "6-А"}


def test_no_parallel_for_lonely_class(teacher, student_headers):
    boards = me(student_headers(104))["leaderboards"]  # 6-А — один в параллели
    assert boards["parallel"] is None
    assert boards["school"] is not None


def test_hidden_excluded_everywhere(teacher, student_headers):
    headers = student_headers(101)
    teacher.patch("/api/students/103", json={"rating_hidden": True})
    boards = me(headers)["leaderboards"]
    assert all(r["id"] != 103 for b in boards.values() if b for r in b["top"])


def test_activity_streak_and_idle(teacher, student_headers):
    for day in ("2026-11-17", "2026-11-18", "2026-11-19"):
        lesson(teacher, [{"student_id": 102, "stars": 2}], day=day)
    lesson(teacher, [{"student_id": 101, "stars": 1}, {"student_id": 102, "stars": 1}], day="2026-11-20")
    database = load_db()
    a101 = stats.student_activity(database, student_record(101), date(2026, 11, 21))
    a102 = stats.student_activity(database, student_record(102), date(2026, 11, 21))
    assert a102["streak"] == 4 and a102["idle_lessons"] == 0
    assert a101["streak"] == 1
    assert a102["week"] == 7 and a102["lessons_30"] == 4 and a102["lessons_30_with_stars"] == 4
    lesson(teacher, [{"student_id": 102, "stars": 1}], day="2026-11-21")
    lesson(teacher, [{"student_id": 102, "stars": 1}], day="2026-11-22")
    lesson(teacher, [{"student_id": 102, "stars": 1}], day="2026-11-23")
    a101 = stats.student_activity(load_db(), student_record(101), date(2026, 11, 23))
    assert a101["idle"] and a101["idle_lessons"] == 3


def test_start_balance_is_not_earned_this_week(teacher):
    teacher.post("/api/students", json={"class_id": 1, "name": "Новиков Пётр", "total_stars": 20})
    s = next(x for x in load_db()["students"] if x["name"] == "Новиков Пётр")
    a = stats.student_activity(load_db(), s)
    assert a["week"] == 0 and a["earned"] == 20


def test_me_has_activity_and_marks_seen(teacher, student_headers):
    headers = student_headers(101)
    data = me(headers)
    assert "week" in data["activity"] and len(data["activity"]["weeks"]) == 8
    assert student_record(101)["last_seen"]


def test_preview_does_not_mark_seen(teacher):
    token = teacher.post("/api/auth/preview?student_id=102").json()["token"]
    TestClient(app).get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert not student_record(102).get("last_seen")


def test_school_stats(teacher):
    lesson(teacher, [{"student_id": 101, "stars": 3}, {"student_id": 102, "stars": 2}])
    teacher.post("/api/students/deduct", json={"student_id": 102, "amount": 12, "reason": "Приз: светодиоды",
                                               "prize_id": 8})
    data = load_db()
    for s in data["students"]:                         # даты в окне «за 7 дней» от сегодня
        for h in s["history"]:
            h["date"] = stats.today().isoformat()
    for l in data["lessons"]:
        l["date"] = stats.today().isoformat()
    save_db(data)
    r = teacher.get("/api/stats?school_id=1&days=7")
    assert r.status_code == 200, r.text
    st = r.json()
    t = st["totals"]
    assert t["students"] == 4 and t["lessons"] == 1 and t["prizes"] == 1 and t["spent"] == 12
    assert t["awarded"] >= 5
    assert [c["name"] for c in st["classes"]] == ["5-А", "5-Б", "6-А"]
    assert len(st["weeks"]) == 12
    assert st["teachers"][0]["lessons"] == 1 and st["teachers"][0]["name"] == "Администратор"
    rows = {s["id"]: s for s in st["students"]}
    assert rows[101]["period"] >= 3 and rows[101]["class_name"] == "5-А"
    assert "token" not in rows[101] and "login" not in rows[101]


def test_stats_period_validation(teacher):
    assert teacher.get("/api/stats?school_id=1&days=13").status_code == 400
    assert teacher.get("/api/stats?school_id=1").status_code == 200


def test_student_stats_endpoint(teacher, school_teacher):
    assert teacher.get("/api/stats/student/101").json()["activity"]["earned"] == 34
    assert school_teacher.get(f"/api/stats/student/{school_teacher.other['student']}").status_code == 404


def test_lesson_remembers_teacher(teacher):
    lesson(teacher, [{"student_id": 101, "stars": 1}])
    admin = next(u for u in load_db()["users"] if u["role"] == "superadmin")
    assert load_db()["lessons"][0]["user_id"] == admin["id"]
