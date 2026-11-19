"""Запуск и остановка Telegram-бота внутри процесса FastAPI.

Два режима:
  * long polling (по умолчанию) — ничего настраивать не нужно, работает даже
    без публичного адреса. Подходит для разработки и небольшой школы;
  * webhook (USE_WEBHOOK=1) — Telegram сам шлёт обновления на наш HTTPS-адрес.
    Быстрее и экономнее, но требует домена с сертификатом.

В обоих случаях бот живёт в том же процессе, что и сайт: одна база, никакой
синхронизации между процессами.
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, Update, WebAppInfo

from .. import config
from .handlers import router

log = logging.getLogger("stars.bot")

_bot: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None
_task: Optional[asyncio.Task] = None


async def _configure(bot: Bot) -> None:
    """Имя бота, список команд и кнопка меню."""
    me = await bot.get_me()
    if not config.BOT_USERNAME and me.username:
        # Подставляем имя автоматически, чтобы ссылки в QR-кодах собирались сами
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
    try:
        await _configure(_bot)
        log.info("Бот запущен (long polling), кабинет: %s", config.webapp_url())
        await _dispatcher.start_polling(_bot, handle_signals=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Бот остановился из-за ошибки")


async def _setup_webhook() -> None:
    assert _bot
    url = config.webhook_url()
    if not url.startswith("https://"):
        log.error("USE_WEBHOOK=1, но BASE_URL не https (%s). Telegram такой адрес не примет — "
                  "бот не запущен. Укажите настоящий домен или вернитесь к polling.", config.BASE_URL)
        return
    try:
        await _configure(_bot)
        await _bot.set_webhook(
            url=url,
            secret_token=config.WEBHOOK_SECRET,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "my_chat_member"],
        )
        log.info("Бот запущен (webhook), кабинет: %s", config.webapp_url())
    except Exception:
        log.exception("Не удалось настроить webhook")


async def start_bot() -> None:
    global _bot, _dispatcher, _task

    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN не задан в .env — бот не запущен (сайт работает как обычно)")
        return

    _bot = Bot(token=config.BOT_TOKEN,
               default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _dispatcher = Dispatcher()
    _dispatcher.include_router(router)

    if config.USE_WEBHOOK:
        await _setup_webhook()
    else:
        _task = asyncio.create_task(_run_polling(), name="telegram-bot")


async def feed_webhook_update(payload: Dict[str, Any]) -> None:
    """Передаёт обновление из webhook в обычные обработчики бота."""
    if _bot is None or _dispatcher is None:
        log.warning("Пришло обновление, но бот не инициализирован")
        return
    try:
        update = Update.model_validate(payload, context={"bot": _bot})
    except Exception:
        log.exception("Не удалось разобрать обновление Telegram")
        return
    await _dispatcher.feed_update(_bot, update)


async def stop_bot() -> None:
    global _bot, _dispatcher, _task

    if _dispatcher is not None and not config.USE_WEBHOOK:
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
        if config.USE_WEBHOOK:
            try:
                await _bot.delete_webhook()
            except Exception:
                pass
        await _bot.session.close()
        _bot = None

    _dispatcher = None
