"""Логины и пароли учеников для входа через сайт.

Своя реализация вместо bcrypt/passlib: лишняя зависимость в образе не нужна,
а pbkdf2 из стандартной библиотеки для школьной системы более чем достаточен.
"""
import hashlib
import hmac
import os
import re
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any, Dict, Optional

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 120_000

# ── Пароли ───────────────────────────────────────────────────────────────────
# Слово плюс четыре цифры: ребёнок наберёт без ошибок, а подобрать
# сложнее, чем «12345». Слова записаны латиницей — на клавиатуре телефона
# не нужно переключать раскладку.
_WORDS = [
    "zvezda", "raketa", "kometa", "planeta", "orbita", "luna", "sputnik", "galaktika",
    "tigr", "panda", "lisa", "volk", "medved", "orel", "delfin", "drakon",
    "kompas", "fonarik", "magnit", "robot", "antenna", "motor", "lampa", "provod",
    "yakor", "parus", "mayak", "veter", "groza", "raduga", "iskra", "plamya",
    "atlas", "kvant", "atom", "piksel", "kabel", "schema", "datchik", "zaryad",
]


def new_password() -> str:
    """Пароль, который не стыдно напечатать на карточке и продиктовать вслух."""
    word = secrets.choice(_WORDS)
    return f"{word}{secrets.randbelow(9000) + 1000}"


MIN_LENGTH = 5
MAX_LENGTH = 64


def validate_new(password: str) -> str:
    """Проверяет новый пароль и возвращает его без пробелов по краям.

    Одна функция на всех: и учитель, и ребёнок получают одинаковые правила.
    """
    value = (password or "").strip()
    if len(value) < MIN_LENGTH:
        raise ValueError(f"Пароль слишком короткий — нужно хотя бы {MIN_LENGTH} символов")
    if len(value) > MAX_LENGTH:
        raise ValueError(f"Пароль слишком длинный — не больше {MAX_LENGTH} символов")
    if any(ch.isspace() for ch in value):
        raise ValueError("В пароле не должно быть пробелов")
    return value


def hash_password(password: str) -> str:
    """Хранимое представление: алгоритм$итерации$соль$хеш."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    parts = (ALGORITHM, str(ITERATIONS),
             urlsafe_b64encode(salt).decode(), urlsafe_b64encode(digest).decode())
    return "$".join(parts)


def verify_password(password: str, stored: str) -> bool:
    """Сверяет пароль с хешем. Любой непонятный формат считаем несовпадением."""
    if not stored or not password:
        return False
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            urlsafe_b64decode(salt_b64), int(iterations),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, urlsafe_b64decode(digest_b64))


_dummy_hashes = {}


def dummy_hash() -> str:
    """Хеш, с которым сверяется пароль при несуществующем логине.

    Ни с чем не совпадёт, но считается с тем же числом итераций, что и настоящие,
    поэтому ответ «нет такого логина» приходит за то же время, что «неверный пароль».
    """
    if ITERATIONS not in _dummy_hashes:
        _dummy_hashes[ITERATIONS] = hash_password(secrets.token_urlsafe(16))
    return _dummy_hashes[ITERATIONS]


# ── Логины ───────────────────────────────────────────────────────────────────

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # Узбекская и казахская латиница-кириллица, которая встречается в именах
    "ғ": "g", "қ": "q", "ҳ": "h", "ў": "o", "ә": "a", "ө": "o", "ү": "u",
    "ұ": "u", "ң": "n", "і": "i", "һ": "h", "ҷ": "j",
}

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,30}$")


def translit(text: str) -> str:
    out = []
    for char in (text or "").lower():
        if char in _TRANSLIT:
            out.append(_TRANSLIT[char])
        elif char.isascii() and (char.isalnum()):
            out.append(char)
        elif char in " -":
            out.append("-")
    return "".join(out).strip("-")


def suggest_login(full_name: str, taken: set) -> str:
    """«Иванов Артём» → ivanov.artem. При совпадении добавляем число.

    Логин должен легко диктоваться вслух: учитель называет его ребёнку,
    а не пересылает ссылкой.
    """
    parts = [translit(p) for p in (full_name or "").split() if translit(p)]
    base = ".".join(parts[:2]) if parts else "uchenik"
    base = re.sub(r"[^a-z0-9.]+", "", base).strip(".") or "uchenik"
    if len(base) < 3:
        base = f"{base}uchenik"[:12]

    candidate = base
    counter = 1
    while candidate in taken:
        counter += 1
        candidate = f"{base}{counter}"
    return candidate


def normalize_login(value: str) -> str:
    return (value or "").strip().lower()


def set_credentials(student: Dict[str, Any], password: Optional[str] = None) -> str:
    """Выдаёт ученику новый пароль и запоминает его хеш.

    Открытый пароль остаётся в password_shown, пока ребёнок не сменит его сам, —
    иначе учитель не сможет перепечатать карточку и подсказать забывшему.
    После самостоятельной смены поле очищается, и пароль знает только ребёнок.
    """
    value = (password or "").strip() or new_password()
    student["password_hash"] = hash_password(value)
    student["password_shown"] = value
    return value
