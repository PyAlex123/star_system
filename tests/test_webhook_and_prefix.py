"""Режим webhook и работа на подпути (URL_PREFIX)."""
import pytest

from backend import config, main
from backend.bot import bot as bot_module


# ── Webhook ──────────────────────────────────────────────────────────────────

@pytest.fixture
def webhook_on(monkeypatch):
    received = []

    async def fake_feed(payload):
        received.append(payload)

    monkeypatch.setattr(config, "USE_WEBHOOK", True)
    monkeypatch.setattr(bot_module, "feed_webhook_update", fake_feed)
    return received


def test_webhook_wrong_secret_looks_absent(client, webhook_on):
    r = client.post("/telegram/not-the-secret", json={"update_id": 1})
    assert r.status_code == 404
    assert webhook_on == []


def test_webhook_requires_header(client, webhook_on):
    r = client.post(config.WEBHOOK_PATH, json={"update_id": 1})
    assert r.status_code == 403
    assert webhook_on == []


def test_webhook_rejects_non_json(client, webhook_on):
    r = client.post(config.WEBHOOK_PATH, content=b"not json",
                    headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET})
    assert r.status_code == 400


def test_webhook_delivers_update(client, webhook_on):
    r = client.post(config.WEBHOOK_PATH, json={"update_id": 42},
                    headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET})
    assert r.status_code == 200
    assert webhook_on == [{"update_id": 42}]


def test_webhook_secret_derived_from_token():
    assert len(config.WEBHOOK_SECRET) == 40
    assert config.WEBHOOK_PATH == f"/telegram/{config.WEBHOOK_SECRET}"
    assert config.webhook_url() == f"http://localhost:8000/telegram/{config.WEBHOOK_SECRET}"


# ── Один бот: отдельного вебхука для отметок нет ────────────────────────────

def test_no_separate_checkin_webhook(client, webhook_on):
    assert client.post("/telegram/checkin/anything", json={"update_id": 1},
                       headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET}).status_code == 404
    assert webhook_on == []


def test_webhook_survives_broken_update(monkeypatch):
    """Ошибка в обработке не должна превращаться в 500 — Telegram слал бы обновление снова."""
    import asyncio

    monkeypatch.setattr(bot_module, "_bot", object())
    monkeypatch.setattr(bot_module, "_dispatcher", object())
    asyncio.run(bot_module.feed_webhook_update({"update_id": "не число"}))


def test_bot_not_started_without_token(monkeypatch):
    import asyncio

    monkeypatch.setattr(config, "BOT_TOKEN", "")
    monkeypatch.setattr(bot_module, "_bot", None)
    asyncio.run(bot_module.start_bot())
    assert bot_module.get_bot() is None


# ── Подпуть ──────────────────────────────────────────────────────────────────

@pytest.fixture
def prefix(monkeypatch):
    """Система на подпути /stars. nginx срезает префикс, поэтому запросы в тестах
    идут без него, а вот все ссылки наружу обязаны его содержать."""
    main._page_cache.clear()
    monkeypatch.setattr(config, "URL_PREFIX", "/stars")
    yield "/stars"
    main._page_cache.clear()


def test_prefix_in_page_links(client, prefix):
    html = client.get("/login").text
    assert 'href="/stars/static/css/theme.css?v=' in html
    assert 'const BASE = "/stars"' in html


def test_prefix_in_login_redirect(client, prefix):
    r = client.get("/teacher", follow_redirects=False)
    assert r.headers["location"] == "/stars/login?next=/stars/teacher"


def test_prefix_limits_teacher_cookie(client, prefix):
    from conftest import ADMIN_LOGIN, ADMIN_PASSWORD
    r = client.post("/api/auth/teacher", json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD})
    assert "path=/stars" in r.headers["set-cookie"].lower()


def test_prefix_in_cards(teacher, prefix):
    html = teacher.get("/cards/1").text
    assert 'src="/stars/api/students/' in html
    assert 'href="/stars/static/css/cards.css?v=' in html
    assert 'href="/stars/teacher"' in html, "«В журнал» — с подпутём"


def test_prefix_in_external_urls(prefix):
    assert config.webapp_url() == "http://localhost:8000/stars/app"
    assert config.webhook_url().startswith("http://localhost:8000/stars/telegram/")


def test_prefix_in_monitoring(teacher, prefix):
    """«Мониторинг» на подпути: ссылка школы, QR-плакат, страница ученика и её cookie."""
    link = teacher.get("/api/monitoring/link?school_id=1").json()
    assert link["url"] == f"http://localhost:8000/stars/m/{link['code']}"

    poster = teacher.get("/monitoring/poster/1").text
    assert 'href="/stars/teacher"' in poster, "«В журнал» — с подпутём"
    assert f"/stars/m/{link['code']}" in poster

    page = teacher.get(f"/m/{link['code']}")
    assert 'href="/stars/static/css/m.css?v=' in page.text
    assert "path=/stars/m" in page.headers["set-cookie"].lower()

    short = teacher.get("/m", follow_redirects=False)
    assert short.headers["location"] == f"/stars/m/{link['code']}"


def test_static_served_without_prefix_internally(client, prefix):
    """root_path не задан: внутри приложение живёт в корне (см. ARCHITECTURE.md)."""
    assert client.get("/static/css/theme.css").status_code == 200


def test_bad_token_stops_bot_without_retry(monkeypatch):
    """Замороженный или удалённый бот: одна запись в лог и остановка, а не повтор каждую минуту."""
    import asyncio

    from aiogram.exceptions import TelegramUnauthorizedError
    calls = []

    async def bad_configure(bot):
        calls.append(1)
        raise TelegramUnauthorizedError(method=None, message="Unauthorized")

    monkeypatch.setattr(bot_module, "_configure", bad_configure)
    monkeypatch.setattr(bot_module, "_bot", object())
    monkeypatch.setattr(bot_module, "_dispatcher", object())
    monkeypatch.setattr(bot_module, "_status", {"mode": None, "running": False, "last_error": None})
    asyncio.run(asyncio.wait_for(bot_module._run_polling(), 5))
    assert calls == [1] and bot_module.status()["bot_error"] == "TelegramUnauthorizedError"
