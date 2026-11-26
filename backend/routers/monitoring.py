"""«Мониторинг» в панели: наборы вопросов, ссылка школы, запуск, живой экран, итоги.

Весь роутер — require_teacher: администрация школы раздел не видит (решение
этапа 1; роль завуча в мониторинге — этап 3 ТЗ). Запуск и его ответы видят
автор и администратор; чужое отвечает 404, как несуществующее.
"""
import base64
import html
import secrets
import urllib.parse
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from .. import assets, config, db, excel, schemas, themes
from ..monitoring import library, logic, store
from ..security import panel_user, require_editor_page, require_teacher
from .cards import FAVICON, ICONS_FILE, _qr_png
from .common import bad_request, not_found, school_or_404

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])
pages = APIRouter(tags=["monitoring"])        # печатный лист — страница, а не API

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
        "id": run["id"], "title": run["title"], "set_key": run["set_key"], "form_id": run["form_id"], "kind": run["kind"],
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


# ── Свои формы ────────────────────────────────────────────────────────────────

def _form_or_404(conn, form_id: int, user: User) -> Dict[str, Any]:
    form = store.get_form(conn, form_id)
    if not form or not _visible(user, form, db.load_db()):
        raise not_found("Форма не найдена")
    return form


def _form_brief(form: Dict[str, Any]) -> Dict[str, Any]:
    qs = form["questions"]
    return {"id": form["id"], "title": form["title"], "kind": form["kind"], "questions": len(qs),
            "updated_at": form["updated_at"], "uses_topic": library.uses(qs, library.TOPIC),
            "uses_goals": library.uses(qs, library.GOAL), "school_id": form["school_id"]}


@router.get("/forms")
def list_forms(school_id: int, user: User = Depends(require_teacher)):
    """«Мои формы» школы; администратору — формы всех учителей школы."""
    school_or_404(db.load_db(), school_id, user)
    with store.read() as conn:
        forms = store.list_forms(conn, school_id, None if db.is_superadmin(user) else user["id"])
    return {"forms": [_form_brief(f) for f in forms]}


@router.post("/forms")
def create_form(item: schemas.MonitorFormCreate, user: User = Depends(require_teacher)):
    """Новая форма: пустая, копия готового набора или копия своей формы."""
    school_or_404(db.load_db(), item.school_id, user)
    with store.write() as conn:
        if item.from_form is not None:
            source = _form_or_404(conn, item.from_form, user)
            raw = {"title": item.title or f"{source['title']} (копия)", "kind": source["kind"],
                   "settings": source["settings"], "questions": source["questions"]}
        elif item.from_set:
            found = library.SETS_BY_KEY.get(item.from_set)
            if not found:
                raise not_found("Набор не найден")
            raw = {"title": item.title or found["title"], "kind": "survey",
                   "questions": [dict(q, std=q["std"]) for q in library.set_questions(found["key"])]}
        else:
            raw = {"title": item.title or "Новая форма", "questions": []}
        form = logic.clean_form(raw)
        form_id = store.save_form(conn, form, owner_id=user["id"], school_id=item.school_id)
        saved = store.get_form(conn, form_id)
    return {"ok": True, "form": saved}


@router.get("/forms/{form_id}")
def get_form(form_id: int, user: User = Depends(require_teacher)):
    with store.read() as conn:
        return _form_or_404(conn, form_id, user)


@router.put("/forms/{form_id}")
def save_form(form_id: int, item: schemas.MonitorFormSave, user: User = Depends(require_teacher)):
    """Сохранить форму из конструктора (автосохранение). Прошлые запуски не меняются:
    у них свой снимок вопросов."""
    try:
        form = logic.clean_form(item.model_dump())
    except ValueError as error:
        raise bad_request(str(error))
    with store.write() as conn:
        _form_or_404(conn, form_id, user)
        store.save_form(conn, form, form_id=form_id)
        saved = store.get_form(conn, form_id)
    return {"ok": True, "form": saved}


@router.delete("/forms/{form_id}")
def delete_form(form_id: int, user: User = Depends(require_teacher)):
    """Удалить форму. Проведённые по ней опросы и ответы остаются."""
    with store.write() as conn:
        _form_or_404(conn, form_id, user)
        conn.execute("DELETE FROM forms WHERE id = ?", (form_id,))
    return {"ok": True}


# ── Запуски ───────────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(school_id: int, limit: int = 30, class_id: Optional[int] = None,
              user: User = Depends(require_teacher)):
    """Свои запуски (администратору — все), новые сверху. class_id — только опросы этого класса."""
    school_or_404(db.load_db(), school_id, user)
    owner = None if db.is_superadmin(user) else user["id"]
    with store.read() as conn:
        runs = _briefs(conn, store.list_runs(conn, school_id, owner, max(1, min(200, limit)), class_id))
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

    goals = logic.clean_goals(item.goals)
    if item.form_id is not None:
        with store.read() as conn:
            form = _form_or_404(conn, item.form_id, user)
        source = {"key": None, "title": form["title"], "kind": form["kind"], "settings": form["settings"],
                  "questions": form["questions"], "form_id": form["id"]}
    elif item.set_key in library.SETS_BY_KEY:
        found = library.SETS_BY_KEY[item.set_key]
        source = {"key": found["key"], "title": found["title"], "kind": "survey",
                  "settings": {"after": "none", "show_names": False, "shuffle": False},
                  "questions": library.set_questions(found["key"]), "form_id": None}
    else:
        raise bad_request("Выберите набор вопросов")
    try:
        questions = logic.prepare_questions(source["questions"], item.topic, goals)
    except ValueError as error:
        raise bad_request(str(error))

    t = store.now()
    fields = {
        "school_id": school["id"], "school_name": school["name"],
        "owner_id": user["id"], "owner_name": user.get("name") or user.get("login", ""),
        "form_id": source["form_id"], "set_key": source["key"], "title": source["title"], "kind": source["kind"],
        "settings": source["settings"],
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


def _report(conn, run: Dict[str, Any]) -> Dict[str, Any]:
    """Итоги запуска: сводка по вопросам и таблица «участник × вопрос»."""
    brief = _briefs(conn, [run])[0]
    answers = store.run_answers(conn, run["id"])
    people = [{"id": p["id"], "name": f"{p['last_name']} {p['first_name']}", "class_name": p["class_name"],
               "guest": p["student_id"] is None, "finished": p["finished_at"] is not None, "score": p["score"]}
              for p in store.participants(conn, run["id"])]
    brief["results"] = logic.summarize(run["questions"], answers)
    brief["columns"] = [{"id": q["id"], "std": q.get("std"), "type": q["type"], "text": q["text"],
                         "test": bool(q.get("correct"))} for q in run["questions"]]
    brief["rows"] = logic.answer_table(run["questions"], people, answers, bool(run["settings"].get("show_names")))
    brief["is_test"] = any(q.get("correct") for q in run["questions"])
    brief["max_score"] = sum(float(q.get("points") or 1) for q in run["questions"] if q.get("correct"))
    return brief


@router.get("/runs/{run_id}/results")
def results(run_id: int, user: User = Depends(require_teacher)):
    with store.read() as conn:
        return _report(conn, _run_or_404(conn, run_id, user))


@router.get("/runs/{run_id}/export.xlsx")
def export(run_id: int, user: User = Depends(require_teacher)):
    """Excel: лист «Сводка» и лист «Ответы» (участник × вопрос)."""
    with store.read() as conn:
        report = _report(conn, _run_or_404(conn, run_id, user))
    data = excel.build_monitoring_export(report)
    name = f"{report['title']} {report['lesson_date']}.xlsx"
    return Response(data, media_type=excel.XLSX_TYPE, headers={
        "Content-Disposition": f"attachment; filename=\"monitoring.xlsx\"; filename*=UTF-8''{urllib.parse.quote(name)}",
        "Cache-Control": "no-store",
    })


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, user: User = Depends(require_teacher)):
    """Удалить запуск вместе со всеми ответами. Окно в панели называет, сколько ответов пропадёт."""
    with store.write() as conn:
        _run_or_404(conn, run_id, user)
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    return {"ok": True}


# ── Печатный лист с QR для кабинета ───────────────────────────────────────────

@pages.get("/monitoring/poster/{school_id}", response_class=HTMLResponse, include_in_schema=False,
            dependencies=[Depends(require_editor_page)])
def poster(school_id: int, request: Request):
    """Лист А4: QR и адрес ссылки школы — повесить в кабинете. Ссылка постоянная."""
    database = db.load_db()
    school = school_or_404(database, school_id, panel_user(request))
    url = config.site_url(f"/m/{_link(school)}")
    qr = base64.b64encode(_qr_png(url, box_size=16)).decode()
    shown = html.escape(url.replace("https://", "").replace("http://", ""))
    name = html.escape(school["name"])
    base = config.URL_PREFIX
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru" {themes.html_attrs(themes.school_theme(school), themes.page_mode(request))}>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Опрос на уроке — {name}</title>
{FAVICON}
<link rel="stylesheet" href="{base}{assets.versioned('css/tokens.css')}">
<link rel="stylesheet" href="{base}{assets.versioned('css/theme.css')}">
<link rel="stylesheet" href="{base}{assets.versioned('css/cards.css')}">
</head>
<body class="print-page">
{ICONS_FILE.read_text(encoding="utf-8")}
<div class="ptoolbar no-print">
  <div class="ptoolbar-in">
    <div class="ptitle">
      <span class="ptitle-main">Лист с QR для кабинета</span>
      <span class="ptitle-sub">{name} · 1 лист А4 · ссылка постоянная</span>
    </div>
    <a class="btn btn-ghost btn-md" href="{base}/teacher"><svg class="ic ic-sm"><use href="#i-arrow-left"></use></svg>В журнал</a>
    <button type="button" class="btn btn-md" onclick="window.print()"><svg class="ic ic-sm"><use href="#i-printer"></use></svg>Печать</button>
  </div>
</div>
<div class="sheets" data-light>
  <section class="a4 m-poster">
    <p class="m-poster-school">{name}</p>
    <h1>Опрос на уроке</h1>
    <p class="m-poster-lead">Наведи камеру телефона на QR-код</p>
    <img class="m-poster-qr" src="data:image/png;base64,{qr}" alt="QR-код ссылки {shown}">
    <p class="m-poster-url">{shown}</p>
    <ol class="m-poster-steps">
      <li>Открой ссылку — вход и пароль не нужны.</li>
      <li>Подожди, пока учитель начнёт: вопросы откроются сами.</li>
      <li>Ответь на вопросы — это минута-две.</li>
    </ol>
  </section>
</div>
</body>
</html>""", headers={"Cache-Control": "no-store"})
