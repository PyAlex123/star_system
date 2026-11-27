"""Отметки преподавателей, этап 3: API панели, права, поля сотрудников и школ,
срок действия занятий."""
import io
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend import db, stats
from backend.checkin import clock, service, store
from backend.main import app
from conftest import TEACHER_LOGIN, TEACHER_PASSWORD, load_db, panel_client

MON = date(2026, 11, 23)
LAT, LON = 41.311, 69.279


def checkin_routes():
    """Все маршруты раздела — и чтение тоже: там часы, ставки и суммы."""
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/api/checkin") and not path.startswith("/api/checkin/my"):   # кабинет — свой
            for method in methods:
                yield method.upper(), re.sub(r"\{[^}]+\}", "1", path)


def make_school_admin(teacher):
    r = teacher.post("/api/users", json={"login": "director", "name": "Директор", "school_ids": [1],
                                         "password": "direktor-2026", "role": "school_admin"})
    assert r.status_code == 200, r.text
    return r.json()["user"]["id"], panel_client("director", "direktor-2026")


# ── Доступ ───────────────────────────────────────────────────────────────────

def test_checkin_routes_exist():
    routes = set(checkin_routes())
    assert ("GET", "/api/checkin/report") in routes and ("POST", "/api/checkin/lessons/decide") in routes
    assert len(routes) >= 15


def test_only_superadmin_sees_the_section(teacher):
    plain = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    _, director = make_school_admin(teacher)
    for method, path in checkin_routes():
        for who, client in (("учитель", plain), ("администрация школы", director)):
            r = client.request(method, path, json={})
            assert r.status_code == 403, f"{who}: {method} {path} → {r.status_code}"
        assert teacher.request(method, path, json={}).status_code != 403, f"администратор: {method} {path}"

    # Поручения «ведёт отметки» больше нет: учитель с ним из старой базы — тоже 403
    def _apply(database):
        db.find_user(database, 2)["checkin_manager"] = True
    db.transaction(_apply)
    for method, path in checkin_routes():
        assert plain.request(method, path, json={}).status_code == 403, f"бывший ответственный: {method} {path}"


def test_staff_checkin_fields_are_superadmin_only(teacher):
    plain = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert plain.patch("/api/users/2", json={"hourly_rate": 1}).status_code == 403
    assert plain.post("/api/users/2/code").status_code == 403
    assert plain.get("/api/users/2/qr.png").status_code == 403
    assert plain.delete("/api/users/2/telegram").status_code == 403


# ── Сотрудники ───────────────────────────────────────────────────────────────

def test_rate_and_tracking_with_history(teacher):
    r = teacher.patch("/api/users/2", json={"hourly_rate": 90000, "checkin_tracked": True})
    assert r.status_code == 200 and r.json()["user"]["hourly_rate"] == 90000
    assert teacher.patch("/api/users/2", json={"hourly_rate": None}).json()["user"]["hourly_rate"] is None
    assert teacher.patch("/api/users/2", json={"hourly_rate": -5}).status_code == 422
    assert teacher.patch("/api/users/2", json={"name": "Учитель Иванова"}).json()["user"]["checkin_tracked"] is True
    with store.tx() as conn:
        rows = store.rows(conn.execute("SELECT * FROM audit_log WHERE entity = 'user' ORDER BY id"))
    assert [(store.loads(r["before"])["hourly_rate"], store.loads(r["after"])["hourly_rate"]) for r in rows] == \
        [(None, 90000), (90000, None)]
    assert len(rows) == 2, "переименование без полей отметок в историю отметок не пишется"


def test_staff_code_new_code_and_unbind(teacher):
    """Код сотрудника — как карточка ученика: постоянный, «Новый код» отвязывает Telegram,
    «Отвязать» код не меняет."""
    code = db.find_user(load_db(), 2)["bot_code"]
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", code), "8 символов без O/0 и I/1"
    assert code not in str(panel_client(TEACHER_LOGIN, TEACHER_PASSWORD).get("/api/state").json())
    assert code not in str(teacher.get("/api/checkin/teachers").json()), "в разделе отметок кода нет"
    row = next(t for t in teacher.get("/api/checkin/teachers").json()["teachers"] if t["id"] == 2)
    assert row["tg"] == "none"

    def _bind(database):
        db.find_user(database, 2).update(tg_id=900, tg_username="uchitel", consent_at="2026-11-20T04:00:00+00:00")
    db.transaction(_bind)
    row = next(t for t in teacher.get("/api/checkin/teachers").json()["teachers"] if t["id"] == 2)
    assert row["tg_bound"] and row["consent"] and row["tg"] == "bound"

    assert teacher.delete("/api/users/2/telegram").status_code == 200
    saved = db.find_user(load_db(), 2)
    assert saved["tg_id"] is None and saved["consent_at"] is None and saved["bot_code"] == code, "код прежний"

    db.transaction(_bind)
    r = teacher.post("/api/users/2/code")
    assert r.status_code == 200
    data = r.json()
    new = data["code"]
    assert new != code and data["link"] == f"https://t.me/test_stars_bot?start=inv_{new}"
    saved = db.find_user(load_db(), 2)
    assert saved["bot_code"] == new and saved["tg_id"] is None and saved["consent_at"] is None
    assert service.bind_code(code, 901, None)[0] == "not_found", "старая карточка не работает"
    with store.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'new_code'").fetchone()[0] == 1


def test_staff_qr_and_cards(teacher):
    r = teacher.get("/api/users/2/qr.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert teacher.get("/api/users/999/qr.png").status_code == 404
    user = db.find_user(load_db(), 2)
    page = teacher.get("/cards/staff/2")
    assert page.status_code == 200
    html = page.text
    assert f"{user['bot_code'][:4]}" in html and user["login"] in html and "КАРТОЧКА СОТРУДНИКА" in html
    school_page = teacher.get("/cards/school/1/staff")
    assert school_page.status_code == 200 and user["login"] in school_page.text
    assert teacher.get("/cards/school/999/staff").status_code == 404
    plain = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert plain.get("/cards/staff/2").status_code == 404, "учителю — нет: там коды и пароли"
    assert plain.get("/cards/school/1/staff").status_code == 404


def test_staff_card_password_only_until_changed(teacher):
    def _apply(database):
        db.find_user(database, 2)["password_shown"] = "uchitel-2026"
    db.transaction(_apply)
    assert "uchitel-2026" in teacher.get("/cards/staff/2").text

    def _changed(database):
        db.find_user(database, 2)["password_shown"] = None
    db.transaction(_changed)
    html = teacher.get("/cards/staff/2").text
    assert "uchitel-2026" not in html and "свой пароль" in html


# ── Школа на карте ───────────────────────────────────────────────────────────

def test_school_location(teacher):
    r = teacher.put("/api/checkin/schools/1/location",
                    json={"address": "Чиланзар, 5", "lat": LAT, "lon": LON, "radius_m": 200})
    assert r.status_code == 200 and r.json()["school"]["radius_m"] == 200
    school = db.find_school(load_db(), 1)
    assert (school["lat"], school["lon"], school["address"]) == (LAT, LON, "Чиланзар, 5")
    assert teacher.put("/api/checkin/schools/1/location", json={"lat": LAT}).status_code == 400
    assert teacher.put("/api/checkin/schools/1/location", json={"lat": 100, "lon": 1}).status_code == 422
    assert teacher.put("/api/checkin/schools/1/location", json={"radius_m": 10}).status_code == 422
    assert teacher.put("/api/checkin/schools/99/location", json={}).status_code == 404
    with store.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity = 'school'").fetchone()[0] == 1


# ── Срок действия занятий ────────────────────────────────────────────────────

def _slot(**extra):
    return dict({"class_id": 1, "weekdays": [1], "start": "09:00", "end": "09:45"}, **extra)


def test_schedule_validity_and_date_aware_clash(teacher):
    assert teacher.post("/api/schedule", json=_slot(valid_from="2026-12-10", valid_to="2026-12-01")).status_code == 400
    old = teacher.post("/api/schedule", json=_slot(valid_to="2099-12-31"))
    assert old.status_code == 200
    clash = teacher.post("/api/schedule", json=_slot(valid_from="2099-06-01"))
    assert clash.status_code == 400 and "действует по 31.12.2099" in clash.json()["detail"]
    after = teacher.post("/api/schedule", json=_slot(valid_from="2100-01-01"))
    assert after.status_code == 200, "смена расписания с даты: новое занятие сразу после старого"
    assert after.json()["created"][0]["valid_from"] == "2100-01-01"
    assert teacher.post("/api/schedule", json={"class_id": 1, "weekdays": [1], "start": "09:00", "end": "09:45",
                                               "valid_from": "31.12.2099"}).status_code == 422


def test_patch_checks_clashes_too(teacher):
    teacher.post("/api/schedule", json=_slot())
    other = teacher.post("/api/schedule", json=_slot(start="11:00", end="11:45")).json()["created"][0]
    r = teacher.patch(f"/api/schedule/{other['id']}", json={"start": "09:30", "end": "10:15"})
    assert r.status_code == 400, "раньше правка могла положить два занятия друг на друга"
    assert teacher.patch(f"/api/schedule/{other['id']}", json={"valid_to": "2020-01-01"}).status_code == 400
    assert teacher.patch(f"/api/schedule/{other['id']}", json={"valid_to": None}).status_code == 200


def test_closed_entry_is_not_shown_to_students(teacher, student_headers):
    entry = teacher.post("/api/schedule", json=_slot()).json()["created"][0]

    def _close(database):
        found = next(e for e in database["schedule"] if e["id"] == entry["id"])
        found.update(created_at="2026-01-01", valid_to="2026-02-01")
    db.transaction(_close)
    me = TestClient(app).get("/api/me", headers=student_headers(101)).json()
    assert me["schedule"] == []


def test_schedule_plan_stops_at_valid_to():
    database = {"schedule": [{"id": 1, "class_id": 1, "weekday": 1, "start": "09:00", "end": "09:45",
                              "created_at": "2026-11-02", "valid_from": None, "valid_to": "2026-11-16"}],
                "lessons": []}
    assert stats.schedule_plan(database, [1], None, date(2026, 11, 30)) == {"planned": 3, "held": 0}


# ── Сценарий целиком через API ───────────────────────────────────────────────

def test_full_flow_through_api(teacher):
    assert teacher.patch("/api/users/2", json={"checkin_tracked": True, "hourly_rate": 60000}).status_code == 200
    teacher.put("/api/checkin/schools/1/location", json={"lat": LAT, "lon": LON})

    def _schedule(database):
        database["schedule"].append({"id": 50, "class_id": 1, "weekday": 1, "start": "09:00", "end": "09:45",
                                     "room": "", "user_id": 2, "created_at": "2026-01-01",
                                     "valid_from": None, "valid_to": None})
    db.transaction(_schedule)
    clock.freeze(clock.at(MON, "07:00"))
    day = teacher.get("/api/checkin/day?date=2026-11-23").json()
    assert [(l["start"], l["state"], l["teacher"]) for l in day["lessons"]] == [("09:00", "ahead", "Учитель")]
    overview = teacher.get("/api/checkin/overview").json()
    assert overview["counts"]["today"] == 1 and overview["bot"]["enabled"] is True, "бот общий — тот же, что у учеников"

    clock.freeze(clock.at(MON, "09:20"))
    service.tick()
    queue = teacher.get("/api/checkin/queue").json()["lessons"]
    assert [l["status"] for l in queue] == ["no_checkin"]
    lesson_id = queue[0]["id"]

    assert teacher.post("/api/checkin/lessons/decide",
                        json={"lesson_ids": [lesson_id], "action": "approve"}).status_code == 400, "без причины"
    r = teacher.post("/api/checkin/lessons/decide",
                     json={"lesson_ids": [lesson_id], "action": "approve", "reason": "Подтвердил директор"})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert teacher.get("/api/checkin/queue").json()["lessons"] == []

    report = teacher.get("/api/checkin/report?month=2026-11").json()
    assert report["teachers"][0]["hours"] == 0.75 and report["teachers"][0]["amount"] == 45000
    xlsx = teacher.get("/api/checkin/report.xlsx?month=2026-11")
    assert xlsx.status_code == 200 and "hours-2026-11.xlsx" in xlsx.headers["content-disposition"]
    assert load_workbook(io.BytesIO(xlsx.content))["Преподаватели"]["J2"].value == 60000
    assert teacher.get("/api/checkin/report?month=2026-1").status_code == 400

    detail = teacher.get(f"/api/checkin/lessons/{lesson_id}").json()
    assert detail["history"][0]["reason"] == "Подтвердил директор"
    assert teacher.get("/api/checkin/lessons/999").status_code == 404
    assert teacher.get("/api/checkin/audit").json()["items"][0]["action"] == "approve"


def test_exceptions_and_settings_through_api(teacher):
    teacher.patch("/api/users/2", json={"checkin_tracked": True})
    clock.freeze(clock.at(MON, "07:00"))
    r = teacher.post("/api/checkin/exceptions", json={"kind": "holiday", "date_from": "2026-12-31",
                                                      "date_to": "2027-01-02", "reason": "Новый год"})
    assert r.status_code == 200
    listed = teacher.get("/api/checkin/exceptions").json()["exceptions"]
    assert listed[0]["reason"] == "Новый год" and listed[0]["author"] == "Администратор"
    assert teacher.post("/api/checkin/exceptions", json={"kind": "cancel", "date_from": "2026-12-31",
                                                         "reason": "x"}).status_code == 400
    assert teacher.post("/api/checkin/exceptions", json={"kind": "trip", "date_from": "2026-12-31"}).status_code == 422
    assert teacher.delete(f"/api/checkin/exceptions/{r.json()['exception']['id']}").status_code == 200
    assert teacher.delete("/api/checkin/exceptions/999").status_code == 404

    view = teacher.get("/api/checkin/settings").json()
    assert view["values"]["grace_minutes"] == 5 and "report_chat_id" not in view["values"]
    assert {f["key"] for f in view["fields"]} == set(view["values"])
    assert teacher.put("/api/checkin/settings", json={"values": {"grace_minutes": 10}}).json()["values"]["grace_minutes"] == 10
    assert teacher.put("/api/checkin/settings", json={"values": {"grace_minutes": 999}}).status_code == 400
    assert teacher.put("/api/checkin/settings", json={"values": {"report_chat_id": 5}}).status_code == 400


def test_one_off_lesson_through_api(teacher):
    teacher.patch("/api/users/2", json={"checkin_tracked": True})
    clock.freeze(clock.at(MON, "07:00"))
    r = teacher.post("/api/checkin/lessons", json={"user_id": 2, "class_id": 1, "date": "2026-11-23",
                                                   "start": "15:00", "end": "15:45", "reason": "Перенос"})
    assert r.status_code == 200
    day = teacher.get("/api/checkin/day?date=2026-11-23").json()["lessons"]
    assert day[0]["one_off"] is True and day[0]["start"] == "15:00"
    assert teacher.patch(f"/api/checkin/lessons/{r.json()['lesson_id']}",
                         json={"hours": 2, "reason": "Сдвоенный"}).status_code == 200
    assert teacher.post("/api/checkin/lessons", json={"user_id": 2, "class_id": 99, "date": "2026-11-23",
                                                      "start": "15:00", "end": "15:45",
                                                      "reason": "x"}).status_code == 404


def test_healthz_reports_checkin(client):
    data = client.get("/healthz").json()["checkin"]
    assert data["bot_enabled"] is True and data["bot_username"] == "test_stars_bot" and "last_tick" in data
