"""Версии CSS и JS в адресах: долгий кеш без риска показать старую версию.

Раньше CSS и JS отдавались с «no-cache»: при каждом открытии страницы браузер
перепроверял каждый файл у сервера — по запросу на файл, на мобильном интернете
это заметно. Теперь адрес несёт отметку версии (theme.css?v=3f9a1c2b0e), такой
адрес кешируется навсегда, а после обновления файла меняется сама отметка —
и браузер сразу берёт новый файл.

Отметка считается из содержимого, а не из времени изменения: пересборка
Docker-образа без изменений в файле не сбрасывает кеш у детей.
"""
import hashlib
import re
from typing import Dict, Tuple

from . import config

_cache: Dict[str, Tuple[int, int, str]] = {}

# Ссылки на наши CSS и JS внутри HTML: "/static/css/app.css" или "{{BASE}}/static/js/app.js"
ASSET_LINK = re.compile(r'(/static/((?:css|js)/[\w.-]+\.(?:css|js)))(")')


def version(relative: str) -> str:
    """Короткая отметка версии файла из static/, например 'css/theme.css'."""
    path = config.STATIC_DIR / relative
    stat = path.stat()
    cached = _cache.get(relative)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    digest = hashlib.md5(path.read_bytes()).hexdigest()[:10]
    _cache[relative] = (stat.st_mtime_ns, stat.st_size, digest)
    return digest


def versioned(relative: str) -> str:
    """Адрес файла с отметкой версии: /static/css/theme.css?v=…"""
    return f"/static/{relative}?v={version(relative)}"


def add_versions(html: str) -> str:
    """Дописывает отметку версии ко всем ссылкам на наши CSS и JS в странице."""
    return ASSET_LINK.sub(lambda m: f"{m.group(1)}?v={version(m.group(2))}{m.group(3)}", html)


def files_stamp() -> int:
    """Сумма времён изменения всех CSS и JS — чтобы кеш готовых страниц
    обновлялся, когда меняется любой из подключённых файлов."""
    total = 0
    for folder in ("css", "js"):
        for path in (config.STATIC_DIR / folder).iterdir():
            total += path.stat().st_mtime_ns
    return total
