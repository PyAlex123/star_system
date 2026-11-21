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
