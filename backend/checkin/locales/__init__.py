"""Тексты бота преподавателей: русский и узбекский (латиница).

t(lang, key, **kw) — текст с подстановками. Неизвестный язык — русский;
ключа нет в узбекском — берётся русский, чтобы бот не молчал. Одинаковость
ключей и подстановок в двух языках проверяет тест.
"""
from typing import Any, Dict

from . import ru, uz

LANGS = {"ru": ru.TEXTS, "uz": uz.TEXTS}
DEFAULT = "ru"
MONTHS = {"ru": ru.MONTHS, "uz": uz.MONTHS}


def lang_of(value: Any) -> str:
    return value if value in LANGS else DEFAULT


def t(lang: Any, key: str, **kw: Any) -> str:
    texts: Dict[str, str] = LANGS[lang_of(lang)]
    template = texts.get(key) or LANGS[DEFAULT][key]
    return template.format(**kw) if kw else template


def minutes(lang: Any, n: int) -> str:
    """«2 минуты» / «5 минут» / «2 daqiqa»."""
    if lang_of(lang) == "uz":
        return f"{n} daqiqa"
    tail = n % 100
    if 11 <= tail <= 14:
        word = "минут"
    elif n % 10 == 1:
        word = "минуту"
    elif 2 <= n % 10 <= 4:
        word = "минуты"
    else:
        word = "минут"
    return f"{n} {word}"


def month_title(lang: Any, year: int, month: int) -> str:
    name = MONTHS[lang_of(lang)][month - 1]
    return f"{year}-yil {name}" if lang_of(lang) == "uz" else f"{name} {year}"


def day_title(lang: Any, day) -> str:
    """«24 ноября» / «24-noyabr»."""
    if lang_of(lang) == "uz":
        return f"{day.day}-{uz.MONTHS[day.month - 1]}"
    return f"{day.day} {ru.MONTHS_GEN[day.month - 1]}"
