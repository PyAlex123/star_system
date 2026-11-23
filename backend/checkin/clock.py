"""Время отметок.

В базе всё хранится в UTC (строки ISO с «+00:00»), считается и показывается —
по часам школ (TZ_NAME, по умолчанию Asia/Tashkent). Контейнер обычно живёт в UTC,
поэтому наивное datetime.now() здесь не используем нигде.

now_utc() — единственная точка, откуда модуль узнаёт «сейчас»: тесты подменяют её.
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

from .. import config

UTC = timezone.utc
TZ = ZoneInfo(config.TZ_NAME)

_frozen: Optional[datetime] = None


def now_utc() -> datetime:
    return _frozen if _frozen is not None else datetime.now(UTC)


def freeze(moment: Optional[datetime]) -> None:
    """Только для тестов: остановить часы на моменте (aware) или вернуть настоящие (None)."""
    global _frozen
    _frozen = moment.astimezone(UTC) if moment is not None else None


def local(moment: datetime) -> datetime:
    return moment.astimezone(TZ)


def today(moment: Optional[datetime] = None) -> date:
    """Сегодняшняя дата по часам школ."""
    return local(moment or now_utc()).date()


def at(day: date, hhmm: str) -> datetime:
    """Дата + «ЧЧ:ММ» по часам школ → момент в UTC."""
    return datetime.combine(day, time.fromisoformat(hhmm), TZ).astimezone(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse(value: Union[str, datetime, None]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:          # на всякий случай: наивное время считаем UTC
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def hhmm(moment: datetime) -> str:
    """«09:05» по часам школ — для сообщений и панели."""
    return local(moment).strftime("%H:%M")


def minutes_between(earlier: datetime, later: datetime) -> int:
    """Целые минуты от earlier до later, с округлением вниз; отрицательные — если раньше."""
    seconds = (later - earlier).total_seconds()
    return int(seconds // 60)


def month_bounds(year: int, month: int) -> tuple:
    """Первый и последний день месяца."""
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)
