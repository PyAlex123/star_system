"""На экранах нет служебных надписей: .env, имён переменных, технических терминов."""
import re

import pytest

from backend import config

SERVICE = re.compile(r"\.env|BOT_TOKEN|BOT_USERNAME|TEACHER_PASSWORD|DEV_LOGIN|initData|JWT|uvicorn",
                     re.IGNORECASE)


def visible_text(html: str) -> str:
    """Текст страницы без скриптов, стилей и комментариев — то, что видит человек."""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


@pytest.mark.parametrize("url", ["/", "/login", "/enter", "/app"])
def test_public_pages_clean(client, url):
    assert not SERVICE.search(visible_text(client.get(url).text))


def test_teacher_pages_clean(teacher):
    assert not SERVICE.search(visible_text(teacher.get("/teacher").text))
    assert not SERVICE.search(visible_text(teacher.get("/cards/1").text))


def test_cards_without_bot_username_have_no_warning(teacher, monkeypatch):
    monkeypatch.setattr(config, "BOT_USERNAME", "")
    assert not SERVICE.search(visible_text(teacher.get("/cards/1").text))


def test_telegram_errors_are_human(client, monkeypatch):
    r = client.post("/api/auth/twa", json={"init_data": "user=1&hash=abc"})
    assert r.status_code == 401
    assert not SERVICE.search(r.json()["detail"])
    monkeypatch.setattr(config, "BOT_TOKEN", "")
    r = client.post("/api/auth/twa", json={"init_data": "x"})
    assert not SERVICE.search(r.json()["detail"])


def test_teacher_script_has_no_bot_status():
    """Шапка журнала больше не пишет «бот не настроен — заполните BOT_TOKEN в .env»."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "js" / "teacher.js").read_text(encoding="utf-8")
    assert "BOT_TOKEN" not in js and ".env" not in js
