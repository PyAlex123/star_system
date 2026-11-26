"""Общая основа тестов.

Окружение выставляется ДО импорта приложения: настройки читаются один раз
при импорте backend.config. Рабочая база, .env и загруженные фото не участвуют —
всё живёт во временном каталоге и создаётся заново перед каждым тестом.
"""
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_BOT_TOKEN = "123456:TEST-token-only-for-tests"
TEACHER_PASSWORD = "teacher-pass"
ADMIN_PASSWORD = "admin-pass"
ADMIN_LOGIN = "admin"
TEACHER_LOGIN = "teacher"
STUDENT_PASSWORD = "secret-123"

_DATA_DIR = tempfile.mkdtemp(prefix="stars-tests-")

# python-dotenv не перезаписывает уже заданные переменные, поэтому значения
# из настоящего .env сюда не просочатся — даже пустые.
os.environ.update({
    "DATA_DIR": _DATA_DIR,
    "BASE_URL": "http://localhost:8000",
    "URL_PREFIX": "",
    "BOT_TOKEN": TEST_BOT_TOKEN,
    "BOT_USERNAME": "test_stars_bot",
    "USE_WEBHOOK": "0",
    "TEACHER_PASSWORD": TEACHER_PASSWORD,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "ADMIN_LOGIN": ADMIN_LOGIN,
    "TEACHER_LOGIN": TEACHER_LOGIN,
    "JWT_SECRET": "jwt-secret-only-for-tests-long-enough-for-hs256",
    "DEV_LOGIN": "0",
    "SCHOOL_NAME": "Тестовая школа",
    "TZ_NAME": "Asia/Tashkent",
})

from fastapi.testclient import TestClient  # noqa: E402

from backend import config, db, passwords, ratelimit  # noqa: E402
from backend.checkin import clock as checkin_clock, store as checkin_store  # noqa: E402
from backend.main import app  # noqa: E402
from backend.monitoring import store as monitoring_store  # noqa: E402
from backend.routers import m_public  # noqa: E402

# Боевые 120 000 итераций делают каждый тест на полсекунды дольше.
# Проверка хеша читает число итераций из самой записи, поэтому совместимость
# с настоящими хешами не страдает.
passwords.ITERATIONS = 2_000


# ── Изоляция ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_state():
    """Чистая база, пустые загрузки и обнулённые счётчики перед каждым тестом."""
    if config.DB_FILE.exists():
        config.DB_FILE.unlink()
    shutil.rmtree(config.UPLOADS_DIR, ignore_errors=True)
    for directory in (config.AVATARS_UPLOAD_DIR, config.PRIZES_UPLOAD_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    ratelimit._failures.clear()
    checkin_store.reset_for_tests()
    checkin_clock.freeze(None)
    # «Мониторинг»: своя SQLite рядом с базой — тоже чистая перед каждым тестом
    for suffix in ("", "-wal", "-shm"):
        Path(f"{monitoring_store.db_file()}{suffix}").unlink(missing_ok=True)
    monitoring_store.forget()
    m_public._roster.update(stamp=None, by_class={})
    yield
    checkin_clock.freeze(None)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


# ── Клиенты ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """Клиент без входа. Жизненный цикл приложения не запускается — бот не стартует."""
    return TestClient(app)


def panel_client(login: str, password: str) -> TestClient:
    """Клиент, вошедший в панель (cookie хранится внутри клиента)."""
    c = TestClient(app)
    r = c.post("/api/auth/teacher", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def teacher():
    """Панель с полными правами — суперадмин. Большинству тестов важны действия,
    а не роли; ограничения учителя проверяются отдельно (test_roles.py)."""
    return panel_client(ADMIN_LOGIN, ADMIN_PASSWORD)


@pytest.fixture
def school_teacher(teacher):
    """Учитель только школы 1. Вторая школа с классом и учеником для проверки,
    что чужое ему не видно."""
    r = teacher.post("/api/schools", json={"name": "Чужая школа"})
    other_school = r.json()["school"]["id"]
    r = teacher.post("/api/classes", json={"school_id": other_school, "name": "9-Я"})
    other_class = r.json()["class"]["id"]
    r = teacher.post("/api/students", json={"class_id": other_class, "name": "Чужой Ученик"})
    other_student = r.json()["student"]["id"]
    c = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    c.other = {"school": other_school, "class": other_class, "student": other_student}
    return c


@pytest.fixture
def student_headers(teacher):
    """Заголовки с сессией ученика, полученной честным входом через сайт.

    Учитель выдаёт известный пароль, ученик входит по логину — так тест не
    зависит от того, как именно устроен токен внутри.
    """
    def make(student_id: int = 101):
        r = teacher.post(f"/api/students/{student_id}/password",
                         json={"password": STUDENT_PASSWORD})
        assert r.status_code == 200, r.text
        login = student_record(student_id)["login"]
        r = TestClient(app).post("/api/auth/student",
                                 json={"login": login, "password": STUDENT_PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}
    return make


# ── Помощники ────────────────────────────────────────────────────────────────

def load_db():
    with open(config.DB_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save_db(data):
    db.save_db(data)


def student_record(student_id: int):
    return next(s for s in db.load_db()["students"] if s["id"] == student_id)


def jpeg_bytes(size=(400, 300), color=(90, 160, 120)) -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def signed_init_data(user: dict, auth_date: int = None, bot_token: str = TEST_BOT_TOKEN) -> str:
    """initData так, как её подписывает Telegram."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHtest",
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def bind_telegram(student_id: int, tg_id: int, username: str = "kid"):
    """Привязать Telegram к ученику напрямую в базе — как после сканирования QR."""
    def _apply(database):
        s = db.find_student(database, student_id)
        s["tg_id"], s["tg_username"], s["bound_at"] = tg_id, username, db.now_iso()
    db.transaction(_apply)
