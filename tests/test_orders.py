"""Ученик покупает приз сам (схема 13): звёзды списываются сразу, приз ждёт выдачи;
учитель отмечает «Выдан» или отменяет покупку — звёзды возвращаются."""
import pytest

from backend import db, stats
from backend.main import app
from conftest import TestClient, load_db, panel_client, student_record, TEACHER_LOGIN, TEACHER_PASSWORD

KID = TestClient(app)


@pytest.fixture
def shop(teacher):
    """Школа 1 с покупкой; у Иванова (101) 34 ★, приз 6 стоит 30, приз 1 — 70."""
    assert teacher.put("/api/schools/1/rewards", json={"self_buy": True}).status_code == 200
    return teacher


def buy(headers, prize_id=6):
    return KID.post("/api/me/orders", json={"prize_id": prize_id}, headers=headers)


def test_buy_takes_stars_and_waits(shop, student_headers):
    h = student_headers(101)
    earned_before = stats.earned(student_record(101))
    r = buy(h)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["status"] == "waiting" and order["cost"] == 30 and r.json()["total_stars"] == 4
    student = student_record(101)
    assert student["total_stars"] == 4
    entry = student["history"][0]
    assert entry["delta"] == -30 and entry["prize_id"] == 6 and entry["order_id"] == order["id"]
    assert stats.earned(student) == earned_before, "покупка не меняет место в рейтинге"

    me = KID.get("/api/me", headers=h).json()
    assert me["self_buy"] is True
    assert [o["id"] for o in me["orders"]] == [order["id"]]
    assert me["history"][0]["order"] == {"id": order["id"], "status": "waiting"}


def test_buy_off_by_default(teacher, student_headers):
    h = student_headers(101)
    assert KID.get("/api/me", headers=h).json()["self_buy"] is False
    r = buy(h)
    assert r.status_code == 403 and "учитель" in r.json()["detail"]
    assert student_record(101)["total_stars"] == 34


def test_not_enough_stars(shop, student_headers):
    r = buy(student_headers(101), prize_id=1)
    assert r.status_code == 400 and "Не хватает 36" in r.json()["detail"]
    assert student_record(101)["total_stars"] == 34 and load_db()["orders"] == []


def test_hidden_and_foreign_prizes(shop, student_headers):
    h = student_headers(101)
    shop.patch("/api/prizes/6", json={"active": False})
    assert buy(h).status_code == 400
    other = shop.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    foreign = shop.post("/api/prizes", json={"name": "Чужой", "cost": 5, "school_id": other}).json()["prize"]["id"]
    assert buy(h, foreign).status_code == 400
    assert buy(h, 999).status_code == 400


def test_preview_cannot_buy(shop):
    token = shop.post("/api/auth/preview?student_id=101").json()["token"]
    r = buy({"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert student_record(101)["total_stars"] == 34


def test_double_click_buys_once(shop, student_headers):
    h = student_headers(102)                       # Петрова, 52 ★
    first = buy(h).json()
    second = buy(h).json()
    assert second["repeat"] is True and second["order"]["id"] == first["order"]["id"]
    assert student_record(102)["total_stars"] == 22 and len(load_db()["orders"]) == 1


def test_issue(shop, student_headers):
    h = student_headers(101)
    order = buy(h).json()["order"]
    assert len(shop.get("/api/orders/waiting").json()["orders"]) == 1
    r = shop.post(f"/api/orders/{order['id']}/issue")
    assert r.status_code == 200 and r.json()["order"]["status"] == "issued"
    assert r.json()["order"]["closed_by_name"]
    assert student_record(101)["total_stars"] == 4, "звёзды списаны при покупке, выдача их не трогает"
    assert shop.post(f"/api/orders/{order['id']}/issue").status_code == 400, "дважды не выдаётся"
    assert shop.get("/api/orders/waiting").json()["orders"] == []
    assert KID.get("/api/me", headers=h).json()["history"][0]["order"]["status"] == "issued"


def test_cancel_returns_stars(shop, student_headers):
    h = student_headers(101)
    earned_before = stats.earned(student_record(101))
    order = buy(h).json()["order"]
    r = shop.post(f"/api/orders/{order['id']}/cancel", json={"reason": "Приз закончился"})
    assert r.status_code == 200 and r.json()["order"]["status"] == "cancelled"
    student = student_record(101)
    assert student["total_stars"] == 34
    assert not any(e.get("order_id") == order["id"] for e in student["history"]), "запись покупки убрана"
    assert stats.earned(student) == earned_before, "возврат — не заработок"
    me = KID.get("/api/me", headers=h).json()
    assert me["orders"][0]["status"] == "cancelled" and me["orders"][0]["cancel_reason"] == "Приз закончился"
    assert shop.post(f"/api/orders/{order['id']}/cancel", json={}).status_code == 400


def test_who_gives_prizes(shop, student_headers):
    order = buy(student_headers(101)).json()["order"]
    # Учитель этой школы — может
    plain = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert len(plain.get("/api/orders/waiting").json()["orders"]) == 1
    # Директор без уроков — нет (выдают только учителя)
    shop.post("/api/users", json={"login": "direktor", "name": "Директор", "password": "Direktor-2026",
                                  "posts": {"1": ["director"]}})
    director = panel_client("direktor", "Direktor-2026")
    assert director.post(f"/api/orders/{order['id']}/issue").status_code == 403
    # Учитель другой школы — не видит вовсе
    other = shop.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    shop.post("/api/users", json={"login": "chuzhoy", "name": "Чужой", "password": "Chuzhoy-2026",
                                  "posts": {str(other): ["teacher"]}})
    stranger = panel_client("chuzhoy", "Chuzhoy-2026")
    assert stranger.get("/api/orders/waiting").json()["orders"] == []
    assert stranger.post(f"/api/orders/{order['id']}/issue").status_code == 404
    assert stranger.get("/api/state").json()["orders"] == []
    assert plain.post(f"/api/orders/{order['id']}/issue").status_code == 200


def test_state_orders_for_givers_only(shop, student_headers):
    buy(student_headers(101))
    assert len(shop.get("/api/state").json()["orders"]) == 1
    shop.post("/api/users", json={"login": "direktor", "name": "Директор", "password": "Direktor-2026",
                                  "posts": {"1": ["director"]}})
    assert panel_client("direktor", "Direktor-2026").get("/api/state").json()["orders"] == []


def test_deleting_student_drops_orders(shop, student_headers):
    buy(student_headers(101))
    shop.delete("/api/students/101")
    assert load_db()["orders"] == []


def test_waiting_order_after_switching_off_can_still_be_closed(shop, student_headers):
    """Покупку выключили — уже купленное всё равно выдают или отменяют."""
    order = buy(student_headers(101)).json()["order"]
    shop.put("/api/schools/1/rewards", json={"self_buy": False})
    assert buy(student_headers(101)).status_code == 403
    assert shop.post(f"/api/orders/{order['id']}/cancel", json={}).status_code == 200
    assert student_record(101)["total_stars"] == 34


def test_order_ids_never_repeat(shop, student_headers):
    h = student_headers(102)
    first = buy(h).json()["order"]["id"]
    shop.post(f"/api/orders/{first}/cancel", json={})
    db_now = load_db()
    assert db_now["settings"]["last_order_id"] == first
    second = buy(h, prize_id=8).json()["order"]["id"]
    assert second == first + 1
    assert db.find_student(load_db(), 102)["total_stars"] == 52 - 12
