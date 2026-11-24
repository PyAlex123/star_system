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


# ── Webhook бота отметок ─────────────────────────────────────────────────────

CHECKIN_SECRET = "c" * 40


@pytest.fixture
def checkin_webhook_on(monkeypatch):
    from backend.checkin import bot as checkin_bot
    received = []

    async def fake_feed(payload):
        received.append(payload)

    monkeypatch.setattr(config, "USE_WEBHOOK", True)
    monkeypatch.setattr(config, "CHECKIN_WEBHOOK_SECRET", CHECKIN_SECRET)
    monkeypatch.setattr(checkin_bot, "feed_webhook_update", fake_feed)
    return received


def test_checkin_webhook_hidden_without_bot(client, monkeypatch):
    monkeypatch.setattr(config, "USE_WEBHOOK", True)
    assert config.CHECKIN_WEBHOOK_SECRET == "", "в тестах бот отметок не подключён"
    assert client.post("/telegram/checkin/anything", json={}).status_code == 404


def test_checkin_webhook_protections(client, checkin_webhook_on):
    good = {"X-Telegram-Bot-Api-Secret-Token": CHECKIN_SECRET}
    assert client.post("/telegram/checkin/wrong", json={}, headers=good).status_code == 404
    assert client.post(f"/telegram/checkin/{CHECKIN_SECRET}", json={}).status_code == 403
    assert client.post(f"/telegram/checkin/{CHECKIN_SECRET}", json={"update_id": 1},
                       headers={"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET}).status_code == 403, \
        "секрет бота учеников к боту отметок не подходит"
    assert client.post(f"/telegram/checkin/{CHECKIN_SECRET}", content=b"x", headers=good).status_code == 400
    r = client.post(f"/telegram/checkin/{CHECKIN_SECRET}", json={"update_id": 7}, headers=good)
    assert r.status_code == 200 and checkin_webhook_on == [{"update_id": 7}]


def test_checkin_webhook_survives_broken_update(monkeypatch):
    """Ошибка в обработке не должна превращаться в 500 — Telegram слал бы обновление снова."""
    import asyncio

    from backend.checkin import bot as checkin_bot
    monkeypatch.setattr(checkin_bot, "_bot", object())
    monkeypatch.setattr(checkin_bot, "_dispatcher", object())
    asyncio.run(checkin_bot.feed_webhook_update({"update_id": "не число"}))


def test_checkin_bot_not_started_without_token():
    import asyncio

    from backend.checkin import bot as checkin_bot
    asyncio.run(checkin_bot.start())
    assert checkin_bot.get_bot() is None and checkin_bot.status()["bot_enabled"] is False


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


def test_static_served_without_prefix_internally(client, prefix):
    """root_path не задан: внутри приложение живёт в корне (см. ARCHITECTURE.md)."""
    assert client.get("/static/css/theme.css").status_code == 200
