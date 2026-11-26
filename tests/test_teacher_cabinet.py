"""Кабинет преподавателя в панели: свои уроки сегодня и за месяц, часы, опоздания —
без ставки и сумм (их видит только администратор). Ссылку в бот можно взять себе самому."""
import json

from backend import db
from backend.checkin import clock
from conftest import TEACHER_LOGIN, TEACHER_PASSWORD, load_db, panel_client
from test_checkin_bot import TeacherChat, at, prepare


def _mine(client, month="2026-11"):
    r = client.get(f"/api/checkin/my?month={month}")
    assert r.status_code == 200, r.text
    return r.json()


def test_cabinet_shows_own_lessons_hours_and_lateness(teacher):
    prepare()
    teacher.patch("/api/users/2", json={"hourly_rate": 90000})
    me = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    data = _mine(me)
    assert data["tracked"] is True and data["telegram"]["bound"] is True
    assert data["today"] == "2026-11-23"
    assert [(l["date"], l["start"], l["state"]) for l in data["today_lessons"]] == [("2026-11-23", "09:00", "ahead")]
    assert data["summary"]["planned"] >= 1 and data["summary"]["hours"] == 0
    assert data["rules"] == {"before": 60, "grace": 5, "merge_gap": 30}

    # Отметился в 09:07 — опоздание 7 минут, урок засчитан
    chat = TeacherChat()
    chat.location(at("09:07"))
    chat.video_note(at("09:07"))
    data = _mine(me)
    today = data["today_lessons"][0]
    assert today["state"] == "late" and today["late_minutes"] == 7 and today["checkin_at"] == "09:07"
    assert data["summary"]["late"] == 1 and data["summary"]["late_minutes"] == 7
    assert data["summary"]["confirmed"] == 1 and data["summary"]["hours"] == 0.75

    text = json.dumps(data, ensure_ascii=False)
    for secret in ("90000", "rate", "money", "сум"):
        assert secret not in text, f"в кабинете нет денег: {secret}"


def test_cabinet_is_only_about_me(teacher):
    prepare()
    data = _mine(teacher)                      # администратор — у него своих уроков нет
    assert data["lessons"] == [] and data["today_lessons"] == [] and data["summary"]["planned"] == 0
    assert teacher.get("/api/checkin/my?month=2026-13").status_code == 400
    assert teacher.get("/api/checkin/my?month=ноябрь").status_code == 400


def test_school_admin_opens_own_cabinet(teacher):
    prepare()
    teacher.post("/api/users", json={"login": "direktor", "name": "Директор", "password": "direktor-2026",
                                     "posts": {"1": ["director"]}})
    director = panel_client("direktor", "direktor-2026")
    data = _mine(director)
    assert data["tracked"] is False and data["lessons"] == []


def test_self_invite_only_when_tracked_and_not_bound(teacher):
    prepare(bound=False)
    me = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    r = me.post("/api/checkin/my/invite")
    assert r.status_code == 200, r.text
    assert r.json()["link"].startswith("https://t.me/test_stars_bot?start=inv_")
    code = db.find_user(load_db(), 2)["invite_code"]
    assert r.json()["link"].endswith(code)
    # Ссылка работает в боте
    replies = TeacherChat().text(f"/start inv_{code}", at=clock.now_utc())
    assert "Telegram привязан" in replies[0]
    assert me.post("/api/checkin/my/invite").json()["detail"] == "Telegram уже привязан"

    teacher.patch("/api/users/2", json={"checkin_tracked": False})
    r = me.post("/api/checkin/my/invite")
    assert r.status_code == 400 and "не включены" in r.json()["detail"]
