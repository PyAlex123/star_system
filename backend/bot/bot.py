"""Один Telegram-бот на всю систему — внутри процесса FastAPI.

В боте живут три роутера, в таком порядке:
  1. группа «Отметки» (только групповые чаты) — карточки уроков и кнопки решений;
  2. преподаватель — только для сотрудника, чей Telegram привязан к панели, и для того,
     кто открыл ссылку-приглашение (/start inv_…): «Я на месте», кругляшок, мои часы;
  3. ученик — все остальные: вход в кабинет по QR-коду или коду с карточки.

Два режима:
  * long polling (по умолчанию) — работает даже без публичного адреса;
  * webhook (USE_WEBHOOK=1) — Telegram сам шлёт обновления на наш HTTPS-адрес.

Ради отметок преподавателей:
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
from aiogram.types import BotCommand, MenuButtonWebApp, Update, WebAppInfo

from .. import config
from ..checkin import handlers as staff_handlers
from . import handlers as student_handlers

log = logging.getLogger("stars.bot")

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]
MAX_BACKOFF_SEC = 60

_bot: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None
_task: Optional[asyncio.Task] = None
_status: Dict[str, Any] = {"mode": None, "running": False, "last_error": None}


def build_dispatcher() -> Dispatcher:
    """Новый Dispatcher со всеми роутерами (aiogram не даёт подключить роутер дважды —
    поэтому каждый роутер собирается заново)."""
    dispatcher = Dispatcher()
    dispatcher.include_router(staff_handlers.build_group_router())
    dispatcher.include_router(staff_handlers.build_router())
    dispatcher.include_router(student_handlers.build_router())
    return dispatcher


def get_bot() -> Optional[Bot]:
    """Для карточек в группе «Отметки» и напоминаний преподавателям."""
    return _bot


async def _configure(bot: Bot) -> None:
    """Имя бота, команды и кнопка «Кабинет» (у сотрудников она своя — см. checkin/handlers.py)."""
    me = await bot.get_me()
    if not config.BOT_USERNAME and me.username:
        # Подставляем имя автоматически, чтобы ссылки в QR-кодах и приглашениях собирались сами
        config.BOT_USERNAME = me.username
        log.info("Имя бота определено автоматически: @%s", me.username)

    await bot.set_my_commands([
        BotCommand(command="start", description="Войти в кабинет"),
        BotCommand(command="cabinet", description="Открыть кабинет"),
        BotCommand(command="help", description="Как пользоваться"),
    ])

    url = config.webapp_url()
    if url.startswith("https://"):
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Кабинет", web_app=WebAppInfo(url=url))
            )
        except Exception as exc:  # адрес может не подойти — не критично
            log.warning("Не удалось установить кнопку меню: %s", exc)


async def _run_polling() -> None:
    assert _bot and _dispatcher
    delay = 1
    while True:
        try:
            await _configure(_bot)
            _status.update(mode="polling", running=True, last_error=None)
            log.info("Бот запущен (long polling), кабинет: %s", config.webapp_url())
            await _dispatcher.start_polling(_bot, handle_signals=False, close_bot_session=False,
                                            handle_as_tasks=False, allowed_updates=ALLOWED_UPDATES)
            return                                       # остановили сами
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _status.update(running=False, last_error=type(exc).__name__)
            log.exception("Бот остановился, перезапуск через %s с", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SEC)


async def _setup_webhook() -> None:
    assert _bot
    url = config.webhook_url()
    if not url.startswith("https://"):
        _status.update(last_error="not_https")
        log.error("USE_WEBHOOK=1, но BASE_URL не https (%s). Telegram такой адрес не примет — "
                  "бот не запущен. Укажите настоящий домен или вернитесь к polling.", config.BASE_URL)
        return
    delay = 1
    while True:
        try:
            await _configure(_bot)
            await _bot.set_webhook(url=url, secret_token=config.WEBHOOK_SECRET,
                                   drop_pending_updates=False, allowed_updates=ALLOWED_UPDATES)
            _status.update(mode="webhook", running=True, last_error=None)
            log.info("Бот запущен (webhook), кабинет: %s", config.webapp_url())
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _status.update(running=False, last_error=type(exc).__name__)
            log.exception("Webhook не настроился, повтор через %s с", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF_SEC)


async def start_bot() -> None:
    global _bot, _dispatcher, _task
    if not config.BOT_TOKEN:
        log.warning("Токен бота не задан — бот не запущен (сайт работает как обычно)")
        return
    _bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _dispatcher = build_dispatcher()
    runner = _setup_webhook() if config.USE_WEBHOOK else _run_polling()
    _task = asyncio.create_task(runner, name="telegram-bot")


async def feed_webhook_update(payload: Dict[str, Any]) -> None:
    """Обновление из webhook. Ошибка обработчика остаётся в логе: ответ Telegram — всегда 200,
    иначе он будет присылать то же обновление снова и снова."""
    if _bot is None or _dispatcher is None:
        log.warning("Пришло обновление, но бот не запущен")
        return
    try:
        update = Update.model_validate(payload, context={"bot": _bot})
        await _dispatcher.feed_update(_bot, update)
    except Exception:
        log.exception("Обновление Telegram не обработано")


async def stop_bot() -> None:
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
    return {"bot_enabled": config.BOT_ENABLED, "bot_mode": _status["mode"],
            "bot_running": _status["running"], "bot_error": _status["last_error"],
            "bot_username": config.BOT_USERNAME or None}
