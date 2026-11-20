"""Ограничение попыток входа.

Логины учеников угадываются (ivanov.artem), поэтому без тормоза пароль
из словаря подбирается за вечер. Счётчик живёт в памяти процесса: приложение
работает одним процессом с одной базой, внешнего хранилища заводить не нужно.
При перезапуске счётчики обнуляются — для школы это приемлемо.
"""
import threading
import time
from typing import Dict, List, Tuple

_lock = threading.Lock()
_failures: Dict[Tuple[str, str], List[float]] = {}

MAX_FAILURES = 8        # столько неудач подряд разрешено
WINDOW_SECONDS = 900    # за какое время они считаются (15 минут)


def _prune(stamps: List[float], now: float) -> List[float]:
    return [t for t in stamps if now - t < WINDOW_SECONDS]


def retry_after(scope: str, key: str) -> int:
    """Сколько секунд ждать. 0 — можно пробовать."""
    now = time.time()
    with _lock:
        stamps = _prune(_failures.get((scope, key), []), now)
        if not stamps:
            _failures.pop((scope, key), None)
            return 0
        _failures[(scope, key)] = stamps
        if len(stamps) < MAX_FAILURES:
            return 0
        return max(1, int(WINDOW_SECONDS - (now - stamps[0])))


def register_failure(scope: str, key: str) -> None:
    now = time.time()
    with _lock:
        stamps = _prune(_failures.get((scope, key), []), now)
        stamps.append(now)
        _failures[(scope, key)] = stamps

        # Чистим чужие просроченные записи, чтобы словарь не рос бесконечно
        if len(_failures) > 2000:
            for k in [k for k, v in _failures.items() if not _prune(v, now)]:
                _failures.pop(k, None)


def reset(scope: str, key: str) -> None:
    """Успешный вход обнуляет счётчик."""
    with _lock:
        _failures.pop((scope, key), None)


def client_key(request) -> str:
    """IP клиента. За nginx настоящий адрес приходит в X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
