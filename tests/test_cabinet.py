"""Кабинет ученика: свои данные, рейтинг класса, призы, аватар, выход."""
import json

from backend import config
from conftest import bind_telegram, jpeg_bytes, student_record


def me(client, headers):
    r = client.get("/api/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_cabinet_contents(client, student_headers):
    data = me(client, student_headers(101))
    s = data["student"]
    assert s["name"] == "Иванов Артём"
    assert s["class_name"] == "5-А"
    assert s["school_name"] == "Наша школа"
    assert s["login"] == "ivanov.artem"
    assert len(data["avatar_presets"]) == 24


def test_cabinet_does_not_leak(client, student_headers):
    """Ни кодов карточек, ни паролей, ни Telegram-данных одноклассников."""
    bind_telegram(102, 555, "secret_username")
    raw = json.dumps(me(client, student_headers(101)), ensure_ascii=False)
    assert "password" not in raw
    assert "secret_username" not in raw
    assert student_record(102)["token"] not in raw
    assert "students" not in json.loads(raw)


def test_leaderboard_top5_and_own_row(teacher, client, student_headers):
    for i, stars in enumerate([90, 80, 70, 60, 50, 40]):
        teacher.post("/api/students", json={"class_id": 1, "name": f"Сильный {i}", "total_stars": stars})
    board = me(client, student_headers(101))["leaderboard"]  # у Иванова 34
    assert len(board["top"]) == 5
    assert board["class_size"] == 8
    assert board["my_place"] == 8
    assert board["me"]["is_me"] and board["me"]["place"] == 8
    assert board["gap_to_next"] == 40 - 34 + 1


def test_leaderboard_only_own_class_and_school(teacher, client, student_headers):
    sid = teacher.post("/api/schools", json={"name": "Вторая"}).json()["school"]["id"]
    twin = teacher.post("/api/classes", json={"school_id": sid, "name": "5-А"}).json()["class"]["id"]
    teacher.post("/api/students", json={"class_id": twin, "name": "Чужой Ученик", "total_stars": 999})
    board = me(client, student_headers(101))["leaderboard"]
    assert board["class_size"] == 2
    assert all(row["name"] != "Чужой Ученик" for row in board["top"])


def test_leaderboard_ties_sorted_by_name(teacher, client, student_headers):
    teacher.post("/api/students", json={"class_id": 1, "name": "Абрамов Ян", "total_stars": 52})
    top = me(client, student_headers(101))["leaderboard"]["top"]
    assert [r["name"] for r in top[:2]] == ["Абрамов Ян", "Петрова София"]


def test_prizes_affordable_first(client, student_headers):
    prizes = me(client, student_headers(101))["prizes"]  # 34 звезды
    affordable = [p["cost"] for p in prizes if p["affordable"]]
    assert affordable and all(c <= 34 for c in affordable)
    flags = [p["affordable"] for p in prizes]
    assert flags == sorted(flags, reverse=True), "сначала доступные"


def test_hidden_prize_not_shown(teacher, client, student_headers):
    teacher.patch("/api/prizes/8", json={"active": False})
    assert all(p["id"] != 8 for p in me(client, student_headers(101))["prizes"])


def test_choose_preset_avatar(client, student_headers):
    h = student_headers(101)
    assert client.post("/api/me/avatar", json={"preset": "slime"}, headers=h).status_code == 200
    assert student_record(101)["avatar"] == {"type": "preset", "value": "slime"}
    assert client.post("/api/me/avatar", json={"preset": "hacker"}, headers=h).status_code == 400


def test_upload_own_photo(client, student_headers):
    h = student_headers(101)
    r = client.post("/api/me/photo", files={"file": ("me.jpg", jpeg_bytes((900, 1200)), "image/jpeg")},
                    headers=h)
    assert r.status_code == 200
    assert client.get(r.json()["avatar_url"]).status_code == 200
    assert student_record(101)["avatar"]["type"] == "photo"


def test_self_unbind(client, student_headers):
    bind_telegram(101, 777)
    h = student_headers(101)
    stars = student_record(101)["total_stars"]
    assert client.post("/api/me/unbind", headers=h).status_code == 200
    s = student_record(101)
    assert s["tg_id"] is None and s["tg_username"] is None and s["bound_at"] is None
    assert s["total_stars"] == stars


def test_logout_without_telegram_still_ends_session(client, student_headers):
    """Ребёнок вошёл через сайт: «Выйти» обязано завершить сессию, хотя отвязывать нечего."""
    h = student_headers(101)
    assert student_record(101)["tg_id"] is None
    assert client.post("/api/me/unbind", headers=h).status_code == 200
    assert client.get("/api/me", headers=h).status_code == 401


# ── Предпросмотр учителя ─────────────────────────────────────────────────────

def preview_headers(teacher, student_id=101):
    token = teacher.post(f"/api/auth/preview?student_id={student_id}").json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_preview_opens_cabinet(teacher, client):
    assert client.get("/api/me", headers=preview_headers(teacher)).status_code == 200


def test_preview_cannot_unbind(teacher, client):
    bind_telegram(101, 777)
    r = client.post("/api/me/unbind", headers=preview_headers(teacher))
    assert r.status_code == 403
    assert student_record(101)["tg_id"] == 777


def test_preview_cannot_change_password(teacher, client):
    r = client.post("/api/me/password", json={"current": "x", "new": "podmena1"},
                    headers=preview_headers(teacher))
    assert r.status_code == 403


def test_invalid_token(client):
    assert client.get("/api/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
