"""SQLite «Мониторинга»: data/monitoring.sqlite3.

Почему не основная JSON-база: каждый ответ ученика — одна строка, а не перезапись
всего журнала школы. Страница ученика спрашивает «начался ли опрос» раз в 5 секунд —
это чтение одной маленькой таблицы.

Соединение открывается на каждую операцию: это дешёвая операция, а файл между
запросами остаётся закрытым (тесты удаляют его перед каждым тестом, бэкап копирует).
Записи идут под одной блокировкой процесса: проверки вида «класс уже в открытом
опросе» и «не больше 400 участников» должны видеть данные без гонок.

Время — секунды UTC (store.now()). Его сверяет сервер, а не устройство ученика.
"""
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .. import config

SCHEMA_VERSION = 1
MAX_PARTICIPANTS = 400
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # без l/1, o/0, i
_CODE_LENGTH = 5

_write_lock = threading.RLock()     # повторный вход: первое соединение под записью проверяет схему
_ready_for: Optional[str] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS school_links (
    school_id   INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    school_name TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS forms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL,
    school_id  INTEGER,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'survey',
    settings   TEXT NOT NULL DEFAULT '{}',
    questions  TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id   INTEGER NOT NULL,
    school_name TEXT NOT NULL,
    owner_id    INTEGER NOT NULL,
    owner_name  TEXT NOT NULL,
    form_id     INTEGER,
    set_key     TEXT,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'survey',
    settings    TEXT NOT NULL DEFAULT '{}',
    questions   TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT '',
    goals       TEXT NOT NULL DEFAULT '[]',
    all_classes INTEGER NOT NULL DEFAULT 0,
    lesson_date TEXT NOT NULL,
    code        TEXT,
    started_at  INTEGER NOT NULL,
    closes_at   INTEGER,
    closed_at   INTEGER
);
CREATE INDEX IF NOT EXISTS runs_school ON runs (school_id, closed_at);
CREATE TABLE IF NOT EXISTS run_classes (
    run_id     INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    class_id   INTEGER NOT NULL,
    class_name TEXT NOT NULL,
    PRIMARY KEY (run_id, class_id)
);
CREATE TABLE IF NOT EXISTS participants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    token       TEXT NOT NULL UNIQUE,
    last_name   TEXT NOT NULL,
    first_name  TEXT NOT NULL,
    class_id    INTEGER NOT NULL,
    class_name  TEXT NOT NULL,
    student_id  INTEGER,
    name_key    TEXT NOT NULL,
    joined_at   INTEGER NOT NULL,
    finished_at INTEGER,
    score       REAL
);
CREATE INDEX IF NOT EXISTS participants_run ON participants (run_id);
CREATE TABLE IF NOT EXISTS answers (
    participant_id INTEGER NOT NULL REFERENCES participants (id) ON DELETE CASCADE,
    question_id    TEXT NOT NULL,
    value          TEXT NOT NULL,
    correct        INTEGER,
    points         REAL,
    answered_at    INTEGER NOT NULL,
    PRIMARY KEY (participant_id, question_id)
);
"""


def now() -> int:
    return int(time.time())


def db_file():
    return config.DATA_DIR / "monitoring.sqlite3"


def _open() -> sqlite3.Connection:
    global _ready_for
    path = db_file()
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if _ready_for != str(path):
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn.execute("PRAGMA journal_mode = WAL")
            if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                conn.executescript(SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            _ready_for = str(path)
    return conn


def forget() -> None:
    """Следующее соединение заново проверит схему — после удаления файла (тесты)."""
    global _ready_for
    _ready_for = None


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = _open()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """Транзакция записи: всё или ничего, по одной за раз."""
    with _write_lock:
        conn = _open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def backup_to(target: str) -> None:
    """Согласованная копия для бэкапа (файл в режиме WAL нельзя просто скопировать)."""
    with read() as conn:
        dest = sqlite3.connect(target)
        try:
            conn.backup(dest)
        finally:
            dest.close()


# ── Ссылка школы ──────────────────────────────────────────────────────────────

def school_link(conn: sqlite3.Connection, school_id: int, school_name: str) -> str:
    """Постоянный код ссылки школы /m/<код>; создаётся при первом обращении."""
    row = conn.execute("SELECT code, school_name FROM school_links WHERE school_id = ?", (school_id,)).fetchone()
    if row:
        if row["school_name"] != school_name:
            conn.execute("UPDATE school_links SET school_name = ? WHERE school_id = ?", (school_name, school_id))
        return row["code"]
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if not conn.execute("SELECT 1 FROM school_links WHERE code = ?", (code,)).fetchone():
            break
    conn.execute("INSERT INTO school_links (school_id, code, school_name, created_at) VALUES (?, ?, ?, ?)",
                 (school_id, code, school_name, now()))
    return code


def school_by_code(conn: sqlite3.Connection, code: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT school_id, school_name, code FROM school_links WHERE code = ?",
                        ((code or "").lower(),)).fetchone()


def code_of_school(conn: sqlite3.Connection, school_id: int) -> Optional[str]:
    row = conn.execute("SELECT code FROM school_links WHERE school_id = ?", (school_id,)).fetchone()
    return row["code"] if row else None


def drop_school(school_id: int) -> None:
    """Школу удалили — её ссылка, опросы и ответы больше не нужны."""
    with write() as conn:
        conn.execute("DELETE FROM runs WHERE school_id = ?", (school_id,))
        conn.execute("DELETE FROM forms WHERE school_id = ?", (school_id,))
        conn.execute("DELETE FROM school_links WHERE school_id = ?", (school_id,))


# ── Запуски ───────────────────────────────────────────────────────────────────

OPEN_SQL = "closed_at IS NULL AND (closes_at IS NULL OR closes_at > ?)"


def is_open(run: Dict[str, Any], at: Optional[int] = None) -> bool:
    at = now() if at is None else at
    return run["closed_at"] is None and (run["closes_at"] is None or run["closes_at"] > at)


def closed_at(run: Dict[str, Any]) -> Optional[int]:
    """Когда запуск закрылся: вручную или по окончании окна. None — ещё открыт."""
    if run["closed_at"] is not None:
        return run["closed_at"]
    if run["closes_at"] is not None and run["closes_at"] <= now():
        return run["closes_at"]
    return None


def run_dict(row: sqlite3.Row) -> Dict[str, Any]:
    run = dict(row)
    for key in ("settings", "questions", "goals"):
        run[key] = json.loads(run[key])
    run["all_classes"] = bool(run["all_classes"])
    return run


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return run_dict(row) if row else None


def run_classes(conn: sqlite3.Connection, run_ids: Iterable[int]) -> Dict[int, List[Dict[str, Any]]]:
    ids = list(run_ids)
    result: Dict[int, List[Dict[str, Any]]] = {i: [] for i in ids}
    if not ids:
        return result
    marks = ",".join("?" * len(ids))
    for row in conn.execute(f"SELECT run_id, class_id, class_name FROM run_classes WHERE run_id IN ({marks}) "
                            "ORDER BY class_name", ids):
        result[row["run_id"]].append({"id": row["class_id"], "name": row["class_name"]})
    return result


def open_runs(conn: sqlite3.Connection, school_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(f"SELECT * FROM runs WHERE school_id = ? AND {OPEN_SQL} ORDER BY id",
                        (school_id, now())).fetchall()
    return [run_dict(r) for r in rows]


def busy_classes(conn: sqlite3.Connection, school_id: int, except_run: Optional[int] = None) -> Dict[int, str]:
    """Классы, у которых уже идёт опрос: {class_id: название набора}."""
    rows = conn.execute("SELECT rc.class_id, r.title FROM runs r JOIN run_classes rc ON rc.run_id = r.id "
                        "WHERE r.school_id = ? AND r.closed_at IS NULL AND (r.closes_at IS NULL OR r.closes_at > ?) "
                        "AND r.id != ?", (school_id, now(), except_run or 0)).fetchall()
    return {r["class_id"]: r["title"] for r in rows}


def create_run(conn: sqlite3.Connection, fields: Dict[str, Any], classes: List[Dict[str, Any]]) -> int:
    data = dict(fields)
    for key in ("settings", "questions", "goals"):
        data[key] = json.dumps(data[key], ensure_ascii=False)
    columns = ", ".join(data)
    cursor = conn.execute(f"INSERT INTO runs ({columns}) VALUES ({', '.join('?' * len(data))})", list(data.values()))
    run_id = cursor.lastrowid
    conn.executemany("INSERT INTO run_classes (run_id, class_id, class_name) VALUES (?, ?, ?)",
                     [(run_id, c["id"], c["name"]) for c in classes])
    return run_id


def list_runs(conn: sqlite3.Connection, school_id: int, owner_id: Optional[int], limit: int = 30) -> List[Dict[str, Any]]:
    """Запуски школы, новые сверху. owner_id=None — все (администратор)."""
    sql = "SELECT * FROM runs WHERE school_id = ?"
    args: List[Any] = [school_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        args.append(owner_id)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
    return [run_dict(r) for r in rows]


def participant_counts(conn: sqlite3.Connection, run_ids: Iterable[int]) -> Dict[int, Dict[str, int]]:
    ids = list(run_ids)
    result = {i: {"joined": 0, "finished": 0} for i in ids}
    if not ids:
        return result
    marks = ",".join("?" * len(ids))
    for row in conn.execute(f"SELECT run_id, COUNT(*) AS joined, COUNT(finished_at) AS finished "
                            f"FROM participants WHERE run_id IN ({marks}) GROUP BY run_id", ids):
        result[row["run_id"]] = {"joined": row["joined"], "finished": row["finished"]}
    return result


# ── Участники и ответы ────────────────────────────────────────────────────────

def new_token() -> str:
    return secrets.token_urlsafe(18)


def participant_by_token(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    if not token:
        return None
    return conn.execute("SELECT * FROM participants WHERE token = ?", (token,)).fetchone()


def participants(conn: sqlite3.Connection, run_id: int) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM participants WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def answers_of(conn: sqlite3.Connection, participant_id: int) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute("SELECT question_id, value FROM answers WHERE participant_id = ?", (participant_id,))
    return {r["question_id"]: json.loads(r["value"]) for r in rows}


def run_answers(conn: sqlite3.Connection, run_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT a.participant_id, a.question_id, a.value, a.correct, a.points, p.last_name, p.first_name, "
        "p.class_name FROM answers a JOIN participants p ON p.id = a.participant_id WHERE p.run_id = ? "
        "ORDER BY p.id", (run_id,)).fetchall()
    return [{"participant_id": r["participant_id"], "question_id": r["question_id"],
             "value": json.loads(r["value"]), "correct": r["correct"], "points": r["points"],
             "name": f"{r['last_name']} {r['first_name']}", "class_name": r["class_name"]} for r in rows]


def answer_counts(conn: sqlite3.Connection, run_id: int) -> Dict[int, int]:
    rows = conn.execute("SELECT a.participant_id, COUNT(*) AS n FROM answers a JOIN participants p "
                        "ON p.id = a.participant_id WHERE p.run_id = ? GROUP BY a.participant_id", (run_id,))
    return {r["participant_id"]: r["n"] for r in rows}


def save_answer(conn: sqlite3.Connection, participant_id: int, question_id: str,
                value: Optional[Dict[str, Any]], correct: Optional[bool], points: float) -> None:
    if value is None:
        conn.execute("DELETE FROM answers WHERE participant_id = ? AND question_id = ?", (participant_id, question_id))
        return
    conn.execute(
        "INSERT INTO answers (participant_id, question_id, value, correct, points, answered_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (participant_id, question_id) DO UPDATE SET "
        "value = excluded.value, correct = excluded.correct, points = excluded.points, answered_at = excluded.answered_at",
        (participant_id, question_id, json.dumps(value, ensure_ascii=False),
         None if correct is None else int(correct), points, now()))
