"""Хранилище отметок: SQLite из стандартной библиотеки, файл data/checkin.sqlite.

Почему не JSON-база «Звёзд»: отметки, уроки по датам и журналы растут каждый день,
а JSON переписывается целиком при каждом изменении. Почему не PostgreSQL: один
процесс, один каталог данных, никакой отдельной службы — как и у всей системы.

Правила:
- время — строки ISO в UTC (clock.iso), даты уроков — «ГГГГ-ММ-ДД» по часам школ;
- статусы — строки, а не перечисления: новые значения не требуют миграции;
- схема растёт миграциями по PRAGMA user_version (MIGRATIONS ниже, только дописывать);
- запись идёт под одной блокировкой процесса: сайт, бот и планировщик пишут из разных
  потоков, а SQLite даёт писать только одному.
"""
import json
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .. import config
from . import clock

_lock = threading.RLock()
_ready_for: Optional[Path] = None


# ── Схема ─────────────────────────────────────────────────────────────────────

MIGRATIONS: List[str] = [
    # 1 — первая версия модуля
    """
    CREATE TABLE settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT ''
    );

    -- Урок на конкретную дату: из занятия расписания (entry_id) или разовый (entry_id NULL).
    -- Названия — снимок на момент урока, как в журнале звёзд: класс переименуют,
    -- а отчёт за прошлый месяц должен остаться читаемым.
    CREATE TABLE planned_lessons (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id         INTEGER,
        date             TEXT NOT NULL,
        user_id          INTEGER,
        original_user_id INTEGER,
        school_id        INTEGER NOT NULL,
        class_id         INTEGER,
        teacher_name     TEXT NOT NULL DEFAULT '',
        school_name      TEXT NOT NULL DEFAULT '',
        class_name       TEXT NOT NULL DEFAULT '',
        room             TEXT NOT NULL DEFAULT '',
        starts_at        TEXT NOT NULL,
        ends_at          TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'planned',
        flag             TEXT NOT NULL DEFAULT '',
        cancel_source    TEXT NOT NULL DEFAULT '',
        checkin_id       INTEGER,
        late_minutes     INTEGER,
        hours_override   REAL,
        note             TEXT NOT NULL DEFAULT '',
        reviewed_by      INTEGER,
        reviewed_at      TEXT,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        UNIQUE (entry_id, date)
    );
    CREATE INDEX ix_lessons_date        ON planned_lessons (date);
    CREATE INDEX ix_lessons_user_date   ON planned_lessons (user_id, date);
    CREATE INDEX ix_lessons_status      ON planned_lessons (status);

    -- Отметка: геолокация, затем кругляшок. Ожидание кругляшка — тоже строка здесь
    -- (status = await_video), поэтому переживает перезапуск сервера.
    CREATE TABLE checkins (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           INTEGER NOT NULL,
        created_at        TEXT NOT NULL,
        lat               REAL,
        lon               REAL,
        accuracy_m        REAL,
        live              INTEGER NOT NULL DEFAULT 0,
        tg_message_id     INTEGER,
        school_id         INTEGER,
        distance_m        INTEGER,
        geo               TEXT NOT NULL DEFAULT '',
        lesson_ids        TEXT NOT NULL DEFAULT '[]',
        status            TEXT NOT NULL,
        video_file_id     TEXT,
        video_unique_id   TEXT,
        video_received_at TEXT,
        report_chat_id    INTEGER,
        report_message_id INTEGER,
        report_video_id   INTEGER,
        updated_at        TEXT NOT NULL
    );
    -- status — этап (await_video / accepted / no_video / no_lesson),
    -- geo — сверка с картой (ok / far / no_coords): это два разных вопроса.
    CREATE INDEX ix_checkins_user_created ON checkins (user_id, created_at);
    CREATE INDEX ix_checkins_status       ON checkins (status);
    CREATE INDEX ix_checkins_video        ON checkins (video_unique_id);

    -- Праздники и каникулы, отмены, замены. Диапазон дат: каникулы — одной записью.
    CREATE TABLE exceptions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        kind               TEXT NOT NULL,
        date_from          TEXT NOT NULL,
        date_to            TEXT NOT NULL,
        school_id          INTEGER,
        entry_id           INTEGER,
        user_id            INTEGER,
        substitute_user_id INTEGER,
        reason             TEXT NOT NULL DEFAULT '',
        created_by         INTEGER,
        created_at         TEXT NOT NULL
    );
    CREATE INDEX ix_exceptions_dates ON exceptions (date_from, date_to);

    -- Исходящие по уроку: карточка в чат «Отметки», напоминание преподавателю.
    -- Планировщик только ставит в очередь (status = pending), бот отправляет.
    -- Уникальность (урок, вид) — защита от повторов при каждом тике.
    CREATE TABLE notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        lesson_id  INTEGER NOT NULL,
        kind       TEXT NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending',
        chat_id    INTEGER,
        message_id INTEGER,
        created_at TEXT NOT NULL,
        sent_at    TEXT,
        UNIQUE (lesson_id, kind)
    );
    CREATE INDEX ix_notifications_status ON notifications (status);

    -- Ручные решения и правки: кто, когда, что было и что стало, почему.
    CREATE TABLE audit_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        user_name  TEXT NOT NULL DEFAULT '',
        entity     TEXT NOT NULL,
        entity_id  INTEGER,
        action     TEXT NOT NULL,
        before     TEXT,
        after      TEXT,
        reason     TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_audit_entity  ON audit_log (entity, entity_id);
    CREATE INDEX ix_audit_created ON audit_log (created_at);

    -- Шаги бота: пришла геолокация, отклонён кругляшок и т. п. — для разбора спорных случаев.
    CREATE TABLE events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        tg_id      INTEGER,
        event_type TEXT NOT NULL,
        payload    TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_events_created ON events (created_at);

    -- Бот спросил у администратора причину решения и ждёт ответа на своё сообщение.
    CREATE TABLE pending_actions (
        chat_id    INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        lesson_id  INTEGER NOT NULL,
        action     TEXT NOT NULL,
        user_id    INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    );

    -- Отметки о разовых работах планировщика: «сводка за октябрь уже отправлена».
    CREATE TABLE jobs (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    );
    """,
]


# ── Настройки, которые администратор меняет в панели ─────────────────────────

SETTINGS: Dict[str, Dict[str, Any]] = {
    "grace_minutes": {
        "type": int, "default": 5, "min": 0, "max": 60,
        "label": "Опоздание не считается, минут",
        "description": "Отметка в пределах этого времени после начала урока — «вовремя».",
    },
    "checkin_window_before_min": {
        "type": int, "default": 60, "min": 5, "max": 240,
        "label": "Отметка принимается заранее, минут",
        "description": "За сколько минут до начала первого урока можно отметиться.",
    },
    "video_note_deadline_sec": {
        "type": int, "default": 120, "min": 30, "max": 900,
        "label": "Время на кругляшок, секунд",
        "description": "Сколько ждать видео после геолокации.",
    },
    "review_after_min": {
        "type": int, "default": 15, "min": 1, "max": 180,
        "label": "Нет отметки — на проверку через, минут",
        "description": "Через сколько минут после начала урока без отметки он уходит в очередь.",
    },
    "merge_gap_min": {
        "type": int, "default": 30, "min": 0, "max": 240,
        "label": "Уроки подряд — перерыв до, минут",
        "description": "Уроки одного преподавателя в одной школе с таким перерывом закрываются одной отметкой.",
    },
    "lesson_horizon_days": {
        "type": int, "default": 14, "min": 1, "max": 60,
        "label": "Уроки на сколько дней вперёд",
        "description": "На сколько дней вперёд раскладывать расписание по датам.",
    },
    "notify_teacher_late": {
        "type": bool, "default": True,
        "label": "Напоминать преподавателю",
        "description": "Написать преподавателю, если урок начался, а отметки нет.",
    },
    "report_chat_id": {
        "type": int, "default": 0, "min": -10**15, "max": 10**15, "hidden": True,
        "label": "Чат «Отметки»",
        "description": "Группа, куда приходят отметки и карточки на проверку. Задаётся командой в группе.",
    },
}


def _encode(key: str, value: Any) -> str:
    kind = SETTINGS[key]["type"]
    if kind is bool:
        return "1" if value else "0"
    return str(int(value)) if kind is int else str(value)


def _decode(key: str, raw: Optional[str]) -> Any:
    spec = SETTINGS[key]
    if raw is None:
        return spec["default"]
    if spec["type"] is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if spec["type"] is int:
        try:
            return int(raw)
        except ValueError:
            return spec["default"]
    return raw


def clean_setting(key: str, value: Any) -> Any:
    """Проверяет значение настройки. ValueError с понятным текстом — если не подходит."""
    if key not in SETTINGS:
        raise ValueError("Такой настройки нет")
    spec = SETTINGS[key]
    if spec["type"] is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{spec['label']}: ожидалось да или нет")
        return value
    if spec["type"] is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError(f"{spec['label']}: ожидалось целое число")
        value = int(value)
        if not spec["min"] <= value <= spec["max"]:
            raise ValueError(f"{spec['label']}: от {spec['min']} до {spec['max']}")
        return value
    return str(value)


# ── Соединение ────────────────────────────────────────────────────────────────

def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    # Обычный журнал, не WAL: записей мало, а лишние файлы -wal/-shm рядом с базой
    # копия каталога data/ может захватить несогласованно
    conn.execute("PRAGMA journal_mode = DELETE")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _statements(script):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    # Настройки: недостающие ключи дописываются, изменённые администратором не трогаются
    for key, spec in SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
                     (key, _encode(key, spec["default"]), spec["description"]))


def _statements(script: str) -> Iterator[str]:
    """Скрипт миграции по одной инструкции: executescript сам делает COMMIT,
    а миграция должна пройти целиком или никак."""
    buffer = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            yield "\n".join(buffer)
            buffer = []
    if buffer:
        yield "\n".join(buffer)


def schema_version() -> int:
    with tx() as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Соединение со схемой последней версии. Держит блокировку процесса."""
    global _ready_for
    path = config.CHECKIN_DB_FILE
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _open(path)
        try:
            if _ready_for != path or not path.exists() or path.stat().st_size == 0:
                _migrate(conn)
                _ready_for = path
            yield conn
        finally:
            conn.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Транзакция: всё внутри проходит целиком или откатывается.

    BEGIN IMMEDIATE сразу берёт право записи — так два писателя не ловят
    «database is locked» посреди работы, а встают в очередь на входе.
    """
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def reset_for_tests() -> None:
    """Тесты удаляют файл между проверками — следующее открытие создаст схему заново."""
    global _ready_for
    with _lock:
        _ready_for = None
        for suffix in ("", "-wal", "-shm"):
            target = Path(str(config.CHECKIN_DB_FILE) + suffix)
            if target.exists():
                target.unlink()


# ── Помощники запросов ────────────────────────────────────────────────────────

def row(record: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(record) if record is not None else None


def rows(records: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in records]


def dumps(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def loads(value: Optional[str]) -> Any:
    return None if value in (None, "") else json.loads(value)


def now() -> str:
    return clock.iso(clock.now_utc())


# ── Настройки ─────────────────────────────────────────────────────────────────

def get_settings(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    def _read(c: sqlite3.Connection) -> Dict[str, Any]:
        raw = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
        return {key: _decode(key, raw.get(key)) for key in SETTINGS}

    if conn is not None:
        return _read(conn)
    with tx() as c:
        return _read(c)


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> Any:
    value = clean_setting(key, value)
    conn.execute("INSERT INTO settings (key, value, description) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, _encode(key, value), SETTINGS[key]["description"]))
    return value


# ── Журналы ───────────────────────────────────────────────────────────────────

def log_event(conn: sqlite3.Connection, event_type: str, user_id: Optional[int] = None,
              tg_id: Optional[int] = None, payload: Any = None) -> None:
    conn.execute("INSERT INTO events (user_id, tg_id, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                 (user_id, tg_id, event_type, dumps(payload), now()))


def audit(conn: sqlite3.Connection, user: Optional[Dict[str, Any]], entity: str, entity_id: Optional[int],
          action: str, before: Any = None, after: Any = None, reason: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log (user_id, user_name, entity, entity_id, action, before, after, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ((user or {}).get("id"), (user or {}).get("name", ""), entity, entity_id, action,
         dumps(before), dumps(after), reason or "", now()))


def job_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    found = conn.execute("SELECT value FROM jobs WHERE key = ?", (key,)).fetchone()
    return found["value"] if found else None


def set_job_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO jobs (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (key, value, now()))


# ── Резервная копия ───────────────────────────────────────────────────────────

def snapshot(target: Path) -> Path:
    """Согласованная копия базы средствами SQLite (не копия файла посреди записи).
    Её и забирает deploy/backup.sh в архив вместе с каталогом data/."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with _connection() as conn:
        dest = sqlite3.connect(str(tmp))
        try:
            conn.backup(dest)
        finally:
            dest.close()
    tmp.replace(target)
    return target


if __name__ == "__main__":       # python -m backend.checkin.store snapshot [путь]
    if len(sys.argv) >= 2 and sys.argv[1] == "snapshot":
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else config.DATA_DIR / "checkin.backup.sqlite"
        print(snapshot(out))
    else:
        print("Использование: python -m backend.checkin.store snapshot [путь]")
        sys.exit(2)
