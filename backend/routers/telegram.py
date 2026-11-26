"""Приём обновлений от Telegram в режиме webhook.

Включается переменной USE_WEBHOOK=1. В режиме long polling маршрут отвечает 404 —
снаружи не видно даже того, что такая возможность есть.
"""
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from .. import config
from ..bot import bot as bot_module

log = logging.getLogger("stars.bot")
router = APIRouter(tags=["telegram"], include_in_schema=False)


@router.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if not config.USE_WEBHOOK or not config.WEBHOOK_SECRET:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")

    # Секрет в пути: случайный запрос к /telegram/... не должен ничего сообщать о системе
    if not hmac.compare_digest(secret, config.WEBHOOK_SECRET):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")

    # Телеграм дублирует секрет в заголовке — так подделать запрос сложнее,
    # даже если адрес webhook когда-то попал в логи прокси
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(header, config.WEBHOOK_SECRET):
        log.warning("Запрос на webhook без правильного заголовка — отклонён")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ожидался JSON")

    await bot_module.feed_webhook_update(payload)
    # Telegram важен только код ответа: тело он не читает
    return Response(status_code=status.HTTP_200_OK)

