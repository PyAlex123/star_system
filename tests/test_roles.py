"""Суперадмин и учителя: вход по логину, права и видимость школ."""
import pytest
from fastapi.testclient import TestClient

from backend import db, ratelimit
from backend.main import app
from conftest import (ADMIN_LOGIN, ADMIN_PASSWORD, TEACHER_LOGIN, TEACHER_PASSWORD, load_db,
                      panel_client, save_db)


# ── Первый запуск и вход ─────────────────────────────────────────────────────

def test_first_start_creates_admin_and_teacher(client):
    client.get("/")                         # любая загрузка базы
    users = {u["login"]: u for u in db.load_db()["users"]}
    assert users[ADMIN_LOGIN]["role"] == "superadmin"
    assert users[TEACHER_LOGIN]["role"] == "teacher"
    assert users[TEACHER_LOGIN]["school_ids"] == [1], "учитель получает все школы, что были"
    assert TEACHER_PASSWORD not in str(users), "пароли — только хешами"


def test_old_database_gets_users_once():
    data = db.load_db()
    data.pop("users")
    data["version"] = 5
    save_db(data)
    assert {u["login"] for u in db.load_db()["users"]} == {ADMIN_LOGIN, TEACHER_LOGIN}
    first = db.load_db()["users"][0]["password_hash"]
    assert db.load_db()["users"][0]["password_hash"] == first, "повторно не пересоздаются"


def test_both_roles_can_log_in():
    assert panel_client(ADMIN_LOGIN, ADMIN_PASSWORD).get("/api/state").json()["me"]["role"] == "superadmin"
    assert panel_client(TEACHER_LOGIN, TEACHER_PASSWORD).get("/api/state").json()["me"]["role"] == "teacher"


def test_login_is_case_insensitive(client):
    r = client.post("/api/auth/teacher", json={"login": " Admin ", "password": ADMIN_PASSWORD})
    assert r.status_code == 200


@pytest.mark.parametrize("login,password", [
    (ADMIN_LOGIN, "wrong"),
    ("nobody", ADMIN_PASSWORD),
])
def test_wrong_login_or_password_same_answer(client, login, password):
    r = client.post("/api/auth/teacher", json={"login": login, "password": password})
    assert r.status_code == 401
    assert r.json()["detail"] == "Неверный логин или пароль"


def test_password_only_login_no_longer_works(client):
    """Старая форма входа (только пароль) больше не пускает."""
    assert client.post("/api/auth/teacher", json={"password": ADMIN_PASSWORD}).status_code == 422


def test_bruteforce_per_login(client):
    for i in range(ratelimit.LIMITS["teacher-login"]):
        client.post("/api/auth/teacher", json={"login": TEACHER_LOGIN, "password": f"x{i}"})
    r = client.post("/api/auth/teacher", json={"login": TEACHER_LOGIN, "password": TEACHER_PASSWORD})
    assert r.status_code == 429
    # Другой логин с того же адреса — пока пускает (лимит адреса мягче)
    assert client.post("/api/auth/teacher",
                       json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}).status_code == 200


def test_old_cookie_without_session_key_rejected(client):
    """Cookie до обновления (sub="teacher", без ключа сессии) не открывает панель."""
    import jwt
    import time
    from backend import config
    old = jwt.encode({"sub": "teacher", "role": "teacher", "iat": int(time.time()),
                      "exp": int(time.time()) + 3600}, config.JWT_SECRET, algorithm="HS256")
    client.cookies.set("teacher_session", old)
    assert client.get("/api/state").status_code == 401


# ── Права учителя ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,url,body", [
    ("post", "/api/schools", {"name": "Новая"}),
    ("patch", "/api/schools/1", {"name": "Другое имя"}),
    ("delete", "/api/schools/1", None),
    ("post", "/api/prizes", {"name": "Приз", "cost": 5}),
    ("patch", "/api/prizes/1", {"cost": 1}),
    ("delete", "/api/prizes/1", None),
    ("post", "/api/users", {"login": "new.t", "name": "Новый"}),
    ("post", "/api/users/1/password", {}),
    ("delete", "/api/users/1", None),
])
def test_teacher_cannot_do_admin_things(school_teacher, method, url, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(school_teacher, method)(url, **kwargs)
    assert r.status_code == 403, (url, r.text)


def test_teacher_sees_only_own_schools(school_teacher, teacher):
    state = school_teacher.get("/api/state").json()
    assert [s["id"] for s in state["schools"]] == [1]
    assert school_teacher.other["student"] not in [s["id"] for s in state["students"]]
    assert "users" not in state, "список учителей — только суперадмину"
    assert "users" in teacher.get("/api/state").json()


def test_state_never_leaks_password_hashes(teacher):
    text = teacher.get("/api/state").text
    assert "password_hash" not in text and "session_key" not in text


@pytest.mark.parametrize("make", [
    lambda o: ("get", f"/api/students/{o['student']}/qr.png", None),
    lambda o: ("get", f"/cards/{o['class']}", None),
    lambda o: ("post", f"/api/auth/preview?student_id={o['student']}", None),
    lambda o: ("post", f"/api/students/{o['student']}/password", {}),
    lambda o: ("post", f"/api/students/{o['student']}/reset-token", None),
    lambda o: ("patch", f"/api/students/{o['student']}", {"name": "Взлом"}),
    lambda o: ("delete", f"/api/students/{o['student']}", None),
    lambda o: ("post", "/api/students/deduct", {"student_id": o["student"], "amount": 1}),
    lambda o: ("post", "/api/students", {"class_id": o["class"], "name": "Подкидыш"}),
    lambda o: ("post", "/api/lessons/complete",
               {"class_id": o["class"], "date": "2026-11-20",
                "awards": [{"student_id": o["student"], "stars": 1}]}),
    lambda o: ("post", "/api/classes", {"school_id": o["school"], "name": "1-А"}),
    lambda o: ("post", "/api/classes/bulk", {"school_id": o["school"], "names": ["1-А"]}),
    lambda o: ("patch", f"/api/classes/{o['class']}", {"name": "Взлом"}),
    lambda o: ("delete", f"/api/classes/{o['class']}", None),
    lambda o: ("delete", f"/api/classes/{o['class']}/students", None),
    lambda o: ("post", "/api/students/bulk",
               [{"school_id": o["school"], "class_name": "1-А", "name": "Подкидыш"}]),
    lambda o: ("get", f"/api/excel/export?school_id={o['school']}", None),
])
def test_other_school_is_invisible(school_teacher, make):
    method, url, body = make(school_teacher.other)
    kwargs = {"json": body} if body is not None else {}
    r = getattr(school_teacher, method)(url, **kwargs)
    assert r.status_code == 404, (url, r.status_code, r.text)
    data = load_db()
    other = next(s for s in data["students"] if s["id"] == school_teacher.other["student"])
    assert other["name"] == "Чужой Ученик" and other["total_stars"] == 0


def test_teacher_cannot_move_class_to_other_school(school_teacher):
    r = school_teacher.patch("/api/classes/1", json={"school_id": school_teacher.other["school"]})
    assert r.status_code == 404
    assert next(c for c in load_db()["classes"] if c["id"] == 1)["school_id"] == 1


def test_teacher_works_in_own_school(school_teacher):
    assert school_teacher.post("/api/classes", json={"school_id": 1, "name": "7-В"}).status_code == 200
    assert school_teacher.post("/api/lessons/complete", json={
        "class_id": 1, "date": "2026-11-20", "awards": [{"student_id": 101, "stars": 2}]}).status_code == 200
    assert school_teacher.post("/api/students/deduct",
                               json={"student_id": 101, "amount": 1}).status_code == 200
    assert school_teacher.get("/cards/1").status_code == 200


def test_removed_school_disappears_immediately(school_teacher, teacher):
    """Снятая школа пропадает у учителя без повторного входа."""
    teacher_id = next(u["id"] for u in load_db()["users"] if u["login"] == TEACHER_LOGIN)
    teacher.patch(f"/api/users/{teacher_id}", json={"school_ids": []})
    assert school_teacher.get("/api/state").json()["schools"] == []
    assert school_teacher.post("/api/students/deduct",
                               json={"student_id": 101, "amount": 1}).status_code == 404


# ── Управление учителями ─────────────────────────────────────────────────────

def test_admin_creates_teacher_who_can_log_in(teacher):
    r = teacher.post("/api/users", json={"login": "Ivanova.M", "name": "Иванова М. А.",
                                         "school_ids": [1]})
    assert r.status_code == 200, r.text
    password = r.json()["password"]
    assert r.json()["user"]["login"] == "ivanova.m"
    assert "password_hash" not in r.json()["user"]
    c = panel_client("ivanova.m", password)
    assert [s["id"] for s in c.get("/api/state").json()["schools"]] == [1]


def test_duplicate_login_rejected(teacher):
    r = teacher.post("/api/users", json={"login": TEACHER_LOGIN, "name": "Двойник"})
    assert r.status_code == 400


def test_unknown_school_rejected(teacher):
    r = teacher.post("/api/users", json={"login": "x.teacher", "name": "Икс", "school_ids": [999]})
    assert r.status_code == 400


def test_reset_password_ends_sessions(school_teacher, teacher):
    teacher_id = next(u["id"] for u in load_db()["users"] if u["login"] == TEACHER_LOGIN)
    r = teacher.post(f"/api/users/{teacher_id}/password", json={"password": "novyi-2026"})
    assert r.json()["password"] == "novyi-2026"
    assert school_teacher.get("/api/state").status_code == 401
    panel_client(TEACHER_LOGIN, "novyi-2026")


def test_deleted_teacher_is_logged_out(school_teacher, teacher):
    teacher_id = next(u["id"] for u in load_db()["users"] if u["login"] == TEACHER_LOGIN)
    assert teacher.delete(f"/api/users/{teacher_id}").status_code == 200
    assert school_teacher.get("/api/state").status_code == 401


def test_admin_cannot_delete_self(teacher):
    admin_id = next(u["id"] for u in load_db()["users"] if u["login"] == ADMIN_LOGIN)
    assert teacher.delete(f"/api/users/{admin_id}").status_code == 400
    assert any(u["login"] == ADMIN_LOGIN for u in load_db()["users"])


def test_deleting_school_unassigns_teachers(teacher, school_teacher):
    other = school_teacher.other["school"]
    teacher_id = next(u["id"] for u in load_db()["users"] if u["login"] == TEACHER_LOGIN)
    teacher.patch(f"/api/users/{teacher_id}", json={"school_ids": [1, other]})
    teacher.delete(f"/api/schools/{other}")
    assert db.find_user(db.load_db(), teacher_id)["school_ids"] == [1]


# ── Свой пароль ──────────────────────────────────────────────────────────────

def test_change_own_password_keeps_current_session(school_teacher):
    r = school_teacher.post("/api/auth/teacher/password",
                            json={"current": TEACHER_PASSWORD, "new": "moy-parol-1"})
    assert r.status_code == 200, r.text
    assert school_teacher.get("/api/state").status_code == 200, "новая cookie выдана"
    panel_client(TEACHER_LOGIN, "moy-parol-1")
    r = TestClient(app).post("/api/auth/teacher",
                             json={"login": TEACHER_LOGIN, "password": TEACHER_PASSWORD})
    assert r.status_code == 401


def test_other_sessions_end_after_own_password_change(school_teacher):
    second = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    school_teacher.post("/api/auth/teacher/password",
                        json={"current": TEACHER_PASSWORD, "new": "moy-parol-1"})
    assert second.get("/api/state").status_code == 401


def test_change_password_needs_current(school_teacher):
    r = school_teacher.post("/api/auth/teacher/password", json={"current": "x", "new": "moy-parol-1"})
    assert r.status_code == 400
    r = school_teacher.post("/api/auth/teacher/password", json={"current": TEACHER_PASSWORD, "new": "123"})
    assert r.status_code == 400
