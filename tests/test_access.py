"""Что закрыто, что открыто, как отдаются страницы и статика."""
import pytest

from conftest import TEACHER_PASSWORD


# ── Закрыто без входа ────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/state"),
    ("POST", "/api/schools"),
    ("POST", "/api/classes"),
    ("POST", "/api/students"),
    ("POST", "/api/students/bulk"),
    ("POST", "/api/lessons/complete"),
    ("POST", "/api/students/deduct"),
    ("POST", "/api/prizes"),
    ("POST", "/api/students/101/password"),
    ("GET", "/api/students/101/qr.png"),
    ("GET", "/api/me"),
    ("POST", "/api/me/avatar"),
    ("POST", "/api/me/unbind"),
    ("POST", "/api/me/password"),
])
def test_api_closed_without_login(client, method, path):
    assert client.request(method, path, json={}).status_code == 401


def test_teacher_page_redirects_to_login(client):
    r = client.get("/teacher", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("/login?next=")


def test_cards_page_redirects_to_login(client):
    r = client.get("/cards/1", follow_redirects=False)
    assert r.status_code == 307


def test_dev_login_disabled(client):
    assert client.post("/api/auth/dev?student_id=101").status_code == 404


def test_preview_requires_teacher(client):
    assert client.post("/api/auth/preview?student_id=101").status_code == 401


# ── Вход учителя ─────────────────────────────────────────────────────────────

def test_teacher_wrong_password(client):
    assert client.post("/api/auth/teacher", json={"password": "nope"}).status_code == 401


def test_teacher_login_sets_httponly_cookie(client):
    r = client.post("/api/auth/teacher", json={"password": TEACHER_PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "teacher_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie


def test_teacher_logout(teacher):
    assert teacher.get("/api/state").status_code == 200
    teacher.post("/api/auth/teacher/logout")
    teacher.cookies.clear()
    assert teacher.get("/api/state").status_code == 401


def test_teacher_cookie_is_not_a_student_session(teacher):
    """Cookie учителя не открывает кабинет ученика."""
    assert teacher.get("/api/me").status_code == 401


# ── Страницы ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,marker", [
    ("/", "Я ученик"),
    ("/login", "Панель учителя"),
    ("/enter", "Вход в кабинет"),
    ("/app", "Открываем твой кабинет"),
])
def test_public_pages(client, path, marker):
    r = client.get(path)
    assert r.status_code == 200
    assert marker in r.text
    assert "{{BASE}}" not in r.text
    assert "{{ICONS}}" not in r.text


def test_teacher_page_renders(teacher):
    r = teacher.get("/teacher")
    assert r.status_code == 200
    assert "Журнал звёзд" in r.text
    assert r.text.count('<symbol id="i-') > 30, "иконки вшиты в страницу"
    assert 'href="/static/css/theme.css?v=' in r.text


def test_pages_not_cached(client):
    assert client.get("/app").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("asset", [
    "/static/css/theme.css", "/static/css/app.css", "/static/css/teacher.css",
    "/static/js/app.js", "/static/js/teacher.js",
    "/static/fonts/golos-cyrillic.woff2", "/static/fonts/literata-cyrillic.woff2",
    "/static/img/avatars/fox.svg", "/static/img/prizes/znatok-999.webp",
    "/static/img/icons.svg",
])
def test_static_served(client, asset):
    assert client.get(asset).status_code == 200


def test_css_js_revalidated_images_cached(client):
    assert client.get("/static/js/app.js").headers["cache-control"] == "no-cache"
    assert "max-age=604800" in client.get("/static/img/avatars/fox.svg").headers["cache-control"]


def test_healthz(client):
    data = client.get("/healthz").json()
    assert data["ok"] is True
    assert data["bot_enabled"] is True
    assert data["bot_mode"] == "polling"
    assert data["base_url"] == "http://localhost:8000/"


def test_webhook_hidden_in_polling_mode(client):
    assert client.post("/telegram/anything", json={}).status_code == 404
