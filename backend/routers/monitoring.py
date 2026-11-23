"""«Мониторинг» в панели: наборы вопросов, ссылка школы, запуск, живой экран, итоги.

Весь роутер — require_teacher: администрация школы раздел не видит (решение
этапа 1; роль завуча в мониторинге — этап 3 ТЗ). Запуск и его ответы видят
автор и администратор; чужое отвечает 404, как несуществующее.
"""
import secrets
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from .. import config, db, schemas
from ..monitoring import library, logic, store
from ..security import require_teacher
from .cards import _qr_png
from .common import bad_request, not_found, school_or_404

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])

User = Dict[str, Any]


def _visible(user: User, run: Dict[str, Any], database: Dict[str, Any]) -> bool:
    if db.is_superadmin(user):
        return True
    return run["owner_id"] == user["id"] and run["school_id"] in db.allowed_school_ids(database, user)


def _run_or_404(conn, run_id: int, user: User) -> Dict[str, Any]:
    run = store.get_run(conn, run_id)
    if not run or not _visible(user, run, db.load_db()):
        raise not_found("Опрос не найден")
    return run


def _brief(run: Dict[str, Any], classes: List[Dict[str, Any]], counts: Dict[str, int]) -> Dict[str, Any]:
    """Запуск для списка и живого экрана — без вопросов и ответов."""
    t = store.now()
    ended = store.closed_at(run)
    return {
        "id": run["id"], "title": run["title"], "set_key": run["set_key"], "kind": run["kind"],
        "classes": classes, "all_classes": run["all_classes"], "topic": run["topic"], "goals": run["goals"],
        "code": run["code"], "lesson_date": run["lesson_date"], "owner_name": run["owner_name"],
        "questions": len(run["questions"]), "open": ended is None,
        "stopped": run["closed_at"] is not None,          # завершил учитель — продлить нельзя
        "seconds_left": None if run["closes_at"] is None or ended is not None else run["closes_at"] - t,
        "ended_ago": None if ended is None else t - ended,
        "joined": counts.get("joined", 0), "finished": counts.get("finished", 0),
    }


def _briefs(conn, runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ids = [r["id"] for r in runs]
    classes = store.run_classes(conn, ids)
    counts = store.participant_counts(conn, ids)
    return [_brief(r, classes[r["id"]], counts[r["id"]]) for r in runs]


# ── Библиотека и ссылка ───────────────────────────────────────────────────────

@router.get("/library", dependencies=[Depends(require_teacher)])
def get_library():
    return library.public_library()


def _link(school: Dict[str, Any]) -> str:
    with store.write() as conn:
        return store.school_link(conn, school["id"], school["name"])


@router.get("/link")
def get_link(school_id: int, user: User = Depends(require_teacher)):
    school = school_or_404(db.load_db(), school_id, user)
    code = _link(school)
    return {"code": code, "path": f"/m/{code}", "url": config.site_url(f"/m/{code}")}


@router.get("/link/qr.png")
def link_qr(school_id: int, size: int = 10, user: User = Depends(require_teacher)):
    school = school_or_404(db.load_db(), school_id, user)
    png = _qr_png(config.site_url(f"/m/{_link(school)}"), box_size=max(4, min(24, size)))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


# ── Запуски ───────────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(school_id: int, user: User = Depends(require_teacher)):
    school_or_404(db.load_db(), school_id, user)
    owner = None if db.is_superadmin(user) else user["id"]
    with store.read() as conn:
        runs = _briefs(conn, store.list_runs(conn, school_id, owner))
    return {"open": [r for r in runs if r["open"]], "recent": [r for r in runs if not r["open"]]}


@router.post("/runs")
def create_run(item: schemas.MonitorRunCreate, user: User = Depends(require_teacher)):
    database = db.load_db()
    school = school_or_404(database, item.school_id, user)
    school_classes = {c["id"]: c for c in db.school_classes(database, school["id"])}
    if not school_classes:
        raise bad_request("В школе пока нет классов")
    unknown = [cid for cid in item.class_ids if cid not in school_classes]
    if unknown:
        raise not_found("Класс не найден")
    chosen = [school_classes[cid] for cid in dict.fromkeys(item.class_ids)] or list(school_classes.values())

    source = library.SETS_BY_KEY.get(item.set_key or "")
    if not source:
        raise bad_request("Выберите набор вопросов")
    goals = logic.clean_goals(item.goals)
    try:
        questions = logic.prepare_questions(library.set_questions(source["key"]), item.topic, goals)
    except ValueError as error:
        raise bad_request(str(error))

    t = store.now()
    fields = {
        "school_id": school["id"], "school_name": school["name"],
        "owner_id": user["id"], "owner_name": user.get("name") or user.get("login", ""),
        "form_id": None, "set_key": source["key"], "title": source["title"], "kind": "survey",
        "settings": {"after": "none", "show_names": False, "shuffle": False},
        "questions": questions, "topic": " ".join(item.topic.split()), "goals": goals,
        "all_classes": int(not item.class_ids), "lesson_date": db.today(),
        "code": f"{secrets.randbelow(10000):04d}" if item.use_code else None,
        "started_at": t, "closes_at": t + item.minutes * 60 if item.minutes else None, "closed_at": None,
    }
    with store.write() as conn:
        store.school_link(conn, school["id"], school["name"])
        busy = store.busy_classes(conn, school["id"])
        taken = [c["name"] for c in chosen if c["id"] in busy]
        if taken:
            raise bad_request(f"У класса {', '.join(taken)} уже идёт опрос — завершите его или выберите другой класс")
        run_id = store.create_run(conn, fields, [{"id": c["id"], "name": c["name"]} for c in chosen])
        run = store.get_run(conn, run_id)
        brief = _briefs(conn, [run])[0]
    return {"ok": True, "run": brief}


@router.get("/runs/{run_id}")
def live(run_id: int, user: User = Depends(require_teacher)):
    """Живой экран: участники в реальном времени. Гости помечены, повторы ФИО подсвечены."""
    with store.read() as conn:
        run = _run_or_404(conn, run_id, user)
        brief = _briefs(conn, [run])[0]
        people = store.participants(conn, run_id)
        answered = store.answer_counts(conn, run_id)
        code = store.code_of_school(conn, run["school_id"])
    seen: Dict[str, int] = {}
    for p in people:
        key = f"{p['class_id']}|{p['name_key']}"
        seen[key] = seen.get(key, 0) + 1
    total = len(run["questions"])
    brief["participants"] = [{
        "id": p["id"], "name": f"{p['last_name']} {p['first_name']}", "class_name": p["class_name"],
        "guest": p["student_id"] is None, "duplicate": seen[f"{p['class_id']}|{p['name_key']}"] > 1,
        "answered": min(total, answered.get(p["id"], 0)), "finished": p["finished_at"] is not None,
    } for p in people]
    brief["total_questions"] = total
    brief["link"] = {"path": f"/m/{code}", "url": config.site_url(f"/m/{code}")} if code else None
    return brief


@router.post("/runs/{run_id}/extend")
def extend(run_id: int, item: schemas.MonitorExtend, user: User = Depends(require_teacher)):
    """«+5 минут». Окно, которое уже истекло, открывается снова — если класс не занят другим опросом."""
    with store.write() as conn:
        run = _run_or_404(conn, run_id, user)
        if run["closed_at"] is not None:
            raise bad_request("Опрос завершён — запустите новый")
        if run["closes_at"] is None:
            return {"ok": True, "seconds_left": None}
        t = store.now()
        if run["closes_at"] <= t:
            busy = store.busy_classes(conn, run["school_id"], except_run=run_id)
            classes = store.run_classes(conn, [run_id])[run_id]
            if any(c["id"] in busy for c in classes):
                raise bad_request("У этих классов уже идёт другой опрос")
        closes = max(run["closes_at"], t) + item.minutes * 60
        conn.execute("UPDATE runs SET closes_at = ? WHERE id = ?", (closes, run_id))
    return {"ok": True, "seconds_left": closes - t}


@router.post("/runs/{run_id}/close")
def close(run_id: int, user: User = Depends(require_teacher)):
    with store.write() as conn:
        run = _run_or_404(conn, run_id, user)
        if store.closed_at(run) is None:
            conn.execute("UPDATE runs SET closed_at = ? WHERE id = ?", (store.now(), run_id))
    return {"ok": True}


@router.delete("/runs/{run_id}/participants/{participant_id}")
def remove_participant(run_id: int, participant_id: int, user: User = Depends(require_teacher)):
    """Убрать участника (случайный вход, шалость). Его ответы удаляются вместе с ним."""
    with store.write() as conn:
        _run_or_404(conn, run_id, user)
        gone = conn.execute("DELETE FROM participants WHERE id = ? AND run_id = ?", (participant_id, run_id)).rowcount
    if not gone:
        raise not_found("Участник не найден")
    return {"ok": True}


@router.get("/runs/{run_id}/results")
def results(run_id: int, user: User = Depends(require_teacher)):
    with store.read() as conn:
        run = _run_or_404(conn, run_id, user)
        brief = _briefs(conn, [run])[0]
        answers = store.run_answers(conn, run_id)
    brief["results"] = logic.summarize(run["questions"], answers)
    return brief
