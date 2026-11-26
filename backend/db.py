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
import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from . import config, passwords
from .config import DB_FILE

log = logging.getLogger("stars")

_lock = threading.RLock()

# 2 -> 3: появились школы. Классы стали объектами {id, school_id, name},
#         у ученика вместо названия класса хранится class_id.
# 3 -> 4: вход через сайт. У ученика появились login, password_hash
#         и password_shown (открытый пароль до первой самостоятельной смены).
# 4 -> 5: отзываемые сессии. У ученика появился session_key — он зашит в токен,
#         и смена ключа завершает все выданные ранее сессии. Номера учеников
#         больше не выдаются повторно (settings.last_student_id).
# 5 -> 6: пользователи панели. Вместо одного общего пароля — список users:
#         суперадмин и учителя со своими логинами, паролями и школами.
# 6 -> 7: темы оформления. У школы появилась theme; у урока — id и undone
#         (отменённые начисления); категории призов small/medium/large;
#         у ученика rating_hidden («скрыт из рейтинга»).
# 7 -> 8: расписание (schedule), роль «администрация школы» (school_admin), открытый
#         пароль сотрудника до первой смены (password_shown), автор урока (lesson.user_id),
#         последний заход ученика в кабинет (last_seen).
# 8 -> 9: расписание таблицей. У школы время уроков (bells: [{start, end}], номер урока —
#         место в списке) и учебные дни (days: 5 — Пн–Пт, 6 — с субботой); у занятия —
#         номер урока в таблице (lesson) или None, если оно идёт в другое время.
DB_VERSION = 9

# Категории призов как в макетах: небольшой, средний, большой
PRIZE_CATEGORIES = ("small", "medium", "large")


def category_by_cost(cost: int) -> str:
    """Категория для старых призов («electronics», «gadgets») — по цене."""
    if cost >= 60:
        return "large"
    if cost >= 40:
        return "medium"
    return "small"

# Поля ученика, которые не покидают сервер даже в панели учителя
SECRET_STUDENT_FIELDS = ("password_hash", "session_key")
# То же для пользователей панели
SECRET_USER_FIELDS = ("password_hash", "session_key")

ROLE_SUPERADMIN = "superadmin"
ROLE_TEACHER = "teacher"
ROLE_SCHOOL_ADMIN = "school_admin"      # директор, завуч: статистика своих школ, оформление, свои призы,
                                         # печать карточек учеников; журнала и звёзд нет
STAFF_ROLES = (ROLE_TEACHER, ROLE_SCHOOL_ADMIN)

# Чего администрация школы не видит у учеников: всё, чем можно войти в кабинет ребёнка
LOGIN_FIELDS = ("token", "login", "password_shown", "tg_id", "tg_username", "bound_at", "last_login")

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
    {"id": 1, "name": "Большой набор «Знаток»", "cost": 70, "category": "large",
     "desc": "999 схем для сборки без пайки",
     "image": "/static/img/prizes/znatok-999.webp", "active": True},
    {"id": 2, "name": "Игровая клавиатура с RGB", "cost": 70, "category": "large",
     "desc": "Механический отклик и подсветка",
     "image": "/static/img/prizes/keyboard-rgb.webp", "active": True},
    {"id": 3, "name": "Стандартный набор «Знаток»", "cost": 50, "category": "medium",
     "desc": "320 увлекательных электронных схем",
     "image": "/static/img/prizes/znatok-320.webp", "active": True},
    {"id": 4, "name": "Набор паяльника", "cost": 50, "category": "medium",
     "desc": "Паяльник с регулировкой, припой и подставка",
     "image": "/static/img/prizes/soldering-kit.webp", "active": True},
    {"id": 5, "name": "Игровая мышь (Logitech / Bloody)", "cost": 50, "category": "medium",
     "desc": "Точный сенсор и программируемые кнопки",
     "image": "/static/img/prizes/gaming-mouse.webp", "active": True},
    {"id": 6, "name": "Набор датчиков для Arduino", "cost": 30, "category": "small",
     "desc": "Модули света, звука, расстояния и температуры",
     "image": "/static/img/prizes/arduino-sensors.webp", "active": True},
    {"id": 7, "name": "Игровые наушники / Коврик XXL", "cost": 30, "category": "small",
     "desc": "Полноразмерная гарнитура или большой коврик",
     "image": "/static/img/prizes/headset-mousepad.webp", "active": True},
    {"id": 8, "name": "Набор светодиодов / Брелок", "cost": 12, "category": "small",
     "desc": "Быстрый приз за активность на уроках",
     "image": "/static/img/prizes/led-kit.webp", "active": True},
]

DEFAULT_DB: Dict[str, Any] = {
    "version": DB_VERSION,
    "schools": [{"id": 1, "name": "Наша школа", "theme": "notebook"}],
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
    "schedule": [],
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


def clear_telegram(student: Dict[str, Any]) -> None:
    """Открепляет Telegram-аккаунт от ученика."""
    student["tg_id"] = None
    student["tg_username"] = None
    student["bound_at"] = None


def public_student(student: Dict[str, Any]) -> Dict[str, Any]:
    """Запись ученика для ответа API — без хеша пароля и ключа сессии."""
    return {k: v for k, v in student.items() if k not in SECRET_STUDENT_FIELDS}


def public_user(user: Dict[str, Any], with_password: bool = False) -> Dict[str, Any]:
    """Пользователь панели для ответа API — без хеша пароля и ключа сессии.
    Открытый пароль (до первой смены) — только в списке сотрудников у администратора."""
    hidden = SECRET_USER_FIELDS + (() if with_password else ("password_shown",))
    return {k: v for k, v in user.items() if k not in hidden}


def telegram_login(database: Dict[str, Any]) -> bool:
    """Вход учеников через Telegram-бота включён (по умолчанию да). Пока бот недоступен,
    администратор выключает его: карточки печатаются с QR на страницу входа сайта."""
    return database.get("settings", {}).get("telegram_login", True) is not False


def is_superadmin(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and user.get("role") == ROLE_SUPERADMIN


def is_school_admin(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and user.get("role") == ROLE_SCHOOL_ADMIN


def can_edit(user: Optional[Dict[str, Any]]) -> bool:
    """Менять данные может администратор и учитель; администрация школы — только смотрит."""
    return bool(user) and user.get("role") in (ROLE_SUPERADMIN, ROLE_TEACHER)


def prize_for_school(prize: Dict[str, Any], school_ids) -> bool:
    """Приз виден школе: общий (school_id нет — добавил администратор системы)
    или добавлен администрацией этой школы."""
    owner = prize.get("school_id")
    return owner is None or owner in school_ids


def allowed_school_ids(db: Dict[str, Any], user: Optional[Dict[str, Any]]) -> set:
    """Школы, которые видит пользователь. Суперадмину — все."""
    if user is None or is_superadmin(user):
        return {s["id"] for s in db.get("schools", [])}
    return set(user.get("school_ids") or [])


def public_state(db: Dict[str, Any], user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """База для панели — без хешей паролей и ключей сессий и без чужих школ.

    Учитель получает только свои школы, их классы, учеников, уроки и расписание.
    Администрация школы — то же, но без данных для входа учеников.
    Суперадмину вдобавок приходит список пользователей панели с паролями до первой смены.
    """
    copy = json.loads(json.dumps(db))
    schools = allowed_school_ids(copy, user)
    copy["schools"] = [s for s in copy.get("schools", []) if s["id"] in schools]
    copy["classes"] = [c for c in copy.get("classes", []) if c["school_id"] in schools]
    class_ids = {c["id"] for c in copy["classes"]}
    copy["students"] = [public_student(s) for s in copy.get("students", [])
                        if s.get("class_id") in class_ids]
    if is_school_admin(user):
        copy["students"] = [{k: v for k, v in s.items() if k not in LOGIN_FIELDS} for s in copy["students"]]
    copy["lessons"] = [lesson for lesson in copy.get("lessons", [])
                       if lesson.get("class_id") in class_ids]
    copy["schedule"] = [e for e in copy.get("schedule", []) if e.get("class_id") in class_ids]
    copy["prizes"] = [p for p in copy.get("prizes", []) if prize_for_school(p, schools)]
    users = copy.pop("users", [])
    copy["telegram_login"] = telegram_login(db)
    copy.pop("settings", None)
    if user is not None:
        copy["me"] = public_user(user)
        if is_superadmin(user):
            copy["users"] = [public_user(u, with_password=True) for u in users]
        else:
            # Коллеги по школам — только имена: для расписания и статистики по учителям
            mine = allowed_school_ids(db, user)
            copy["staff"] = [{"id": u["id"], "name": u.get("name", ""), "role": u.get("role"),
                              "avatar": u.get("avatar")}
                             for u in users if is_superadmin(u) or mine & set(u.get("school_ids") or [])]
    return copy


# ── Пользователи панели ──────────────────────────────────────────────────────

def find_user(db: Dict[str, Any], user_id: int) -> Optional[Dict[str, Any]]:
    return next((u for u in db.get("users", []) if u["id"] == user_id), None)


def find_user_by_login(db: Dict[str, Any], login: str) -> Optional[Dict[str, Any]]:
    wanted = passwords.normalize_login(login)
    if not wanted:
        return None
    return next((u for u in db.get("users", []) if u.get("login") == wanted), None)


def make_user(db: Dict[str, Any], login: str, name: str, role: str,
              password: str, school_ids: Optional[list] = None, show_password: bool = True) -> Dict[str, Any]:
    """Новый пользователь панели. Пароль сотрудника, созданного в панели, виден
    администратору до первой смены; пароли из .env в базу открытым текстом не пишем."""
    user = {
        "id": next_id(db.setdefault("users", [])),
        "login": passwords.normalize_login(login),
        "name": name,
        "role": role,
        "school_ids": sorted(set(school_ids or [])),
        "password_hash": passwords.hash_password(password),
        "password_shown": password if show_password and role != ROLE_SUPERADMIN else None,
        "avatar": None,                  # {"type": "photo", "value": адрес} или None — инициалы
        "session_key": new_session_key(),
        "created_at": now_iso(),
    }
    db["users"].append(user)
    return user


def _bootstrap_users(db: Dict[str, Any]) -> bool:
    """Первый запуск после обновления: суперадмин и учитель из .env.

    Учитель сразу получает все существующие школы — он работал с ними и раньше.
    """
    if db.get("users"):
        return False
    db["users"] = []
    make_user(db, config.ADMIN_LOGIN, "Администратор", ROLE_SUPERADMIN, config.ADMIN_PASSWORD,
              show_password=False)
    if config.TEACHER_LOGIN != config.ADMIN_LOGIN:
        make_user(db, config.TEACHER_LOGIN, "Учитель", ROLE_TEACHER, config.TEACHER_PASSWORD,
                  [s["id"] for s in db.get("schools", [])], show_password=False)
    log.warning("Созданы пользователи панели: %s (суперадмин), %s (учитель)",
                config.ADMIN_LOGIN, config.TEACHER_LOGIN)
    if config.ADMIN_PASSWORD_IS_FALLBACK:
        log.warning("ADMIN_PASSWORD не задан: у суперадмина пока пароль учителя. "
                    "Смените его в панели.")
    return True


# ── Время уроков школы ───────────────────────────────────────────────────────

SCHOOL_DAYS = (5, 6)      # Пн–Пт или Пн–Сб
MAX_BELLS = 12


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def default_bells() -> List[Dict[str, str]]:
    """Время уроков новой школы: 7 уроков с 08:00, по 45 минут, перемены по 10."""
    first, length, pause = 8 * 60, 45, 10
    return [{"start": _hhmm(first + i * (length + pause)), "end": _hhmm(first + i * (length + pause) + length)}
            for i in range(7)]


def school_bells(school: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    return (school or {}).get("bells") or default_bells()


def bell_number(bells: List[Dict[str, str]], start: str, end: str) -> Optional[int]:
    """Номер урока (с 1), время которого точно совпадает с занятием; иначе None."""
    return next((n for n, bell in enumerate(bells, 1) if bell["start"] == start and bell["end"] == end), None)


def class_school_ids(db: Dict[str, Any]) -> Dict[int, int]:
    return {c["id"]: c["school_id"] for c in db.get("classes", [])}


def link_schedule_to_bells(db: Dict[str, Any], school_id: Optional[int] = None) -> bool:
    """Занятиям «в другое время», чьё время совпало с уроком школы, — место в таблице,
    если эта ячейка (день + номер урока) в школе ещё свободна. Возвращает True, если что-то
    привязалось."""
    schools = {s["id"]: s for s in db.get("schools", [])}
    owner = class_school_ids(db)
    taken = {(owner.get(e["class_id"]), e["weekday"], e["lesson"]) for e in db.get("schedule", [])
             if e.get("lesson")}
    changed = False
    for entry in db.get("schedule", []):
        sid = owner.get(entry.get("class_id"))
        if entry.get("lesson") or sid is None or (school_id is not None and sid != school_id):
            continue
        number = bell_number(school_bells(schools.get(sid)), entry["start"], entry["end"])
        if number and (sid, entry["weekday"], number) not in taken:
            entry["lesson"] = number
            taken.add((sid, entry["weekday"], number))
            changed = True
    return changed


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

    if _bootstrap_users(db):
        changed = True

    for key, default in (("classes", []), ("students", []), ("lessons", []), ("schedule", []),
                         ("settings", {})):
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
        if prize.get("category") not in PRIZE_CATEGORIES:
            prize["category"] = category_by_cost(int(prize.get("cost") or 0))
            changed = True

    from .themes import clean_theme, DEFAULT_THEME
    for school in db["schools"]:
        if not clean_theme(school.get("theme")):
            school["theme"] = DEFAULT_THEME
            changed = True
        if not school.get("bells"):
            school["bells"] = default_bells()
            changed = True
        if school.get("days") not in SCHOOL_DAYS:
            school["days"] = SCHOOL_DAYS[0]
            changed = True

    # Расписание таблицей: занятие, совпавшее по времени с уроком школы, встаёт в его ячейку
    fresh = [e for e in db["schedule"] if "lesson" not in e]
    for entry in fresh:
        entry["lesson"] = None
    if fresh:
        link_schedule_to_bells(db)
        changed = True

    # Урокам — постоянный номер: по нему учитель отменяет ошибочное начисление
    settings = db["settings"]
    last_lesson = max([settings.get("last_lesson_id", 0)] +
                      [int(lesson.get("id") or 0) for lesson in db["lessons"]])
    for lesson in reversed(db["lessons"]):      # новые сверху — номера по порядку проведения
        if not lesson.get("id"):
            last_lesson += 1
            lesson["id"] = last_lesson
            changed = True
        if "undone" not in lesson:
            lesson["undone"] = {}
            changed = True
    if settings.get("last_lesson_id", 0) != last_lesson:
        settings["last_lesson_id"] = last_lesson
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
