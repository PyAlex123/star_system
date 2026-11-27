"""Школа без системы наград (схема 13): расписание, ученики, опросы и отметки остаются,
звёзд, призов, карточек с QR и кабинета ученика нет. И призы для отдельной школы."""
import json

from backend import config, db
from backend.main import app
from conftest import TestClient, load_db, panel_client, student_record, TEACHER_LOGIN, TEACHER_PASSWORD
from test_checkin_bot import TeacherChat


def rewards(client, school_id=1, **change):
    return client.put(f"/api/schools/{school_id}/rewards", json=change)


# ── Данные ───────────────────────────────────────────────────────────────────

def test_defaults_and_migration_from_12():
    database = db.load_db()
    school = database["schools"][0]
    assert school["rewards"] is True and school["self_buy"] is False
    assert database["orders"] == [] and database["version"] == db.DB_VERSION == 13

    old = load_db()
    old["version"] = 12
    for s in old["schools"]:
        s.pop("rewards"), s.pop("self_buy")
    old.pop("orders")
    config.DB_FILE.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    migrated = db.load_db()
    assert migrated["schools"][0]["rewards"] is True and migrated["schools"][0]["self_buy"] is False
    assert migrated["orders"] == []


def test_new_school_takes_part_by_default(teacher):
    school = teacher.post("/api/schools", json={"name": "Новая"}).json()["school"]
    assert school["rewards"] is True and school["self_buy"] is False


# ── Кто меняет ───────────────────────────────────────────────────────────────

def test_only_superadmin_switches(teacher):
    r = rewards(teacher, rewards=False)
    assert r.status_code == 200 and r.json() == {"ok": True, "rewards": False, "self_buy": False}
    assert db.find_school(db.load_db(), 1)["rewards"] is False
    assert rewards(teacher, rewards=True, self_buy=True).json()["self_buy"] is True
    assert rewards(teacher, school_id=999, rewards=False).status_code == 404

    plain = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert rewards(plain, rewards=False).status_code == 403
    assert db.find_school(db.load_db(), 1)["rewards"] is True


def test_self_buy_off_without_rewards(teacher):
    rewards(teacher, self_buy=True)
    r = rewards(teacher, rewards=False)
    assert r.json()["self_buy"] is False, "без системы наград покупки нет, даже если галочка стояла"


# ── Без наград: звёзд нет, остальное работает ────────────────────────────────

def test_no_stars_no_prizes_no_cards(teacher):
    rewards(teacher, rewards=False)
    r = teacher.post("/api/lessons/complete", json={"class_id": 1, "date": "2026-11-20",
                                                    "awards": [{"student_id": 101, "stars": 3}]})
    assert r.status_code == 400 and "система наград выключена" in r.json()["detail"]
    assert teacher.post("/api/students/deduct", json={"student_id": 101, "amount": 5}).status_code == 400
    assert teacher.get("/cards/1").status_code == 400
    assert teacher.get("/api/students/101/qr.png").status_code == 400
    assert teacher.post("/api/auth/preview?student_id=101").status_code == 400
    assert student_record(101)["total_stars"] == 34

    # Ученики, классы и расписание — как раньше
    assert teacher.post("/api/students", json={"class_id": 1, "name": "Новый Ученик"}).status_code == 200
    assert teacher.post("/api/classes", json={"school_id": 1, "name": "7-А"}).status_code == 200


def test_undo_blocked_after_switch_off(teacher):
    teacher.post("/api/lessons/complete", json={"class_id": 1, "date": "2026-11-20",
                                                "awards": [{"student_id": 101, "stars": 3}]})
    lesson_id = load_db()["lessons"][0]["id"]
    rewards(teacher, rewards=False)
    assert teacher.post(f"/api/lessons/{lesson_id}/undo", json={}).status_code == 400


def test_other_school_unaffected(teacher):
    other = teacher.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    rewards(teacher, school_id=other, rewards=False)
    r = teacher.post("/api/lessons/complete", json={"class_id": 1, "date": "2026-11-20",
                                                    "awards": [{"student_id": 101, "stars": 2}]})
    assert r.status_code == 200


def test_cabinet_closed(teacher, student_headers):
    h = student_headers(101)
    rewards(teacher, rewards=False)
    data = TestClient(app).get("/api/me", headers=h).json()
    assert data["closed"] is True and data["student"]["name"] == "Иванов Артём"
    assert "prizes" not in data and "history" not in data and "total_stars" not in data["student"]


def test_poll_without_stars_in_school_without_rewards(teacher):
    rewards(teacher, rewards=False)
    r = teacher.post("/api/monitoring/runs", json={"school_id": 1, "set_key": "pulse", "class_ids": [1],
                                                   "minutes": 5, "stars": 3})
    assert r.status_code == 200, r.text
    assert r.json()["run"]["stars"] == 0, "опросы есть, звёзд за них нет"


def test_poll_stars_not_given_if_school_switched_off_later(teacher, student_headers):
    from backend.monitoring import store
    from backend.routers import m_public
    h = student_headers(101)
    teacher.post("/api/monitoring/runs", json={"school_id": 1, "set_key": "pulse", "class_ids": [1],
                                               "minutes": 5, "stars": 3})
    kid = TestClient(app)
    token = kid.post("/api/me/poll/join", json={}, headers=h).json()["url"].split("?p=")[1]
    rewards(teacher, rewards=False)
    before = student_record(101)["total_stars"]
    for qid, value in (("c2", {"v": 4}), ("c5", {"v": "yellow"}), ("c9", {"o": "b"})):
        kid.post("/api/m/answer", json={"token": token, "question_id": qid, "value": value})
    kid.post("/api/m/finish", json={"token": token})
    assert student_record(101)["total_stars"] == before
    with store.read() as conn:
        pid = store.participant_by_token(conn, token)["id"]
    assert m_public.award_stars(pid) == 0


def test_bot_gives_no_cabinet_button(teacher):
    rewards(teacher, rewards=False)
    code = student_record(101)["token"]
    chat = TeacherChat(tg_id=7700)
    replies = chat.text(f"/start {code}")
    assert replies and "не работает" in replies[-1]
    sent = chat.keyboard_of_last()
    assert sent is None, "кнопки кабинета нет"


def test_panel_state_shows_flags(teacher):
    rewards(teacher, rewards=False)
    school = teacher.get("/api/state").json()["schools"][0]
    assert school["rewards"] is False and school["self_buy"] is False


# ── Призы для отдельной школы ────────────────────────────────────────────────

def test_superadmin_moves_prize_between_common_and_school(teacher):
    other = teacher.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    r = teacher.patch("/api/prizes/1", json={"school_id": other})
    assert r.status_code == 200 and r.json()["prize"]["school_id"] == other
    assert teacher.patch("/api/prizes/1", json={"name": "Знаток"}).json()["prize"]["school_id"] == other, \
        "без school_id в запросе — школа не меняется"
    assert teacher.patch("/api/prizes/1", json={"school_id": None}).json()["prize"]["school_id"] is None
    assert teacher.patch("/api/prizes/1", json={"school_id": 999}).status_code == 404


def test_director_cannot_move_prize(teacher):
    teacher.post("/api/users", json={"login": "direktor", "name": "Директор", "password": "Direktor-2026",
                                     "posts": {"1": ["director"]}})
    director = panel_client("direktor", "Direktor-2026")
    prize = director.post("/api/prizes", json={"name": "Кружка", "cost": 10}).json()["prize"]
    assert prize["school_id"] == 1
    r = director.patch(f"/api/prizes/{prize['id']}", json={"school_id": None})
    assert r.status_code == 403
    assert director.patch(f"/api/prizes/{prize['id']}", json={"school_id": 1, "cost": 12}).status_code == 200
