"""Фоновая задача отметок внутри процесса сайта: раз в минуту service.tick().

Тик синхронный (JSON и SQLite), поэтому уходит в поток — цикл событий, на котором
живут боты и сайт, не ждёт диска. Ошибка тика пишется в лог, цикл живёт дальше:
планировщик, который тихо умер, хуже упавшего.
"""
import asyncio
import logging
from typing import Any, Dict, Optional

from . import clock, notify, service

log = logging.getLogger("stars.checkin")

INTERVAL_SEC = 60

_task: Optional[asyncio.Task] = None
_last_tick: Optional[str] = None
_last_error: Optional[str] = None


def run_once() -> Dict[str, Any]:
    """Один тик с учётом в состоянии — то же, что делает цикл."""
    global _last_tick, _last_error
    try:
        result = service.tick()
    except Exception as exc:
        _last_error = f"{clock.iso(clock.now_utc())}: {type(exc).__name__}"
        raise
    _last_tick = clock.iso(clock.now_utc())
    _last_error = None
    return result


async def _loop() -> None:
    from . import bot as checkin_bot              # бот подключается позже планировщика
    while True:
        try:
            await asyncio.to_thread(run_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Отметки: тик планировщика не удался")
        try:
            await notify.drain(checkin_bot.get_bot())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Отметки: отправка в Telegram не удалась")
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="checkin-scheduler")
        log.info("Отметки: планировщик запущен (раз в %s с)", INTERVAL_SEC)


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):
        pass
    _task = None


def status() -> Dict[str, Any]:
    return {"scheduler": bool(_task and not _task.done()), "last_tick": _last_tick, "last_error": _last_error}
