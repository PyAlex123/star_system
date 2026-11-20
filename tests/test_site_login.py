"""Вход через сайт по логину и паролю, смена пароля ребёнком."""
from conftest import STUDENT_PASSWORD, student_record


def login(client, login, password):
    return client.post("/api/auth/student", json={"login": login, "password": password})


def test_every_student_has_credentials(teacher):
    students = teacher.get("/api/state").json()["students"]
    assert all(s.get("login") for s in students)
    assert len({s["login"] for s in students}) == len(students)


def test_generated_password_works(client):
    s = student_record(101)
    assert login(client, s["login"], s["password_shown"]).status_code == 200


def test_login_case_insensitive(teacher, client):
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    assert login(client, "  Ivanov.ARTEM ", STUDENT_PASSWORD).status_code == 200


def test_wrong_password_and_unknown_login_look_the_same(teacher, client):
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    wrong = login(client, "ivanov.artem", "неверный")
    unknown = login(client, "net.takogo", "неверный")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_child_changes_password(client, student_headers):
    h = student_headers(101)
    assert client.post("/api/me/password", json={"current": "мимо", "new": "moysecret1"},
                       headers=h).status_code == 401
    assert client.post("/api/me/password", json={"current": STUDENT_PASSWORD, "new": "abc"},
                       headers=h).status_code == 400
    assert client.post("/api/me/password", json={"current": STUDENT_PASSWORD, "new": "moysecret1"},
                       headers=h).status_code == 200

    assert login(client, "ivanov.artem", STUDENT_PASSWORD).status_code == 401
    assert login(client, "ivanov.artem", "moysecret1").status_code == 200
    assert student_record(101)["password_shown"] is None, "учитель больше не видит пароль"


def test_password_never_stored_in_clear_after_change(client, student_headers):
    h = student_headers(101)
    client.post("/api/me/password", json={"current": STUDENT_PASSWORD, "new": "moysecret1"}, headers=h)
    from conftest import load_db
    import json
    assert "moysecret1" not in json.dumps(load_db(), ensure_ascii=False)


def test_cards_show_login_and_password(teacher):
    teacher.post("/api/students/101/password", json={"password": "kartochka1"})
    html = teacher.get("/cards/1").text
    assert "ivanov.artem" in html
    assert "kartochka1" in html
    assert "/enter" in html


def test_cards_hide_password_changed_by_child(teacher, client, student_headers):
    h = student_headers(101)
    client.post("/api/me/password", json={"current": STUDENT_PASSWORD, "new": "moysecret1"}, headers=h)
    html = teacher.get("/cards/1").text
    assert "moysecret1" not in html
    assert "свой пароль" in html
