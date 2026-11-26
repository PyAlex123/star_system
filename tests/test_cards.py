"""Печатные карточки класса и QR-коды."""
from backend import config
from conftest import student_record


def test_cards_page(teacher):
    r = teacher.get("/cards/1")
    assert r.status_code == 200
    html = r.text
    assert "Иванов Артём" in html and "Петрова София" in html
    assert "Каримов Тимур" not in html, "ученик другого класса"
    assert html.count('class="card"') == 2
    assert student_record(101)["token"] in html
    assert 'src="/api/students/101/qr.png' in html


def test_cards_school_name_from_database(teacher):
    teacher.patch("/api/schools/1", json={"name": "Школа № 7"})
    assert "Школа № 7" in teacher.get("/cards/1").text


def test_cards_missing_class(teacher):
    assert teacher.get("/cards/999").status_code == 404


def test_cards_escape_names(teacher):
    teacher.post("/api/students", json={"class_id": 1, "name": "<script>alert(1)</script>"})
    html = teacher.get("/cards/1").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_qr_png(teacher):
    r = teacher.get("/api/students/101/qr.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers["cache-control"] == "no-store"


def test_qr_missing_student(teacher):
    assert teacher.get("/api/students/999/qr.png").status_code == 404


def test_deep_link_points_to_bot():
    assert config.deep_link("ABCD2345") == "https://t.me/test_stars_bot?start=ABCD2345"


def test_deep_link_without_bot_falls_back_to_site(monkeypatch):
    monkeypatch.setattr(config, "BOT_USERNAME", "")
    assert config.deep_link("ABCD2345") == "http://localhost:8000/app?code=ABCD2345"


def test_cards_eight_per_a4_sheet(teacher):
    """Лист А4 — 2×4 карточки; девятый ученик уходит на второй лист."""
    html = teacher.get("/cards/1").text
    assert html.count('class="a4"') == 1
    assert html.count('class="cell blank"') == 6
    for i in range(7):
        teacher.post("/api/students", json={"class_id": 1, "name": f"Ученик Номер{i}"})
    html = teacher.get("/cards/1").text
    assert html.count('class="card"') == 9
    assert html.count('class="a4"') == 2
    assert "2 листа А4" in html


def test_cards_long_name_gets_smaller_font(teacher):
    teacher.post("/api/students", json={"class_id": 1, "name": "Абдурахмонова-Ибрагимова Мадина"})
    assert 'class="c-name xlong"' in teacher.get("/cards/1").text


def test_cards_hide_changed_password(teacher):
    from conftest import load_db, save_db
    data = load_db()
    next(s for s in data["students"] if s["id"] == 101)["password_shown"] = None
    save_db(data)
    assert "свой пароль" in teacher.get("/cards/1").text


# ── Карточки без бота ────────────────────────────────────────────────────────

def test_telegram_login_on_by_default(teacher):
    assert teacher.get("/api/state").json()["telegram_login"] is True
    html = teacher.get("/cards/1").text
    assert "Код для бота" in html and "Вход на сайте" not in html
    assert 'type="checkbox" checked' in html, "галочка включена"
    assert "Менять может только администратор." not in html, "администратору подсказка не нужна"


def test_cards_without_bot(teacher):
    from backend.routers import cards
    from conftest import load_db
    r = teacher.put("/api/settings/telegram-login", json={"enabled": False})
    assert r.status_code == 200 and r.json()["telegram_login"] is False
    assert teacher.get("/api/state").json()["telegram_login"] is False
    html = teacher.get("/cards/1").text
    assert "Код для бота" not in html and "Вход на сайте" in html
    assert student_record(101)["token"] not in html, "код бота не печатается"
    assert student_record(101)["login"] in html
    assert 'src="/api/students/101/qr.png' in html, "QR остаётся"
    database = load_db()
    target = cards.qr_target(database, student_record(101))
    assert target == config.site_url(f"/enter?login={student_record(101)['login']}")
    assert teacher.get("/api/students/101/qr.png").content == cards._qr_png(target)
    teacher.put("/api/settings/telegram-login", json={"enabled": True})
    assert cards.qr_target(load_db(), student_record(101)) == config.deep_link(student_record(101)["token"])


def test_telegram_login_only_admin(school_teacher):
    assert school_teacher.put("/api/settings/telegram-login", json={"enabled": False}).status_code == 403
    html = school_teacher.get("/cards/1").text
    assert "disabled" in html.split("QR и вход через Telegram-бота")[0][-200:], "учителю галочка недоступна"
    assert "Менять может только администратор" in html, "причина видна сразу, а не только в подсказке"


def test_enter_page_takes_login_from_qr(client):
    html = client.get("/enter?login=ivanov.a").text
    assert 'get("login")' in html
