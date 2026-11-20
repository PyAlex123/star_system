"""Находки аудита. Каждый тест здесь сначала падал на старом коде."""
import json

import pytest

from backend import config, passwords, ratelimit
from conftest import STUDENT_PASSWORD, bind_telegram, jpeg_bytes, load_db, signed_init_data, student_record


def me_status(client, headers):
    return client.get("/api/me", headers=headers).status_code


# ── 1–2. Сессии: повторный id и отзыв ────────────────────────────────────────

def test_ids_never_reused(teacher):
    teacher.delete("/api/students/104")
    new = teacher.post("/api/students", json={"class_id": 1, "name": "Новый Ученик"}).json()["student"]
    assert new["id"] == 105


def test_deleted_student_session_does_not_open_newcomer(teacher, client, student_headers):
    old = student_headers(104)
    teacher.delete("/api/students/104")
    teacher.post("/api/students", json={"class_id": 1, "name": "Новый Ученик"})
    assert me_status(client, old) == 401


def test_teacher_password_reset_ends_sessions(teacher, client, student_headers):
    h = student_headers(101)
    teacher.post("/api/students/101/password", json={})
    assert me_status(client, h) == 401


def test_new_card_code_ends_sessions(teacher, client, student_headers):
    h = student_headers(101)
    teacher.post("/api/students/101/reset-token")
    assert me_status(client, h) == 401


def test_teacher_unbind_ends_sessions(teacher, client, student_headers):
    bind_telegram(101, 777)
    h = student_headers(101)
    teacher.post("/api/students/101/unbind")
    assert me_status(client, h) == 401


def test_logout_ends_session(client, student_headers):
    h = student_headers(101)
    client.post("/api/me/unbind", headers=h)
    assert me_status(client, h) == 401


def test_own_password_change_keeps_this_device(client, student_headers):
    """Сменил пароль сам — остальные устройства выходят, а текущее получает новую сессию."""
    h = student_headers(101)
    r = client.post("/api/me/password", json={"current": STUDENT_PASSWORD, "new": "moysecret1"}, headers=h)
    assert me_status(client, h) == 401
    fresh = {"Authorization": f"Bearer {r.json()['token']}"}
    assert me_status(client, fresh) == 200


def test_telegram_login_after_logout_works(client):
    bind_telegram(101, 777)
    first = client.post("/api/auth/twa", json={"init_data": signed_init_data({"id": 777})}).json()["token"]
    client.post("/api/me/unbind", headers={"Authorization": f"Bearer {first}"})
    bind_telegram(101, 777)
    r = client.post("/api/auth/twa", json={"init_data": signed_init_data({"id": 777})})
    assert me_status(client, {"Authorization": f"Bearer {r.json()['token']}"}) == 200


# ── 3. Фото детей ────────────────────────────────────────────────────────────

def upload_avatar(client, headers):
    r = client.post("/api/me/photo", files={"file": ("me.jpg", jpeg_bytes(), "image/jpeg")}, headers=headers)
    assert r.status_code == 200
    return r.json()["avatar_url"]


def avatar_files():
    return list(config.AVATARS_UPLOAD_DIR.iterdir())


def test_photo_address_not_guessable(client, student_headers):
    url = upload_avatar(client, student_headers(101))
    assert "101" not in url.split("?")[0]


def test_replaced_photo_deleted(client, student_headers):
    h = student_headers(101)
    first = upload_avatar(client, h)
    upload_avatar(client, h)
    assert len(avatar_files()) == 1
    assert client.get(first).status_code == 404


def test_photo_deleted_when_switching_to_preset(client, student_headers):
    h = student_headers(101)
    upload_avatar(client, h)
    client.post("/api/me/avatar", json={"preset": "fox"}, headers=h)
    assert avatar_files() == []


def test_teacher_reset_avatar_deletes_photo(teacher, client, student_headers):
    """Сброс неуместного фото должен убрать файл, а не только ссылку на него."""
    url = upload_avatar(client, student_headers(101))
    teacher.post("/api/students/101/reset-avatar")
    assert avatar_files() == []
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("remove", [
    lambda t: t.delete("/api/students/101"),
    lambda t: t.delete("/api/classes/1"),
    lambda t: (t.post("/api/schools", json={"name": "Другая"}), t.delete("/api/schools/1")),
])
def test_photo_deleted_with_student(teacher, client, student_headers, remove):
    upload_avatar(client, student_headers(101))
    remove(teacher)
    assert avatar_files() == []


# ── 4, 5, 8. Перебор паролей ─────────────────────────────────────────────────

def fail_login(client, login, headers=None):
    return client.post("/api/auth/student", json={"login": login, "password": "wrong-pass"},
                       headers=headers or {})


def test_forged_forwarded_for_does_not_help(client, monkeypatch):
    monkeypatch.setitem(ratelimit.LIMITS, "student-ip", 5)
    for i in range(5):
        fail_login(client, f"login{i}", {"X-Forwarded-For": f"10.0.0.{i}"})
    assert fail_login(client, "login9", {"X-Forwarded-For": "10.0.0.99"}).status_code == 429


def test_real_ip_header_from_nginx_is_used(client, monkeypatch):
    monkeypatch.setitem(ratelimit.LIMITS, "student-ip", 3)
    for i in range(3):
        fail_login(client, f"login{i}", {"X-Real-IP": "203.0.113.5"})
    assert fail_login(client, "other", {"X-Real-IP": "203.0.113.5"}).status_code == 429
    assert fail_login(client, "other", {"X-Real-IP": "198.51.100.7"}).status_code == 401


def test_own_login_does_not_launder_address(teacher, client, monkeypatch):
    monkeypatch.setitem(ratelimit.LIMITS, "student-ip", 5)
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    for i in range(4):
        fail_login(client, f"victim{i}")
    assert client.post("/api/auth/student",
                       json={"login": "ivanov.artem", "password": STUDENT_PASSWORD}).status_code == 200
    fail_login(client, "victim5")
    assert fail_login(client, "victim6").status_code == 429


def test_classroom_typos_do_not_lock_school(teacher, client):
    """Вся школа за одним адресом: двадцать опечаток у разных детей — не повод блокировать урок."""
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    for i in range(20):
        fail_login(client, f"kid{i}")
    assert client.post("/api/auth/student",
                       json={"login": "ivanov.artem", "password": STUDENT_PASSWORD}).status_code == 200


def test_unknown_login_still_hashes(client, monkeypatch):
    """Иначе по времени ответа видно, существует ли логин."""
    calls = []
    real = passwords.verify_password
    monkeypatch.setattr(passwords, "verify_password", lambda p, s: calls.append(s) or real(p, s))
    fail_login(client, "net.takogo.uchenika")
    assert len(calls) == 1 and calls[0].startswith("pbkdf2_sha256$")


# ── 6. Выдача базы учителю ───────────────────────────────────────────────────

def test_state_has_no_secrets(teacher):
    raw = json.dumps(teacher.get("/api/state").json(), ensure_ascii=False)
    assert "password_hash" not in raw
    assert "session_key" not in raw
    assert "password_shown" in raw, "открытый пароль учителю нужен для карточек"


# ── 7. Аватар из Telegram ────────────────────────────────────────────────────

def test_arbitrary_avatar_url_rejected(client, student_headers):
    h = student_headers(101)
    client.post("/api/me/avatar", json={"use_telegram_photo": True,
                                        "telegram_photo_url": "https://evil.example/track.png"}, headers=h)
    assert "evil.example" not in json.dumps(student_record(101)["avatar"])


def test_telegram_photo_taken_from_signed_data(client):
    bind_telegram(101, 777)
    photo = "https://t.me/i/userpic/320/kid.jpg"
    token = client.post("/api/auth/twa", json={
        "init_data": signed_init_data({"id": 777, "photo_url": photo})}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/me/avatar", json={"use_telegram_photo": True,
                                            "telegram_photo_url": "https://evil.example/x.png"}, headers=h)
    assert r.status_code == 200
    assert student_record(101)["avatar"] == {"type": "telegram", "value": photo}


# ── 9. Заголовки безопасности ────────────────────────────────────────────────

def test_teacher_pages_cannot_be_framed(teacher, client):
    for r in (teacher.get("/"), client.get("/login"), client.get("/enter"), teacher.get("/cards/1")):
        assert r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_cabinet_framed_only_by_telegram(client):
    r = client.get("/app")
    assert "x-frame-options" not in r.headers
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors" in csp and "https://web.telegram.org" in csp
    assert "'none'" not in csp


def test_common_headers(client):
    r = client.get("/static/css/theme.css")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"


# ── 10. Проверка входных данных ──────────────────────────────────────────────

@pytest.mark.parametrize("date", ["вчера", "2026-13-01", "2026-02-30", "", "20.11.2026"])
def test_lesson_date_must_be_real(teacher, date):
    r = teacher.post("/api/lessons/complete",
                     json={"class_id": 1, "date": date, "awards": [{"student_id": 101, "stars": 1}]})
    assert r.status_code == 422


def test_long_names_rejected(teacher):
    long_name = "Я" * 121
    assert teacher.post("/api/students", json={"class_id": 1, "name": long_name}).status_code == 422
    assert teacher.post("/api/schools", json={"name": long_name}).status_code == 422
    assert teacher.post("/api/prizes", json={"name": long_name, "cost": 5}).status_code == 422


def test_import_size_limited(teacher):
    rows = [{"school_id": 1, "class_name": "1-А", "name": f"Ученик {i}"} for i in range(501)]
    assert teacher.post("/api/students/bulk", json=rows).status_code == 422
    from backend import db
    assert not any(s["name"] == "Ученик 0" for s in db.load_db()["students"])


# ── 11. Предпросмотр не меняет аватар ────────────────────────────────────────

def preview(teacher):
    return {"Authorization": f"Bearer {teacher.post('/api/auth/preview?student_id=101').json()['token']}"}


def test_preview_cannot_change_avatar(teacher, client):
    before = student_record(101)["avatar"]
    assert client.post("/api/me/avatar", json={"preset": "ghost"}, headers=preview(teacher)).status_code == 403
    assert student_record(101)["avatar"] == before


def test_preview_cannot_upload_photo(teacher, client):
    r = client.post("/api/me/photo", files={"file": ("x.jpg", jpeg_bytes(), "image/jpeg")},
                    headers=preview(teacher))
    assert r.status_code == 403
    assert avatar_files() == []


# ── Найдено при написании тестов: одна учётка Telegram на две карточки ────────

def test_telegram_account_cannot_take_second_card():
    from backend.bot.handlers import bind_by_code
    bind_by_code(student_record(101)["token"], 7001, "kid")
    status, student = bind_by_code(student_record(102)["token"], 7001, "kid")
    assert status == "other_card"
    assert student["id"] == 101, "сообщаем, к какой карточке аккаунт уже привязан"
    assert student_record(102)["tg_id"] is None
    assert student_record(101)["tg_id"] == 7001
