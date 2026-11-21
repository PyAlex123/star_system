"""Темы оформления: у каждой школы своя, сервер сразу отдаёт страницу в нужной теме."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.main import app
from conftest import ROOT, load_db, save_db

THEMES = ("notebook", "quest", "sky", "clear", "sticker")


def html_theme(html: str) -> str:
    return re.search(r'<html[^>]*data-theme="(\w+)"', html).group(1)


def set_theme(school_id: int, theme: str):
    data = load_db()
    next(s for s in data["schools"] if s["id"] == school_id)["theme"] = theme
    save_db(data)


# ── Данные ────────────────────────────────────────────────────────────────────

def test_new_base_has_default_theme(teacher):
    assert load_db()["schools"][0]["theme"] == "notebook"
    assert load_db()["version"] == db.DB_VERSION == 7


def test_migration_adds_theme_lesson_ids_and_categories(teacher):
    data = load_db()
    data["version"] = 6
    for school in data["schools"]:
        school.pop("theme", None)
    data["prizes"][0]["category"] = "electronics"     # 70 ★
    data["prizes"][2]["category"] = "gadgets"         # 50 ★
    data["lessons"] = [
        {"class_id": 1, "date": "2026-11-20", "total_stars": 3, "students_rewarded": 1},
        {"class_id": 1, "date": "2026-11-19", "total_stars": 2, "students_rewarded": 1},
    ]
    with open(db.config.DB_FILE, "w", encoding="utf-8") as fh:
        import json
        json.dump(data, fh, ensure_ascii=False)
    fresh = db.load_db()
    assert fresh["version"] == 7
    assert all(s["theme"] == "notebook" for s in fresh["schools"])
    assert fresh["prizes"][0]["category"] == "large"
    assert fresh["prizes"][2]["category"] == "medium"
    # новые сверху: старший урок получает меньший номер
    assert [x["id"] for x in fresh["lessons"]] == [2, 1]
    assert fresh["settings"]["last_lesson_id"] == 2


def test_new_school_gets_default_theme(teacher):
    r = teacher.post("/api/schools", json={"name": "Новая"})
    assert r.json()["school"]["theme"] == "notebook"


# ── Кто меняет тему ───────────────────────────────────────────────────────────

def test_admin_sets_theme(teacher):
    r = teacher.put("/api/schools/1/theme", json={"theme": "sky"})
    assert r.status_code == 200, r.text
    assert load_db()["schools"][0]["theme"] == "sky"
    state = teacher.get("/api/state").json()
    assert state["schools"][0]["theme"] == "sky"


def test_teacher_sets_theme_of_own_school(school_teacher):
    assert school_teacher.put("/api/schools/1/theme", json={"theme": "quest"}).status_code == 200
    assert load_db()["schools"][0]["theme"] == "quest"


def test_teacher_cannot_touch_other_school(school_teacher):
    r = school_teacher.put(f"/api/schools/{school_teacher.other['school']}/theme",
                           json={"theme": "quest"})
    assert r.status_code == 404


def test_unknown_theme_rejected(teacher):
    assert teacher.put("/api/schools/1/theme", json={"theme": "neon"}).status_code == 422
    assert teacher.put("/api/schools/1/theme", json={"theme": "<script>"}).status_code == 422


def test_theme_needs_login(client):
    assert client.put("/api/schools/1/theme", json={"theme": "sky"}).status_code == 401


# ── Страницы сразу в нужной теме ──────────────────────────────────────────────

def test_teacher_page_in_school_theme(teacher):
    set_theme(1, "sticker")
    assert html_theme(teacher.get("/teacher").text) == "sticker"


def test_teacher_page_remembers_selected_school(teacher):
    r = teacher.post("/api/schools", json={"name": "Вторая"})
    second = r.json()["school"]["id"]
    teacher.put(f"/api/schools/{second}/theme", json={"theme": "clear"})
    teacher.cookies.set("zv_school", str(second))
    assert html_theme(teacher.get("/teacher").text) == "clear"
    teacher.cookies.set("zv_school", "999")          # чужая или удалённая — первая школа
    assert html_theme(teacher.get("/teacher").text) == "notebook"


def test_teacher_cookie_cannot_show_other_school(school_teacher, teacher):
    teacher.put(f"/api/schools/{school_teacher.other['school']}/theme", json={"theme": "quest"})
    school_teacher.cookies.set("zv_school", str(school_teacher.other["school"]))
    assert html_theme(school_teacher.get("/teacher").text) == "notebook"


def test_cards_page_in_school_theme(teacher):
    set_theme(1, "quest")
    html = teacher.get("/cards/1").text
    assert html_theme(html) == "quest"
    assert "data-light" in html


def test_app_takes_theme_from_link(client):
    assert html_theme(client.get("/app?theme=sky").text) == "sky"


def test_app_ignores_garbage_theme(client):
    html = client.get('/app?theme="><script>').text
    assert html_theme(html) == "notebook"
    assert "<script>\"" not in html


def test_pages_use_device_theme_cookie(client):
    client.cookies.set("zv_theme", "clear")
    client.cookies.set("zv_mode", "dark")
    for url in ("/", "/login", "/enter", "/app"):
        html = client.get(url).text
        assert html_theme(html) == "clear", url
        assert 'data-mode="dark"' in html
    client.cookies.set("zv_theme", "evil")
    client.cookies.set("zv_mode", "evil")
    html = client.get("/login").text
    assert html_theme(html) == "notebook" and 'data-mode="auto"' in html


def test_no_placeholders_left(teacher, client):
    for c, url in ((client, "/"), (client, "/login"), (client, "/enter"), (client, "/app"),
                   (teacher, "/teacher"), (teacher, "/_ui")):
        html = c.get(url).text
        assert "{{" not in html, url


def test_ui_page_only_for_admin(teacher, school_teacher, client):
    assert teacher.get("/_ui?theme=sky").status_code == 200
    assert html_theme(teacher.get("/_ui?theme=sky").text) == "sky"
    assert school_teacher.get("/_ui").status_code == 404
    r = client.get("/_ui", follow_redirects=False)
    assert r.status_code == 307


# ── Ученик узнаёт тему своей школы ────────────────────────────────────────────

def test_me_reports_school_theme(teacher, student_headers):
    set_theme(1, "sky")
    me = TestClient(app).get("/api/me", headers=student_headers(101)).json()
    assert me["student"]["theme"] == "sky"


def test_student_login_returns_theme(teacher):
    set_theme(1, "sticker")
    from conftest import STUDENT_PASSWORD, student_record
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    r = TestClient(app).post("/api/auth/student",
                             json={"login": student_record(101)["login"], "password": STUDENT_PASSWORD})
    assert r.json()["theme"] == "sticker"


def test_preview_returns_theme(teacher):
    set_theme(1, "quest")
    assert teacher.post("/api/auth/preview?student_id=101").json()["theme"] == "quest"


# ── Файлы оформления ──────────────────────────────────────────────────────────

def test_tokens_are_the_designer_file():
    """tokens.css переносится из макета без изменений значений."""
    ours = (ROOT / "static/css/tokens.css").read_text(encoding="utf-8")
    theirs = (ROOT / "docs/design/tokens.css").read_text(encoding="utf-8")
    assert ours == theirs


def test_every_theme_font_is_local():
    """Каждый шрифт из tokens.css объявлен в theme.css и лежит в static/fonts."""
    tokens = (ROOT / "static/css/tokens.css").read_text(encoding="utf-8")
    theme = (ROOT / "static/css/theme.css").read_text(encoding="utf-8")
    families = set(re.findall(r'--font-\w+:\s*"([^"]+)"', tokens))
    assert {"Literata", "Onest", "Nunito", "Unbounded", "Geologica", "Golos Text", "Rubik", "Caveat"} <= families
    declared = set(re.findall(r"font-family:\s*'([^']+)'", theme))
    assert families <= declared, families - declared
    assert "JetBrains Mono" in declared
    for url in re.findall(r"url\('\.\./fonts/([\w.-]+)'\)", theme):
        assert (ROOT / "static/fonts" / url).exists(), url
    assert "fonts.googleapis" not in theme


def test_pages_use_only_new_tokens():
    """Старые переменные (--paper, --ink, --gold…) больше нигде не используются."""
    old = re.compile(r"var\(--(paper|ink|ink-2|gold[\w-]*|r-field|r-btn|r-card|r-pill|warn[\w-]*)\)")
    for path in list((ROOT / "static/css").glob("*.css")) + list((ROOT / "static").glob("*.html")):
        assert not old.search(path.read_text(encoding="utf-8")), path.name


def test_pages_link_tokens_before_theme(client):
    html = client.get("/login").text
    assert html.index("/static/css/tokens.css") < html.index("/static/css/theme.css")
