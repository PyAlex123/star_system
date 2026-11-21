"""Ограничение попыток входа.

Логины учеников угадываются (ivanov.artem), поэтому без тормоза пароль
из словаря подбирается за вечер. Счётчик живёт в памяти процесса: приложение
работает одним процессом с одной базой, внешнего хранилища заводить не нужно.
При перезапуске счётчики обнуляются — для школы это приемлемо.

Лимиты разные по смыслу:
  * на логин — строгий: защищает конкретного ребёнка от подбора пароля;
  * на адрес — мягкий: вся школа выходит в интернет через один IP, и десяток
    опечаток на уроке не должен блокировать вход целому классу. Этот лимит
    ловит перебор «веером» по многим логинам сразу;
  * на вход в панель — строгий по логину и мягче по адресу (учителя одной
    школы тоже выходят в интернет через один IP).
"""
import threading
import time
from typing import Dict, List, Tuple

_lock = threading.Lock()
_failures: Dict[Tuple[str, str], List[float]] = {}

MAX_FAILURES = 8        # лимит по умолчанию
WINDOW_SECONDS = 900    # за какое время считаются неудачи (15 минут)

LIMITS: Dict[str, int] = {
    "student-login": 8,
    "student-ip": 60,
    "teacher-login": 8,
    "teacher-ip": 30,
}


def _limit(scope: str) -> int:
    return LIMITS.get(scope, MAX_FAILURES)


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
        limit = _limit(scope)
        if len(stamps) < limit:
            return 0
        # Ждать, пока не «выгорит» столько старых неудач, чтобы опуститься ниже лимита
        oldest_that_matters = stamps[len(stamps) - limit]
        return max(1, int(WINDOW_SECONDS - (now - oldest_that_matters)))


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
    """IP клиента.

    Берём X-Real-IP: его выставляет наш nginx (proxy_set_header X-Real-IP
    $remote_addr), перезаписывая всё, что прислал клиент. X-Forwarded-For
    для этого не годится — его первое значение клиент пишет сам и может
    представляться новым адресом в каждом запросе.
    Без nginx (локальный запуск, тесты) — адрес соединения.
    """
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"
