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

from . import passwords
from .config import DB_FILE

_lock = threading.RLock()

# 2 -> 3: появились школы. Классы стали объектами {id, school_id, name},
#         у ученика вместо названия класса хранится class_id.
# 3 -> 4: вход через сайт. У ученика появились login, password_hash
#         и password_shown (открытый пароль до первой самостоятельной смены).
# 4 -> 5: отзываемые сессии. У ученика появился session_key — он зашит в токен,
#         и смена ключа завершает все выданные ранее сессии. Номера учеников
#         больше не выдаются повторно (settings.last_student_id).
DB_VERSION = 5

# Поля ученика, которые не покидают сервер даже в панели учителя
SECRET_STUDENT_FIELDS = ("password_hash", "session_key")

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
    "schools": [{"id": 1, "name": "Наша школа"}],
    "classes": [
        {"id": 1, "school_id": 1, "name": "5-А"},
        {"id": 2, "school_id": 1, "name": "5-Б"},
        {"id": 3, "school_id": 1, "name": "6-А"},
    ],
    "prizes": DEFAULT_PRIZES,
    "students": [
        {"id": 101, "class_id": 1, "name": "Иванов Артём", "total_stars": 34,
         "history": [{"date": "2026-11-10", "delta": 4, "reason": "Активность на уроке"}]},
        {"id": 102, "class_id": 1, "name": "Петрова София", "total_stars": 52,
         "history": [{"date": "2026-11-10", "delta": 5, "reason": "Сборка схемы без ошибок"}]},
        {"id": 103, "class_id": 2, "name": "Каримов Тимур", "total_stars": 72,
         "history": [{"date": "2026-11-12", "delta": 5, "reason": "Победа в мини-олимпиаде"}]},
        {"id": 104, "class_id": 3, "name": "Ахмедова Малика", "total_stars": 18,
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


def new_session_key() -> str:
    return secrets.token_urlsafe(12)


def rotate_session(student: Dict[str, Any]) -> None:
    """Завершает все сессии ученика: старые токены перестают совпадать с ключом.

    Вызывается при сбросе пароля, новом коде карточки, отвязке Telegram и выходе.
    """
    student["session_key"] = new_session_key()


def public_state(db: Dict[str, Any]) -> Dict[str, Any]:
    """База для панели учителя — без хешей паролей и ключей сессий."""
    copy = json.loads(json.dumps(db))
    for student in copy.get("students", []):
        for field in SECRET_STUDENT_FIELDS:
            student.pop(field, None)
    return copy


def default_avatar(student_id: int) -> Dict[str, str]:
    """Пока ребёнок не выбрал аватар — выдаём стабильный пресет по его id."""
    return {"type": "preset", "value": AVATAR_PRESETS[student_id % len(AVATAR_PRESETS)]}


def next_id(items: Any) -> int:
    return max((int(i.get("id", 0)) for i in items), default=0) + 1


def _migrate_to_schools(db: Dict[str, Any]) -> bool:
    """Переход со схемы 2 на 3: плоский список классов -> школы и классы с id.

    Все существующие классы попадают в одну школу, название берётся из .env.
    Запускается один раз; на уже новой базе ничего не делает.
    """
    changed = False

    if "schools" not in db:
        db["schools"] = []
        changed = True

    classes = db.get("classes") or []
    old_style = any(isinstance(c, str) for c in classes)

    if not old_style and db["schools"]:
        return changed

    # Школа по умолчанию — в неё переедет всё, что было
    if not db["schools"]:
        from .config import SCHOOL_NAME
        db["schools"] = [{"id": 1, "name": SCHOOL_NAME or "Наша школа"}]
        changed = True

    school_id = db["schools"][0]["id"]

    if old_style:
        converted = []
        by_name: Dict[str, int] = {}
        for index, name in enumerate(classes, start=1):
            if not isinstance(name, str):
                converted.append(name)
                by_name[name.get("name", "")] = name.get("id", index)
                continue
            converted.append({"id": index, "school_id": school_id, "name": name})
            by_name[name] = index
        db["classes"] = converted
        changed = True

        # Переносим учеников: class_name -> class_id
        for student in db.get("students", []):
            if "class_id" in student:
                continue
            name = student.pop("class_name", "")
            if name not in by_name:
                by_name[name] = next_id(db["classes"])
                db["classes"].append({"id": by_name[name], "school_id": school_id, "name": name})
            student["class_id"] = by_name[name]
            changed = True

        # В истории уроков тоже было название класса
        for lesson in db.get("lessons", []):
            if "class_id" not in lesson and "class_name" in lesson:
                lesson["class_id"] = by_name.get(lesson["class_name"])
                changed = True

    # Классы без школы прибиваем к первой
    for cls in db["classes"]:
        if isinstance(cls, dict) and not cls.get("school_id"):
            cls["school_id"] = school_id
            changed = True

    return changed


def migrate(db: Dict[str, Any]) -> bool:
    """Дописывает недостающие поля. Идемпотентна.

    Возвращает True, если что-то изменилось и базу нужно сохранить.
    """
    changed = False

    if db.get("version") != DB_VERSION:
        db["version"] = DB_VERSION
        changed = True

    if _migrate_to_schools(db):
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
    logins = {s["login"] for s in db["students"] if s.get("login")}
    for student in db["students"]:
        if not student.get("token"):
            token = new_token(tokens)
            tokens.add(token)
            student["token"] = token
            changed = True

        # Вход через сайт: логин и пароль выдаются каждому, даже если класс
        # пользуется Telegram. Так ребёнок без аккаунта не остаётся за бортом.
        if not student.get("login"):
            login = passwords.suggest_login(student.get("name", ""), logins)
            logins.add(login)
            student["login"] = login
            changed = True
        if not student.get("password_hash"):
            passwords.set_credentials(student)
            changed = True
        if "password_shown" not in student:
            student["password_shown"] = None
            changed = True
        if not student.get("session_key"):
            student["session_key"] = new_session_key()
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

    # Счётчик номеров: удалённый ученик не освобождает свой номер
    highest = max((s["id"] for s in db["students"]), default=100)
    if db["settings"].get("last_student_id", 0) < highest:
        db["settings"]["last_student_id"] = highest
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


# ── Школы и классы ───────────────────────────────────────────────────────────

def find_school(db: Dict[str, Any], school_id: int) -> Optional[Dict[str, Any]]:
    return next((s for s in db["schools"] if s["id"] == school_id), None)


def find_class(db: Dict[str, Any], class_id: int) -> Optional[Dict[str, Any]]:
    return next((c for c in db["classes"] if c["id"] == class_id), None)


def school_classes(db: Dict[str, Any], school_id: int) -> list:
    return [c for c in db["classes"] if c["school_id"] == school_id]


def class_students(db: Dict[str, Any], class_id: int) -> list:
    return [s for s in db["students"] if s.get("class_id") == class_id]


def class_name(db: Dict[str, Any], class_id: Optional[int]) -> str:
    cls = find_class(db, class_id) if class_id else None
    return cls["name"] if cls else "—"


def school_name_of_class(db: Dict[str, Any], class_id: Optional[int]) -> str:
    cls = find_class(db, class_id) if class_id else None
    if not cls:
        return ""
    school = find_school(db, cls["school_id"])
    return school["name"] if school else ""


def find_class_by_name(db: Dict[str, Any], school_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Класс по названию внутри школы. Названия уникальны только в пределах школы:
    «5-А» может быть и в первой школе, и во второй."""
    wanted = (name or "").strip().lower()
    return next((c for c in db["classes"]
                 if c["school_id"] == school_id and c["name"].strip().lower() == wanted), None)


def ensure_class(db: Dict[str, Any], school_id: int, name: str) -> Dict[str, Any]:
    """Находит класс по названию или создаёт новый — нужно при импорте из Excel."""
    existing = find_class_by_name(db, school_id, name)
    if existing:
        return existing
    cls = {"id": next_id(db["classes"]), "school_id": school_id, "name": name.strip()}
    db["classes"].append(cls)
    return cls


def find_by_token(db: Dict[str, Any], token: str) -> Optional[Dict[str, Any]]:
    token = (token or "").strip().upper()
    if not token:
        return None
    return next((s for s in db["students"] if (s.get("token") or "").upper() == token), None)


def find_by_login(db: Dict[str, Any], login: str) -> Optional[Dict[str, Any]]:
    """Ученик по логину. Регистр не важен: дети набирают как придётся."""
    wanted = passwords.normalize_login(login)
    if not wanted:
        return None
    return next((s for s in db["students"]
                 if passwords.normalize_login(s.get("login")) == wanted), None)


def logins_taken(db: Dict[str, Any], except_id: Optional[int] = None) -> set:
    return {passwords.normalize_login(s.get("login")) for s in db["students"]
            if s.get("login") and s["id"] != except_id}


def find_by_tg_id(db: Dict[str, Any], tg_id: int) -> Optional[Dict[str, Any]]:
    return next((s for s in db["students"] if s.get("tg_id") == tg_id), None)


def next_student_id(db: Dict[str, Any]) -> int:
    """Номер для нового ученика. Никогда не повторяется, даже после удаления.

    Раньше брали «максимум + 1»: удалили последнего — новый получал его номер,
    и всё, что было привязано к номеру, доставалось чужому ребёнку.
    """
    settings = db.setdefault("settings", {})
    highest = max((s["id"] for s in db["students"]), default=100)
    new_id = max(highest, settings.get("last_student_id", 0)) + 1
    settings["last_student_id"] = new_id
    return new_id


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
