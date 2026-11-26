"""Страница ученика /m/<код школы>: ответы без входа и пароля.

Что можно узнать отсюда: название школы, классы, у которых сейчас идёт опрос,
и свои вопросы со своими ответами. Списков учеников, чужих ответов и правильных
ответов теста здесь нет.

Основная JSON-база читается только при входе в опрос — чтобы связать ученика
с записью в классе, и то через кеш по времени изменения файла. Опрос раз в 5 секунд
и ответы работают только с SQLite.
"""
import threading
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request, status

from .. import config, db, ratelimit, schemas
from ..monitoring import logic, store

STARS_REASON = "Опрос «{title}»"

router = APIRouter(prefix="/api/m", tags=["monitoring-public"])

ratelimit.LIMITS.setdefault("m-link", 60)      # неизвестные ссылки с одного адреса
ratelimit.LIMITS.setdefault("m-code", 40)      # неверный код урока: адрес + опрос


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(code, message)


def _too_many(seconds: int) -> HTTPException:
    minutes = max(1, (seconds + 59) // 60)
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                         f"Слишком много попыток. Попробуй через {minutes} мин.",
                         headers={"Retry-After": str(seconds)})


def _school(conn, code: str, request: Request):
    ip = ratelimit.client_key(request)
    wait = ratelimit.retry_after("m-link", ip)
    if wait:
        raise _too_many(wait)
    school = store.school_by_code(conn, code)
    if not school:
        ratelimit.register_failure("m-link", ip)
        raise _error(status.HTTP_404_NOT_FOUND, "Ссылка не найдена — проверь адрес у учителя")
    return school


# ── Список класса для связи с учеником ────────────────────────────────────────

_roster_lock = threading.Lock()
_roster: Dict[str, Any] = {"stamp": None, "by_class": {}}


def class_roster(class_id: int) -> List[Tuple[int, str]]:
    """Ученики класса (id, ФИО). Перечитывает базу, только если файл изменился."""
    try:
        stamp = config.DB_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        stamp = None
    with _roster_lock:
        if stamp is None or stamp != _roster["stamp"]:
            by_class: Dict[int, List[Tuple[int, str]]] = {}
            for s in db.load_db()["students"]:
                by_class.setdefault(s.get("class_id"), []).append((s["id"], s.get("name", "")))
            _roster.update(stamp=stamp, by_class=by_class)
        return list(_roster["by_class"].get(class_id, []))


# ── Состояние участника ───────────────────────────────────────────────────────

def _me(conn, participant) -> Dict[str, Any]:
    run = store.get_run(conn, participant["run_id"])
    ended = store.closed_at(run)
    if participant["finished_at"] is not None:
        state = "done"
    elif ended is not None:
        state = "closed"
    else:
        state = "active"
    me: Dict[str, Any] = {
        "status": state,
        "last_name": participant["last_name"], "first_name": participant["first_name"],
        "class_id": participant["class_id"], "class_name": participant["class_name"],
        "title": run["title"],
        "questions": [logic.public_question(q) for q in
                      (logic.shuffled(run["questions"], participant["token"]) if run["settings"].get("shuffle")
                       else run["questions"])],
        "answers": store.answers_of(conn, participant["id"]),
        "ended_ago": None if ended is None else store.now() - ended,
        # Звёзды за опрос: сколько обещано (только вошедшим из кабинета) и сколько начислено
        "stars": int(run["settings"].get("stars") or 0) if participant["verified"] else 0,
        "stars_given": participant["stars_given"],
        "verified": bool(participant["verified"]),
    }
    after = run["settings"].get("after", "none")
    if state == "done" and after != "none" and any(q.get("correct") for q in run["questions"]):
        me["result"] = {"score": participant["score"] or 0,
                        "max": sum(float(q.get("points") or 1) for q in run["questions"] if q.get("correct"))}
        if after == "full":
            me["result"]["correct"] = {q["id"]: q["correct"] for q in run["questions"] if q.get("correct")}
    return me


def _open_classes(conn, school_id: int) -> List[Dict[str, Any]]:
    runs = store.open_runs(conn, school_id)
    classes = store.run_classes(conn, [r["id"] for r in runs])
    result = [{"id": c["id"], "name": c["name"], "code": bool(r["code"])} for r in runs for c in classes[r["id"]]]
    return sorted(result, key=lambda c: c["name"])


@router.get("/{code}/state")
def state(code: str, request: Request, p: str = ""):
    """Что показать ученику сейчас. Страница спрашивает раз в 5 секунд."""
    with store.read() as conn:
        school = _school(conn, code, request)
        result: Dict[str, Any] = {"school": school["school_name"], "classes": _open_classes(conn, school["school_id"]),
                                  "me": None}
        participant = store.participant_by_token(conn, p[:64])
        if participant:
            run = store.get_run(conn, participant["run_id"])
            if run and run["school_id"] == school["school_id"]:
                result["me"] = _me(conn, participant)
    return result


def _person(value: str, label: str) -> str:
    text = logic.clean_person(value)
    if not text or not any(ch.isalpha() for ch in text):
        raise _error(status.HTTP_400_BAD_REQUEST, f"Напиши {label}")
    return text


@router.post("/{code}/join")
def join(code: str, item: schemas.MonitorJoin, request: Request):
    last = _person(item.last_name, "фамилию")
    first = _person(item.first_name, "имя")
    with store.read() as conn:
        school = _school(conn, code, request)
    roster = class_roster(item.class_id)
    ip = ratelimit.client_key(request)

    with store.write() as conn:
        run = next((r for r in store.open_runs(conn, school["school_id"])
                    if any(c["id"] == item.class_id for c in store.run_classes(conn, [r["id"]])[r["id"]])), None)
        if not run:
            raise _error(status.HTTP_409_CONFLICT, "Для этого класса опрос сейчас не идёт")
        if run["code"]:
            key = f"{ip}:{run['id']}"
            wait = ratelimit.retry_after("m-code", key)
            if wait:
                raise _too_many(wait)
            given = (item.code or "").strip()
            if not given:
                raise _error(status.HTTP_400_BAD_REQUEST, "Введи код урока — 4 цифры на экране класса")
            if given != run["code"]:
                ratelimit.register_failure("m-code", key)
                raise _error(status.HTTP_400_BAD_REQUEST, "Неверный код урока — посмотри на экран класса")
        count = conn.execute("SELECT COUNT(*) FROM participants WHERE run_id = ?", (run["id"],)).fetchone()[0]
        if count >= store.MAX_PARTICIPANTS:
            raise _error(status.HTTP_400_BAD_REQUEST, "В опросе уже слишком много участников")
        class_name = next(c["name"] for c in store.run_classes(conn, [run["id"]])[run["id"]] if c["id"] == item.class_id)
        token = store.new_token()
        conn.execute(
            "INSERT INTO participants (run_id, token, last_name, first_name, class_id, class_name, student_id, "
            "name_key, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run["id"], token, last, first, item.class_id, class_name,
             logic.match_student(roster, last, first), logic.name_key(last, first), store.now()))
        participant = store.participant_by_token(conn, token)
        me = _me(conn, participant)
    return {"token": token, "me": me}


def _active(conn, token: str):
    """Участник, который ещё может отвечать: 404 — убран учителем, 409 — опрос закрыт."""
    participant = store.participant_by_token(conn, token)
    if not participant:
        raise _error(status.HTTP_404_NOT_FOUND, "Тебя нет в списке опроса — войди заново")
    run = store.get_run(conn, participant["run_id"])
    if not store.is_open(run):
        raise _error(status.HTTP_409_CONFLICT, "Опрос закрыт — ответы больше не принимаются")
    if participant["finished_at"] is not None:
        raise _error(status.HTTP_409_CONFLICT, "Ответы уже отправлены")
    return participant, run


@router.post("/answer")
def answer(item: schemas.MonitorAnswer):
    """Один ответ — сразу в базу. Повторная отправка того же ответа безопасна."""
    with store.write() as conn:
        participant, run = _active(conn, item.token)
        question = next((q for q in run["questions"] if q["id"] == item.question_id), None)
        if not question:
            raise _error(status.HTTP_400_BAD_REQUEST, "Такого вопроса нет")
        try:
            value = logic.validate_answer(question, item.value)
        except logic.AnswerError as error:
            raise _error(status.HTTP_400_BAD_REQUEST, str(error))
        correct, points = logic.grade(question, value)
        store.save_answer(conn, participant["id"], question["id"], value, correct, points)
    return {"ok": True}


def award_stars(participant_id: int) -> int:
    """Звёзды за пройденный опрос — ученику, вошедшему из кабинета, один раз.
    Сначала отмечаем начисление в SQLite (повтор не даст второй раз), потом — звёзды
    в основной базе. Блокировки не вкладываются одна в другую."""
    with store.write() as conn:
        p = conn.execute("SELECT * FROM participants WHERE id = ?", (participant_id,)).fetchone()
        if not p or not p["verified"] or p["student_id"] is None or p["finished_at"] is None \
                or p["stars_given"] is not None:
            return 0
        run = store.get_run(conn, p["run_id"])
        stars = int(run["settings"].get("stars") or 0)
        if stars <= 0:
            return 0
        conn.execute("UPDATE participants SET stars_given = ? WHERE id = ?", (stars, participant_id))
        title = run["title"]

    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, p["student_id"])
        if not student:
            return db.NO_SAVE
        student["total_stars"] = int(student.get("total_stars", 0)) + stars
        student.setdefault("history", []).insert(0, {
            "date": db.today(), "delta": stars, "reason": STARS_REASON.format(title=title),
        })
        return None

    try:
        db.transaction(_apply)
    except Exception:
        with store.write() as conn:      # звёзды не дошли — снимаем отметку, начисление повторится
            conn.execute("UPDATE participants SET stars_given = NULL WHERE id = ?", (participant_id,))
        raise
    return stars


@router.post("/finish")
def finish(item: schemas.MonitorToken):
    with store.write() as conn:
        participant, run = _active(conn, item.token)
        given = store.answers_of(conn, participant["id"])
        missing = logic.missing_required(run["questions"], given)
        if missing:
            raise _error(status.HTTP_400_BAD_REQUEST, "Ответь на все обязательные вопросы")
        score: Optional[float] = None
        if any(q.get("correct") for q in run["questions"]):
            score = conn.execute("SELECT COALESCE(SUM(points), 0) FROM answers WHERE participant_id = ?",
                                 (participant["id"],)).fetchone()[0]
        conn.execute("UPDATE participants SET finished_at = ?, score = ? WHERE id = ?",
                     (store.now(), score, participant["id"]))
    award_stars(participant["id"])
    with store.read() as conn:
        me = _me(conn, store.participant_by_token(conn, item.token))
    return {"ok": True, "me": me}


# ── Опрос из кабинета ученика ─────────────────────────────────────────────────

def run_for_class(conn, school_id: int, class_id: int) -> Optional[Dict[str, Any]]:
    """Опрос, который сейчас идёт у этого класса (у класса одновременно — не больше одного)."""
    runs = store.open_runs(conn, school_id)
    classes = store.run_classes(conn, [r["id"] for r in runs])
    return next((r for r in runs if any(c["id"] == class_id for c in classes[r["id"]])), None)


def student_poll(database: Dict[str, Any], student: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Что показать в кабинете: опрос класса сейчас — название, звёзды, пройден ли."""
    cls = db.find_class(database, student.get("class_id"))
    if not cls:
        return None
    with store.read() as conn:
        run = run_for_class(conn, cls["school_id"], cls["id"])
        if not run:
            return None
        mine = conn.execute("SELECT finished_at, stars_given FROM participants WHERE run_id = ? AND student_id = ? "
                            "AND verified = 1 ORDER BY id DESC LIMIT 1", (run["id"], student["id"])).fetchone()
    return {"run_id": run["id"], "title": run["title"], "stars": int(run["settings"].get("stars") or 0),
            "code": bool(run["code"]), "started": mine is not None,
            "done": bool(mine and mine["finished_at"] is not None),
            "stars_given": mine["stars_given"] if mine else None}


def join_from_cabinet(database: Dict[str, Any], student: Dict[str, Any], code: str, ip: str) -> Dict[str, Any]:
    """Ученик из своего кабинета входит в опрос своего класса — без ввода имени.
    Уже входил в этот опрос — возвращаем то же участие (продолжить с того же вопроса)."""
    cls = db.find_class(database, student.get("class_id"))
    if not cls:
        raise _error(status.HTTP_409_CONFLICT, "Для твоего класса опрос сейчас не идёт")
    words = (student.get("name") or "").split()
    last, first = (words + ["", ""])[:2]
    with store.write() as conn:
        link = store.school_link(conn, cls["school_id"], db.school_name_of_class(database, cls["id"]))
        run = run_for_class(conn, cls["school_id"], cls["id"])
        if not run:
            raise _error(status.HTTP_409_CONFLICT, "Для твоего класса опрос сейчас не идёт")
        mine = conn.execute("SELECT token FROM participants WHERE run_id = ? AND student_id = ? AND verified = 1 "
                            "ORDER BY id DESC LIMIT 1", (run["id"], student["id"])).fetchone()
        if mine:
            return {"token": mine["token"], "link": link}
        if run["code"]:
            key = f"{ip}:{run['id']}"
            wait = ratelimit.retry_after("m-code", key)
            if wait:
                raise _too_many(wait)
            given = (code or "").strip()
            if not given:
                raise _error(status.HTTP_400_BAD_REQUEST, "Введи код урока — 4 цифры на экране класса")
            if given != run["code"]:
                ratelimit.register_failure("m-code", key)
                raise _error(status.HTTP_400_BAD_REQUEST, "Неверный код урока — посмотри на экран класса")
        count = conn.execute("SELECT COUNT(*) FROM participants WHERE run_id = ?", (run["id"],)).fetchone()[0]
        if count >= store.MAX_PARTICIPANTS:
            raise _error(status.HTTP_400_BAD_REQUEST, "В опросе уже слишком много участников")
        token = store.new_token()
        conn.execute(
            "INSERT INTO participants (run_id, token, last_name, first_name, class_id, class_name, student_id, "
            "name_key, joined_at, verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (run["id"], token, logic.clean_person(last) or "—", logic.clean_person(first) or "—",
             cls["id"], cls["name"], student["id"], logic.name_key(last, first), store.now()))
    return {"token": token, "link": link}
