"""Общие настройки системы — меняет только суперадмин."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from .. import db, schemas
from ..security import require_superadmin

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_superadmin)])


@router.put("/telegram-login")
def set_telegram_login(item: schemas.TelegramLogin):
    """Вход учеников через Telegram-бота. Выключен — QR на карточках ведёт на страницу
    входа сайта, код для бота не печатается (бот один на все школы, поэтому и настройка общая)."""
    def _apply(database: Dict[str, Any]):
        settings = database.setdefault("settings", {})
        if db.telegram_login(database) == item.enabled:
            return db.NO_SAVE
        settings["telegram_login"] = item.enabled
        return None

    db.transaction(_apply)
    return {"ok": True, "telegram_login": item.enabled}
