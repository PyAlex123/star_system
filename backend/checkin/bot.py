"""Бот преподавателей внутри процесса сайта — рядом с ботом учеников, со своим токеном.

Отличия от бота учеников (backend/bot/bot.py), и все — ради того, чтобы отметка не потерялась:
  * обновления обрабатываются по одному (handle_as_tasks=False): геолокация раньше кругляшка;
  * ожидающие обновления при запуске не сбрасываются, webhook при остановке не удаляется —
    отметки, присланные во время перезапуска, придут после него;
  * polling, упавший из-за сети, перезапускается сам (пауза растёт до минуты);
  * настройка идёт фоновой задачей — сайт стартует, не дожидаясь Telegram.
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, Update

from .. import config
from .handlers import build_group_router, build_router

log = logging.getLogger("stars.checkin.bot")

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]
MAX_BACKOFF_SEC = 60

_bot: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None
_task: Optional[asyncio.Task] = None
_status: Dict[str, Any] = {"mode": None, "running": False, "last_error": None}


def get_bot() -> Optional[Bot]:
    """Для отправки карточек и напоминаний (этап 6)."""
    return _bot


async def _configure(bot: Bot) -> None:
    me = await bot.get_me()
    if not config.CHECKIN_BOT_USERNAME and me.username:
        config.CHECKIN_BOT_USERNAME = me.username      # ссылки-приглашения собираются сами
        log.info("Бот отметок: имя определено автоматически — @%s", me.username)
    await bot.set_my_commands([
        BotCommand(command="start", description="Меню / Menyu"),
        BotCommand(command="help", description="Как отмечаться / Qanday belgilash"),
    ])


async def _run_polling() -> None:
    assert _bot and _dispatcher
    delay = 1
    while True:
        try:
            await _configure(_bot)
            _status.update(mode="polling", running=True, last_error=None)
            log.info("Бот отметок запущен (long polling)")
            await _dispatcher.start_polling(_bot, handle_signals=False, close_bot_session=False,
                                            handle_as_tasks=False, allowed_updates=ALLOWED_UPDATES)
            return                                       # остановили сами
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _status.update(running=False, last_error=type(exc).__name__)
            log.exception("Бот отметок остановился, перезапуск через %s с", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SEC)


async def _setup_webhook() -> None:
    assert _bot
    url = config.site_url(config.CHECKIN_WEBHOOK_PATH)
    if not url.startswith("https://"):
        _status.update(last_error="not_https")
        log.error("Бот отметок: USE_WEBHOOK=1, но адрес сайта не https — Telegram его не примет. "
                  "Бот не запущен.")
        return
    delay = 1
    while True:
        try:
            await _configure(_bot)
            await _bot.set_webhook(url=url, secret_token=config.CHECKIN_WEBHOOK_SECRET,
                                   drop_pending_updates=False, allowed_updates=ALLOWED_UPDATES)
            _status.update(mode="webhook", running=True, last_error=None)
            log.info("Бот отметок запущен (webhook)")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _status.update(running=False, last_error=type(exc).__name__)
            log.exception("Бот отметок: webhook не настроился, повтор через %s с", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SEC)


async def start() -> None:
    global _bot, _dispatcher, _task
    if config.CHECKIN_TOKEN_CLASH:
        return                                           # main.py уже написал в лог почему
    if not config.CHECKIN_BOT_ENABLED:
        log.info("Бот отметок не подключён (токен не задан) — отметки работают без него")
        return
    _bot = Bot(token=config.CHECKIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _dispatcher = Dispatcher()
    _dispatcher.include_router(build_router())
    _dispatcher.include_router(build_group_router())
    runner = _setup_webhook() if config.USE_WEBHOOK else _run_polling()
    _task = asyncio.create_task(runner, name="checkin-bot")


async def feed_webhook_update(payload: Dict[str, Any]) -> None:
    """Обновление из webhook. Ошибка обработчика остаётся в логе: ответ Telegram — всегда 200,
    иначе он будет присылать то же обновление снова и снова."""
    if _bot is None or _dispatcher is None:
        log.warning("Бот отметок: пришло обновление, но бот не запущен")
        return
    try:
        update = Update.model_validate(payload, context={"bot": _bot})
        await _dispatcher.feed_update(_bot, update)
    except Exception:
        log.exception("Бот отметок: обновление не обработано")


async def stop() -> None:
    """Webhook не удаляем: Telegram подержит обновления, пока сервер перезапускается."""
    global _bot, _dispatcher, _task
    if _dispatcher is not None and _status.get("mode") == "polling":
        try:
            await _dispatcher.stop_polling()
        except Exception:
            pass
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
    if _bot is not None:
        await _bot.session.close()
        _bot = None
    _dispatcher = None
    _status.update(mode=None, running=False)


def status() -> Dict[str, Any]:
    return {"bot_enabled": config.CHECKIN_BOT_ENABLED, "bot_mode": _status["mode"],
            "bot_running": _status["running"], "bot_error": _status["last_error"],
            "bot_username": config.CHECKIN_BOT_USERNAME or None}
