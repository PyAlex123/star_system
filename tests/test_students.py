"""Ученики: добавление, импорт, перевод, коды, логины и пароли со стороны учителя."""
from conftest import STUDENT_PASSWORD, load_db, student_record, bind_telegram


def test_add_student_gets_code_login_and_password(teacher):
    r = teacher.post("/api/students", json={"class_id": 1, "name": "Новиков Пётр", "total_stars": 7})
    assert r.status_code == 200
    s = student_record(r.json()["student"]["id"])
    assert len(s["token"]) == 8
    assert s["login"] == "novikov.petr"
    assert s["password_hash"].startswith("pbkdf2_sha256$")
    assert s["password_shown"]
    assert s["total_stars"] == 7
    assert s["history"][0]["reason"] == "Стартовый баланс"


def test_add_student_empty_name(teacher):
    assert teacher.post("/api/students", json={"class_id": 1, "name": "  "}).status_code == 400


def test_add_student_missing_class(teacher):
    assert teacher.post("/api/students", json={"class_id": 999, "name": "Кто-то"}).status_code == 404


def test_login_collision_gets_number(teacher):
    teacher.post("/api/students", json={"class_id": 1, "name": "Иванов Артём"})
    logins = [s["login"] for s in load_db()["students"] if s["name"] == "Иванов Артём"]
    assert sorted(logins) == ["ivanov.artem", "ivanov.artem2"]


def test_bulk_import_creates_classes(teacher):
    r = teacher.post("/api/students/bulk", json=[
        {"school_id": 1, "class_name": "9-Г", "name": "Первый Ученик", "total_stars": 3},
        {"school_id": 1, "class_name": "9-Г", "name": "Второй Ученик"},
        {"school_id": 1, "class_name": "5-А", "name": "Третий Ученик"},
        {"school_id": 1, "class_name": "5-А", "name": "   "},
    ])
    assert r.json()["added"] == 3
    data = load_db()
    new_class = next(c for c in data["classes"] if c["name"] == "9-Г")
    assert sum(1 for s in data["students"] if s["class_id"] == new_class["id"]) == 2
    first = next(s for s in data["students"] if s["name"] == "Первый Ученик")
    assert first["history"][0]["reason"] == "Импорт из таблицы"


def test_delete_student(teacher):
    assert teacher.delete("/api/students/101").status_code == 200
    assert not any(s["id"] == 101 for s in load_db()["students"])


def test_move_student_keeps_stars(teacher):
    before = student_record(101)["total_stars"]
    assert teacher.post("/api/students/101/move?class_id=2").status_code == 200
    s = student_record(101)
    assert s["class_id"] == 2 and s["total_stars"] == before


def test_reset_token_changes_code_and_unbinds(teacher):
    bind_telegram(101, 777)
    old = student_record(101)["token"]
    new = teacher.post("/api/students/101/reset-token").json()["token"]
    s = student_record(101)
    assert new != old and s["token"] == new
    assert s["tg_id"] is None


def test_unbind_by_teacher_keeps_code(teacher):
    bind_telegram(101, 777)
    code = student_record(101)["token"]
    assert teacher.post("/api/students/101/unbind").status_code == 200
    s = student_record(101)
    assert s["tg_id"] is None and s["token"] == code


def test_reset_avatar(teacher, client, student_headers):
    from backend import db
    client.post("/api/me/avatar", json={"preset": "dragon"}, headers=student_headers(101))
    assert student_record(101)["avatar"]["value"] == "dragon"
    teacher.post("/api/students/101/reset-avatar")
    assert student_record(101)["avatar"] == db.default_avatar(101)


# ── Логин и пароль глазами учителя ───────────────────────────────────────────

def test_teacher_sets_password(teacher):
    r = teacher.post("/api/students/101/password", json={"password": "proverka123"})
    assert r.json()["password"] == "proverka123"
    assert student_record(101)["password_shown"] == "proverka123"


def test_teacher_generates_password(teacher):
    r = teacher.post("/api/students/101/password", json={})
    generated = r.json()["password"]
    assert len(generated) >= 8 and generated[-4:].isdigit()
    assert student_record(101)["password_shown"] == generated


def test_short_or_spaced_password_rejected(teacher):
    assert teacher.post("/api/students/101/password", json={"password": "abc"}).status_code == 400
    assert teacher.post("/api/students/101/password", json={"password": "два слова"}).status_code == 400


def test_change_login(teacher, client):
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    assert teacher.patch("/api/students/101", json={"login": "Artem.I"}).status_code == 200
    assert student_record(101)["login"] == "artem.i"
    assert client.post("/api/auth/student",
                       json={"login": "artem.i", "password": STUDENT_PASSWORD}).status_code == 200


def test_login_rules(teacher):
    other = student_record(102)["login"]
    assert teacher.patch("/api/students/101", json={"login": other}).status_code == 400
    assert teacher.patch("/api/students/101", json={"login": "иванов"}).status_code == 400
    assert teacher.patch("/api/students/101", json={"login": "ab"}).status_code == 400


def test_rename_student(teacher):
    assert teacher.patch("/api/students/101", json={"name": "Иванов Артемий"}).status_code == 200
    assert student_record(101)["name"] == "Иванов Артемий"
    assert teacher.patch("/api/students/101", json={"name": " "}).status_code == 400
