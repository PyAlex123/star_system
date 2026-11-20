"""Вход в кабинет из Telegram: проверка подписи initData."""
import time

from conftest import bind_telegram, signed_init_data, student_record

KID = {"id": 5550001, "first_name": "Артём", "username": "artem_kid"}


def twa(client, init_data):
    return client.post("/api/auth/twa", json={"init_data": init_data})


def test_bound_student_gets_session(client):
    bind_telegram(101, KID["id"])
    r = twa(client, signed_init_data(KID))
    assert r.status_code == 200
    me = client.get("/api/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.json()["student"]["name"] == "Иванов Артём"


def test_unbound_account_told_to_scan_card(client):
    r = twa(client, signed_init_data(KID))
    assert r.status_code == 403
    assert "QR" in r.json()["detail"]


def test_tampered_data_rejected(client):
    bind_telegram(101, KID["id"])
    forged = signed_init_data(KID).replace("artem_kid", "hacker")
    assert twa(client, forged).status_code == 401


def test_signature_with_other_bot_token_rejected(client):
    bind_telegram(101, KID["id"])
    assert twa(client, signed_init_data(KID, bot_token="999:other")).status_code == 401


def test_expired_data_rejected(client):
    bind_telegram(101, KID["id"])
    old = int(time.time()) - 2 * 86400
    assert twa(client, signed_init_data(KID, auth_date=old)).status_code == 401


def test_empty_and_unsigned_rejected(client):
    assert twa(client, "").status_code == 401
    assert twa(client, "auth_date=1&user=%7B%7D").status_code == 401


def test_username_refreshed_on_login(client):
    bind_telegram(101, KID["id"], username="old_name")
    twa(client, signed_init_data(KID))
    assert student_record(101)["tg_username"] == "artem_kid"


def test_telegram_photo_avatar_refreshed(client):
    bind_telegram(101, KID["id"])
    from backend import db

    def _set(database):
        db.find_student(database, 101)["avatar"] = {
            "type": "telegram", "value": "https://t.me/i/userpic/320/old.jpg"}
    db.transaction(_set)

    user = {**KID, "photo_url": "https://t.me/i/userpic/320/new.jpg"}
    twa(client, signed_init_data(user))
    assert student_record(101)["avatar"]["value"].endswith("new.jpg")
