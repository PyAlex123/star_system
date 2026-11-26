"""Администрация школы: смотрит статистику, меняет оформление школы, добавляет свои призы и печатает
карточки учеников; журнала и звёзд у неё нет. Пароль сотрудника виден администратору до первой смены."""
import re

import pytest

from backend.main import app
from conftest import load_db, panel_client

SA_LOGIN, SA_PASSWORD = "director", "direktor-2026"

# Не панель: вход, кабинет ученика, вебхук бота. Смена своего пароля и выход — всем ролям.
ALLOWED_FOR_ALL = {"/api/auth/teacher", "/api/auth/teacher/password", "/api/auth/teacher/logout",
                   "/api/auth/teacher/photo"}   # своё фото в профиле — всем ролям
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


# Что администрации школы можно менять — проверяется отдельными тестами ниже
SCHOOL_ADMIN_MAY = {"/api/schools/1/theme", "/api/prizes", "/api/prizes/1", "/api/prizes/1/photo"}


def test_school_admin_cannot_change_anything(school_admin):
    """Перебор всех изменяющих маршрутов панели: новый маршрут без явного
    разрешения тоже закрыт для администрации школы."""
    for method, path in mutating_routes():
        if path in SCHOOL_ADMIN_MAY:
            continue
        r = school_admin.request(method, path, json={})
        assert r.status_code == 403, f"{method} {path}: {r.status_code}"


def test_school_admin_prints_cards_but_no_journal_data(school_admin, teacher):
    """Карточки для печати — да (там логины и пароли); в самой панели данных входа нет."""
    assert school_admin.get("/cards/1").status_code == 200
    html = school_admin.get("/cards/1").text
    assert "Иванов Артём" in html and 'href="/teacher#cards"' in html, "назад — к разделу «Карточки»"
    assert school_admin.get("/api/students/101/qr.png").status_code == 200
    other = teacher.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    cid = teacher.post("/api/classes", json={"school_id": other, "name": "9-Я"}).json()["class"]["id"]
    sid = teacher.post("/api/students", json={"class_id": cid, "name": "Чужой"}).json()["student"]["id"]
    assert school_admin.get(f"/cards/{cid}").status_code == 404, "чужая школа — как несуществующая"
    assert school_admin.get(f"/api/students/{sid}/qr.png").status_code == 404
    for path in ("/api/excel/export?school_id=1", "/api/excel/template"):
        assert school_admin.get(path).status_code == 403, path
    assert school_admin.post("/api/lessons/complete", json={"class_id": 1, "date": "2026-11-20",
                                                            "awards": [{"student_id": 101, "stars": 1}]}).status_code == 403
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
    assert all(set(u) == {"id", "name", "role", "avatar"} for u in state["staff"])


# ── Фото в профиле сотрудника ────────────────────────────────────────────────

def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 120, 40)).save(buf, "PNG")
    return buf.getvalue()


def test_staff_photo_upload_and_drop(teacher, school_admin):
    from backend import images
    r = school_admin.post("/api/auth/teacher/photo", files={"file": ("me.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["avatar"]["value"]
    path = images.upload_path(url)
    assert path.exists(), "фото сохранено"
    me = school_admin.get("/api/state").json()["me"]
    assert me["avatar"] == {"type": "photo", "value": url}
    sa = next(u for u in teacher.get("/api/state").json()["users"] if u["login"] == SA_LOGIN)
    assert sa["avatar"]["value"] == url, "администратор видит фото в «Сотрудниках»"

    again = school_admin.post("/api/auth/teacher/photo", files={"file": ("me.png", _png(), "image/png")}).json()
    assert not path.exists(), "прежний файл удалён"
    assert school_admin.delete("/api/auth/teacher/photo").json()["avatar"] is None
    assert not images.upload_path(again["avatar"]["value"]).exists()
    assert school_admin.get("/api/state").json()["me"]["avatar"] is None


def test_staff_photo_rejects_non_image(school_admin):
    r = school_admin.post("/api/auth/teacher/photo", files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 400


def test_admin_drops_staff_photo_and_file_goes_with_user(teacher, school_admin):
    from backend import images
    url = school_admin.post("/api/auth/teacher/photo",
                            files={"file": ("me.png", _png(), "image/png")}).json()["avatar"]["value"]
    sa = next(u for u in load_db()["users"] if u["login"] == SA_LOGIN)
    assert school_admin.delete(f"/api/users/{sa['id']}/photo").status_code == 403, "чужое фото — только администратор"
    r = teacher.delete(f"/api/users/{sa['id']}/photo")
    assert r.status_code == 200 and r.json()["user"]["avatar"] is None
    assert not images.upload_path(url).exists()

    url = school_admin.post("/api/auth/teacher/photo",
                            files={"file": ("me.png", _png(), "image/png")}).json()["avatar"]["value"]
    assert teacher.delete(f"/api/users/{sa['id']}").status_code == 200
    assert not images.upload_path(url).exists(), "удалили сотрудника — удалили и фото"


def test_colleague_sees_staff_avatar(teacher, school_teacher, school_admin):
    url = school_admin.post("/api/auth/teacher/photo",
                            files={"file": ("me.png", _png(), "image/png")}).json()["avatar"]["value"]
    staff = {u["name"]: u for u in school_teacher.get("/api/state").json()["staff"]}
    assert staff["Директор Иванова"]["avatar"]["value"] == url



# ── Оформление и призы администрации школы ───────────────────────────────────

def test_school_admin_sets_theme_of_own_school(school_admin, teacher):
    assert school_admin.put("/api/schools/1/theme", json={"theme": "quest"}).status_code == 200
    assert load_db()["schools"][0]["theme"] == "quest"
    other = teacher.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    assert school_admin.put(f"/api/schools/{other}/theme", json={"theme": "sky"}).status_code == 404


def test_school_admin_adds_own_prizes_only(school_admin, teacher, school_teacher, student_headers):
    from fastapi.testclient import TestClient
    r = school_admin.post("/api/prizes", json={"name": "Грамота директора", "cost": 15})
    assert r.status_code == 200, r.text
    prize = r.json()["prize"]
    assert prize["school_id"] == 1, "одна школа — приз её"
    assert school_admin.patch(f"/api/prizes/{prize['id']}", json={"cost": 20}).status_code == 200

    r = school_admin.patch("/api/prizes/1", json={"cost": 1})
    assert r.status_code == 403 and "администратор системы" in r.json()["detail"], "общие призы — нельзя"
    assert school_admin.delete("/api/prizes/1").status_code == 403
    assert next(p for p in load_db()["prizes"] if p["id"] == 1)["cost"] != 1

    # Приз виден детям этой школы и учителю этой школы, с пометкой
    me = TestClient(app).get("/api/me", headers=student_headers(101)).json()
    mine = next(p for p in me["prizes"] if p["id"] == prize["id"])
    assert mine["from_school"] is True
    assert all(p["from_school"] is False for p in me["prizes"] if p["id"] != prize["id"])

    # Другая школа приза не видит и не может его выдать
    other = school_teacher.other
    state = school_teacher.get("/api/state").json()
    assert any(p["id"] == prize["id"] for p in state["prizes"]), "учитель этой школы видит"
    r = teacher.post("/api/students/deduct", json={"student_id": other["student"], "amount": 1, "prize_id": prize["id"]})
    assert r.status_code == 400, "приз другой школы ученику другой школы не выдать"
    assert teacher.post("/api/students/deduct", json={"student_id": 101, "amount": 1,
                                                       "prize_id": prize["id"]}).status_code == 200
    assert school_admin.delete(f"/api/prizes/{prize['id']}").status_code == 200


def test_prize_of_other_school_invisible(teacher, school_admin):
    other = teacher.post("/api/schools", json={"name": "Другая"}).json()["school"]["id"]
    r = teacher.post("/api/prizes", json={"name": "Только там", "cost": 5, "school_id": other})
    assert r.status_code == 200 and r.json()["prize"]["school_id"] == other
    pid = r.json()["prize"]["id"]
    assert all(p["id"] != pid for p in school_admin.get("/api/state").json()["prizes"])
    assert school_admin.patch(f"/api/prizes/{pid}", json={"cost": 1}).status_code == 404
    assert school_admin.post("/api/prizes", json={"name": "Туда", "cost": 5, "school_id": other}).status_code == 404
    assert any(p["id"] == pid for p in teacher.get("/api/state").json()["prizes"]), "администратор видит все"


def test_teacher_still_cannot_edit_prizes(school_teacher):
    assert school_teacher.post("/api/prizes", json={"name": "Приз", "cost": 5}).status_code == 403
