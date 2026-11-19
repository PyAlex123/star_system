"""Хранилище: один JSON-файл.

Писать в него теперь могут одновременно веб-панель и Telegram-бот, поэтому
все операции идут под блокировкой, а запись — атомарная (во временный файл и
переименование), чтобы при обрыве не осталось битого JSON.
"""
import json
import os
import secrets
import string
import tempfile
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from .config import DB_FILE

_lock = threading.RLock()

DB_VERSION = 2

# Алфавит кодов без символов, которые дети путают при ручном вводе (O/0, I/1)
_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Порядок = порядок в сетке выбора у ребёнка: сначала звери, потом сказочные,
# потом техника и космос. Файлы лежат в static/img/avatars/<ключ>.svg
# и рисуются скриптом scripts/make_avatars.py.
AVATAR_PRESETS = [
    "fox", "cat", "panda", "bear", "tiger", "wolf", "koala", "owl",
    "penguin", "frog", "shark", "dino", "dragon", "unicorn", "ghost", "slime",
    "strawberry", "bolt", "robot", "cyborg", "astronaut", "alien", "rocket", "planet",
]

DEFAULT_PRIZES = [
    {"id": 1, "name": "Большой набор «Знаток»", "cost": 70, "category": "electronics",
     "desc": "999 схем для сборки без пайки",
     "image": "/static/img/prizes/znatok-999.webp", "active": True},
    {"id": 2, "name": "Игровая клавиатура с RGB", "cost": 70, "category": "gadgets",
     "desc": "Механический отклик и подсветка",
     "image": "/static/img/prizes/keyboard-rgb.webp", "active": True},
    {"id": 3, "name": "Стандартный набор «Знаток»", "cost": 50, "category": "electronics",
     "desc": "320 увлекательных электронных схем",
     "image": "/static/img/prizes/znatok-320.webp", "active": True},
    {"id": 4, "name": "Набор паяльника", "cost": 50, "category": "electronics",
     "desc": "Паяльник с регулировкой, припой и подставка",
     "image": "/static/img/prizes/soldering-kit.webp", "active": True},
    {"id": 5, "name": "Игровая мышь (Logitech / Bloody)", "cost": 50, "category": "gadgets",
     "desc": "Точный сенсор и программируемые кнопки",
     "image": "/static/img/prizes/gaming-mouse.webp", "active": True},
    {"id": 6, "name": "Набор датчиков для Arduino", "cost": 30, "category": "electronics",
     "desc": "Модули света, звука, расстояния и температуры",
     "image": "/static/img/prizes/arduino-sensors.webp", "active": True},
    {"id": 7, "name": "Игровые наушники / Коврик XXL", "cost": 30, "category": "gadgets",
     "desc": "Полноразмерная гарнитура или большой коврик",
     "image": "/static/img/prizes/headset-mousepad.webp", "active": True},
    {"id": 8, "name": "Набор светодиодов / Брелок", "cost": 12, "category": "small",
     "desc": "Быстрый приз за активность на уроках",
     "image": "/static/img/prizes/led-kit.webp", "active": True},
]

DEFAULT_DB: Dict[str, Any] = {
    "version": DB_VERSION,
    "classes": ["5-А", "5-Б", "6-А"],
    "prizes": DEFAULT_PRIZES,
    "students": [
        {"id": 101, "class_name": "5-А", "name": "Иванов Артём", "total_stars": 34,
         "history": [{"date": "2026-11-10", "delta": 4, "reason": "Активность на уроке"}]},
        {"id": 102, "class_name": "5-А", "name": "Петрова София", "total_stars": 52,
         "history": [{"date": "2026-11-10", "delta": 5, "reason": "Сборка схемы без ошибок"}]},
        {"id": 103, "class_name": "5-Б", "name": "Каримов Тимур", "total_stars": 72,
         "history": [{"date": "2026-11-12", "delta": 5, "reason": "Победа в мини-олимпиаде"}]},
        {"id": 104, "class_name": "6-А", "name": "Ахмедова Малика", "total_stars": 18,
         "history": [{"date": "2026-11-14", "delta": 3, "reason": "Домашнее задание и ответы"}]},
    ],
    "lessons": [],
    "settings": {},
}


class _NoSave:
    """Маркер «ничего не менялось, записывать файл не нужно»."""
    __slots__ = ()


NO_SAVE = _NoSave()


def new_token(existing: Optional[set] = None) -> str:
    """Короткий код для QR-карточки: 8 символов, уникальный в пределах базы."""
    existing = existing or set()
    while True:
        token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(8))
        if token not in existing:
            return token


def default_avatar(student_id: int) -> Dict[str, str]:
    """Пока ребёнок не выбрал аватар — выдаём стабильный пресет по его id."""
    return {"type": "preset", "value": AVATAR_PRESETS[student_id % len(AVATAR_PRESETS)]}


def migrate(db: Dict[str, Any]) -> bool:
    """Дописывает недостающие поля. Идемпотентна.

    Возвращает True, если что-то изменилось и базу нужно сохранить.
    """
    changed = False

    if db.get("version") != DB_VERSION:
        db["version"] = DB_VERSION
        changed = True

    for key, default in (("classes", []), ("students", []), ("lessons", []), ("settings", {})):
        if key not in db:
            db[key] = default
            changed = True

    if not db.get("prizes"):
        db["prizes"] = json.loads(json.dumps(DEFAULT_PRIZES))
        changed = True

    for prize in db["prizes"]:
        if "image" not in prize:
            match = next((p for p in DEFAULT_PRIZES if p["id"] == prize.get("id")), None)
            prize["image"] = match["image"] if match else ""
            changed = True
        if "active" not in prize:
            prize["active"] = True
            changed = True
        if "category" not in prize:
            prize["category"] = "small"
            changed = True

    tokens = {s["token"] for s in db["students"] if s.get("token")}
    for student in db["students"]:
        if not student.get("token"):
            token = new_token(tokens)
            tokens.add(token)
            student["token"] = token
            changed = True
        for field, default in (("tg_id", None), ("tg_username", None), ("bound_at", None)):
            if field not in student:
                student[field] = default
                changed = True
        if not student.get("avatar"):
            student["avatar"] = default_avatar(student["id"])
            changed = True
        if "history" not in student:
            student["history"] = []
            changed = True

    return changed


def _write(data: Dict[str, Any]) -> None:
    """Атомарная запись: во временный файл рядом, затем os.replace."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(DB_FILE.parent), prefix=".stars_db_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, DB_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_db() -> Dict[str, Any]:
    with _lock:
        if not DB_FILE.exists():
            data = json.loads(json.dumps(DEFAULT_DB))
            migrate(data)
            _write(data)
            return data

        with open(DB_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        if migrate(data):
            _write(data)
        return data


def save_db(data: Dict[str, Any]) -> None:
    with _lock:
        _write(data)


def transaction(func: Callable[[Dict[str, Any]], Any]) -> Any:
    """Прочитать → изменить → записать под одной блокировкой.

    Нужно, чтобы бот и веб-панель не затирали изменения друг друга.
    Если func вернула NO_SAVE, файл не перезаписывается.
    """
    with _lock:
        db = load_db()
        result = func(db)
        if result is NO_SAVE:
            return result
        _write(db)
        return result


def find_student(db: Dict[str, Any], student_id: int) -> Optional[Dict[str, Any]]:
    return next((s for s in db["students"] if s["id"] == student_id), None)


def find_by_token(db: Dict[str, Any], token: str) -> Optional[Dict[str, Any]]:
    token = (token or "").strip().upper()
    if not token:
        return None
    return next((s for s in db["students"] if (s.get("token") or "").upper() == token), None)


def find_by_tg_id(db: Dict[str, Any], tg_id: int) -> Optional[Dict[str, Any]]:
    return next((s for s in db["students"] if s.get("tg_id") == tg_id), None)


def next_student_id(db: Dict[str, Any]) -> int:
    return max((s["id"] for s in db["students"]), default=100) + 1


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
