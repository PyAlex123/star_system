"""Администрация школы только смотрит; пароль сотрудника виден администратору до первой смены."""
import re

import pytest

from backend.main import app
from conftest import load_db, panel_client

SA_LOGIN, SA_PASSWORD = "director", "direktor-2026"

# Не панель: вход, кабинет ученика, вебхук бота. Смена своего пароля и выход — всем ролям.
ALLOWED_FOR_ALL = {"/api/auth/teacher", "/api/auth/teacher/password", "/api/auth/teacher/logout"}
# /api/m/… — страница опроса ученика: без входа, по ссылке школы.
NOT_PANEL = re.compile(r"^/api/(me|m|telegram|auth/(twa|dev|student))(/|$)")


@pytest.fixture
def school_admin(teacher):
    r = teacher.post("/api/users", json={"login": SA_LOGIN, "name": "Директор Иванова",
                                         "school_ids": [1], "password": SA_PASSWORD,
                                         "role": "school_admin"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "school_admin"
    return panel_client(SA_LOGIN, SA_PASSWORD)


def mutating_routes():
    """Все маршруты API, которые что-то меняют, — из описания приложения (OpenAPI)."""
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api/") or path in ALLOWED_FOR_ALL or NOT_PANEL.match(path):
            continue
        for method in methods:
            if method.upper() not in ("GET", "HEAD"):
                yield method.upper(), re.sub(r"\{[^}]+\}", "1", path)


def test_route_list_is_not_empty():
    routes = list(mutating_routes())
    assert len(routes) > 25
    assert ("POST", "/api/lessons/complete") in routes
    assert ("POST", "/api/schedule") in routes


def test_school_admin_cannot_change_anything(school_admin):
    """Перебор всех изменяющих маршрутов панели: новый маршрут без явного
    разрешения тоже закрыт для администрации школы."""
    for method, path in mutating_routes():
        r = school_admin.request(method, path, json={})
        assert r.status_code == 403, f"{method} {path}: {r.status_code}"


def test_school_admin_cannot_see_login_data(school_admin):
    for path in ("/api/students/101/qr.png", "/api/excel/export?school_id=1", "/api/excel/template"):
        assert school_admin.get(path).status_code == 403, path
    assert school_admin.get("/cards/1").status_code == 403
    state = school_admin.get("/api/state").json()
    assert state["students"], "учеников видно"
    for s in state["students"]:
        for field in ("token", "login", "password_shown", "password_hash", "tg_id", "session_key"):
            assert field not in s, field
    assert "users" not in state


def test_school_admin_sees_own_school_only(school_admin, teacher):
    other = teacher.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    assert school_admin.get(f"/api/stats?school_id={other}").status_code == 404
    assert school_admin.get("/api/stats?school_id=1").status_code == 200
    assert school_admin.get("/teacher").status_code == 200
    assert [s["id"] for s in school_admin.get("/api/state").json()["schools"]] == [1]


def test_school_admin_changes_own_password(school_admin):
    r = school_admin.post("/api/auth/teacher/password", json={"current": SA_PASSWORD, "new": "novyi-parol-1"})
    assert r.status_code == 200


def test_new_staff_password_visible_to_admin_until_changed(teacher):
    r = teacher.post("/api/users", json={"login": "ivanova", "name": "Иванова", "school_ids": [1]})
    password = r.json()["password"]
    user_id = r.json()["user"]["id"]
    users = {u["login"]: u for u in teacher.get("/api/state").json()["users"]}
    assert users["ivanova"]["password_shown"] == password
    assert "password_hash" not in users["ivanova"]

    # Сам учитель и коллеги пароля не видят
    t = panel_client("ivanova", password)
    state = t.get("/api/state").json()
    assert "users" not in state and password not in str(state)

    # Сменил сам — администратор больше не видит
    t.post("/api/auth/teacher/password", json={"current": password, "new": "svoi-parol-9"})
    users = {u["login"]: u for u in teacher.get("/api/state").json()["users"]}
    assert users["ivanova"]["password_shown"] is None

    # Новый пароль от администратора снова виден
    fresh = teacher.post(f"/api/users/{user_id}/password", json={}).json()["password"]
    users = {u["login"]: u for u in teacher.get("/api/state").json()["users"]}
    assert users["ivanova"]["password_shown"] == fresh


def test_role_can_be_changed_but_not_for_admin(teacher, school_admin):
    sa = next(u for u in load_db()["users"] if u["login"] == SA_LOGIN)
    r = teacher.patch(f"/api/users/{sa['id']}", json={"role": "teacher"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "teacher"
    assert school_admin.get("/api/state").status_code == 401, "права сменились — нужен новый вход"
    admin = next(u for u in load_db()["users"] if u["role"] == "superadmin")
    assert teacher.patch(f"/api/users/{admin['id']}", json={"role": "teacher"}).status_code == 400
    assert teacher.post("/api/users", json={"login": "x1x", "name": "X", "role": "superadmin"}).status_code == 422


def test_teacher_sees_colleague_names_only(school_teacher):
    state = school_teacher.get("/api/state").json()
    assert "users" not in state
    assert all(set(u) == {"id", "name", "role"} for u in state["staff"])
