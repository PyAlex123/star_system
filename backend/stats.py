"""Рейтинги, активность ученика и статистика школы — чистые функции над базой.

Место в рейтинге считается по звёздам, **заработанным за всё время**: текущий баланс
плюс всё, что ученик потратил на призы. Иначе ребёнок, забравший приз, «падал» бы
в рейтинге — и копить на приз становилось бы невыгодно.
"""
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from . import db

TOP_SIZE = 5
IDLE_LESSONS = 3          # столько уроков подряд без звёзд — «давно без звёзд»
WEEKS_STUDENT = 8
WEEKS_SCHOOL = 12

# Записи истории, которые не заработаны на уроке: стартовый баланс и импорт из таблицы
NOT_EARNED_REASONS = ("Стартовый баланс", "Импорт из таблицы")
_PARALLEL = re.compile(r"^\s*(\d{1,2})(?!\d)")


# ── Мелкие помощники ──────────────────────────────────────────────────────────

def parse_day(value: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def today() -> date:
    return parse_day(db.today())


def earned(student: Dict[str, Any]) -> int:
    """Заработано за всё время: баланс + всё списанное (призы и списания учителя)."""
    spent = sum(-int(h.get("delta", 0)) for h in student.get("history", []) if int(h.get("delta", 0)) < 0)
    return int(student.get("total_stars", 0)) + spent


def is_earning(entry: Dict[str, Any]) -> bool:
    """Начисление, заработанное ребёнком (а не стартовый баланс)."""
    return int(entry.get("delta", 0)) > 0 and entry.get("reason") not in NOT_EARNED_REASONS


def is_prize(entry: Dict[str, Any]) -> bool:
    return int(entry.get("delta", 0)) < 0 and (
        bool(entry.get("prize_id")) or str(entry.get("reason", "")).startswith("Приз"))


def parallel_of(class_name: str) -> Optional[int]:
    """«5-А», «5 Б», «10-Ф1» → 5, 5, 10. Кружок без номера параллели не имеет."""
    match = _PARALLEL.match(class_name or "")
    return int(match.group(1)) if match else None


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def school_of_class(database: Dict[str, Any], class_id: Optional[int]) -> Optional[int]:
    cls = db.find_class(database, class_id) if class_id else None
    return cls["school_id"] if cls else None


# ── Рейтинги: класс, параллель, школа ─────────────────────────────────────────

def ranking(students: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """По заработанным звёздам, при равенстве — по алфавиту. Скрытые не участвуют."""
    visible = [s for s in students if not s.get("rating_hidden")]
    return sorted(visible, key=lambda s: (-earned(s), s.get("name", "").lower()))


def scope_students(database: Dict[str, Any], student: Dict[str, Any], scope: str) -> Optional[List[Dict[str, Any]]]:
    """Ученики для рейтинга нужного уровня. None — уровня нет (например, кружок без параллели)."""
    cls = db.find_class(database, student.get("class_id"))
    if not cls:
        return None
    if scope == "class":
        return db.class_students(database, cls["id"])
    classes = db.school_classes(database, cls["school_id"])
    if scope == "parallel":
        grade = parallel_of(cls["name"])
        same = [c["id"] for c in classes if grade is not None and parallel_of(c["name"]) == grade]
        if len(same) < 2:
            return None
        return [s for s in database["students"] if s.get("class_id") in same]
    ids = {c["id"] for c in classes}
    if len(ids) < 2:
        return None
    return [s for s in database["students"] if s.get("class_id") in ids]


def leaderboard(database: Dict[str, Any], student: Dict[str, Any], students: List[Dict[str, Any]],
                card) -> Dict[str, Any]:
    """Топ-5, строка самого ученика, место и «сколько до следующего места».
    card(student, place) — как показать строку (кабинет решает, какие поля отдавать)."""
    ranked = ranking(students)
    my_index = next((i for i, s in enumerate(ranked) if s["id"] == student["id"]), None)
    top = [card(s, i + 1) for i, s in enumerate(ranked[:TOP_SIZE])]
    me_row = card(student, my_index + 1) if my_index is not None and my_index >= TOP_SIZE else None
    gap = next_name = None
    if my_index:
        ahead = ranked[my_index - 1]
        gap = max(1, earned(ahead) - earned(student) + 1)
        next_name = ahead["name"]
    return {
        "top": top,
        "me": me_row,
        "my_place": my_index + 1 if my_index is not None else None,
        "class_size": len(ranked),
        "gap_to_next": gap,
        "next_name": next_name,
    }


# ── Активность ученика ────────────────────────────────────────────────────────

def class_lessons(database: Dict[str, Any], class_id: Optional[int]) -> List[Dict[str, Any]]:
    """Уроки класса с раскладкой по ученикам — от новых к старым.
    Уроки до обновления (без раскладки) не знают, кому что досталось, — пропускаем."""
    lessons = [l for l in database.get("lessons", [])
               if l.get("class_id") == class_id and isinstance(l.get("awards"), dict)
               and (l["awards"] or l.get("undone"))]
    return sorted(lessons, key=lambda l: (str(l.get("date", "")), int(l.get("id") or 0)), reverse=True)


def student_activity(database: Dict[str, Any], student: Dict[str, Any], now: Optional[date] = None) -> Dict[str, Any]:
    now = now or today()
    history = student.get("history", [])
    gains = [(parse_day(h.get("date")), int(h["delta"])) for h in history if is_earning(h)]
    gains = [(d, v) for d, v in gains if d]

    def stars_since(days: int) -> int:
        start = now - timedelta(days=days - 1)
        return sum(v for d, v in gains if start <= d <= now)

    this_week = week_start(now)
    weeks = []
    for k in range(WEEKS_STUDENT - 1, -1, -1):
        start = this_week - timedelta(weeks=k)
        end = start + timedelta(days=6)
        weeks.append({"week": start.isoformat(), "stars": sum(v for d, v in gains if start <= d <= end)})

    lessons = class_lessons(database, student.get("class_id"))
    key = str(student["id"])
    got = [key in l.get("awards", {}) for l in lessons]
    streak = 0
    for hit in got:
        if not hit:
            break
        streak += 1
    idle = 0
    for hit in got:
        if hit:
            break
        idle += 1
    month_ago = now - timedelta(days=29)
    recent = [l for l in lessons if (parse_day(l.get("date")) or now) >= month_ago]
    recent_hit = sum(1 for l in recent if key in l.get("awards", {}))

    last_star = max((d for d, _ in gains), default=None)
    return {
        "week": stars_since(7),
        "month": stars_since(30),
        "earned": earned(student),
        "weeks": weeks,
        "streak": streak,
        "idle_lessons": idle,
        "idle": idle >= IDLE_LESSONS,
        "lessons_30": len(recent),
        "lessons_30_with_stars": recent_hit,
        "last_star": last_star.isoformat() if last_star else None,
        "last_seen": student.get("last_seen"),
        "prizes": sum(1 for h in history if is_prize(h)),
    }


# ── Расписание ────────────────────────────────────────────────────────────────

def class_schedule(database: Dict[str, Any], class_id: Optional[int]) -> List[Dict[str, Any]]:
    """Занятия класса по дням недели и времени, с именем учителя."""
    names = {u["id"]: u.get("name", "") for u in database.get("users", [])}
    items = [dict(e, teacher=names.get(e.get("user_id"), "")) for e in database.get("schedule", [])
             if e.get("class_id") == class_id]
    return sorted(items, key=lambda e: (e["weekday"], e["start"]))


def planned_lessons(database: Dict[str, Any], class_ids: Iterable[int], start: Optional[date], end: date) -> int:
    """Сколько занятий было по расписанию с start по end включительно.
    Занятие считается с того дня, как его внесли в расписание."""
    ids = set(class_ids)
    total = 0
    for entry in database.get("schedule", []):
        if entry.get("class_id") not in ids:
            continue
        created = parse_day(entry.get("created_at")) or end
        first = max(created, start) if start else created
        if first > end:
            continue
        # дней с нужным днём недели в отрезке [first, end]
        offset = (entry["weekday"] - 1 - first.weekday()) % 7
        first_hit = first + timedelta(days=offset)
        if first_hit <= end:
            total += (end - first_hit).days // 7 + 1
    return total


# ── Статистика школы ──────────────────────────────────────────────────────────

def school_stats(database: Dict[str, Any], school_id: int, days: Optional[int]) -> Dict[str, Any]:
    now = today()
    start = now - timedelta(days=days - 1) if days else None
    classes = sorted(db.school_classes(database, school_id),
                     key=lambda c: (parallel_of(c["name"]) or 99, c["name"]))
    class_ids = {c["id"] for c in classes}
    students = [s for s in database["students"] if s.get("class_id") in class_ids]

    def in_period(value: Any) -> bool:
        d = parse_day(value)
        return bool(d) and (start is None or d >= start) and d <= now

    awarded = spent = prizes = 0
    active_ids = set()
    per_class: Dict[int, Dict[str, int]] = {c["id"]: {"stars": 0, "active": 0} for c in classes}
    for s in students:
        got_here = 0
        for h in s.get("history", []):
            if not in_period(h.get("date")):
                continue
            if is_earning(h):
                awarded += int(h["delta"])
                got_here += int(h["delta"])
            elif int(h.get("delta", 0)) < 0:
                spent -= int(h["delta"])
                prizes += 1 if is_prize(h) else 0
        if got_here:
            active_ids.add(s["id"])
            per_class[s["class_id"]]["stars"] += got_here
            per_class[s["class_id"]]["active"] += 1

    lessons = [l for l in database.get("lessons", []) if l.get("class_id") in class_ids and in_period(l.get("date"))]
    lessons_per_class: Dict[int, int] = {}
    for l in lessons:
        lessons_per_class[l["class_id"]] = lessons_per_class.get(l["class_id"], 0) + 1

    # Звёзды по неделям — 12 последних недель, независимо от выбранного периода
    this_week = week_start(now)
    weeks = []
    for k in range(WEEKS_SCHOOL - 1, -1, -1):
        w0 = this_week - timedelta(weeks=k)
        w1 = w0 + timedelta(days=6)
        total = 0
        for s in students:
            for h in s.get("history", []):
                d = parse_day(h.get("date"))
                if d and w0 <= d <= w1 and is_earning(h):
                    total += int(h["delta"])
        weeks.append({"week": w0.isoformat(), "stars": total})

    seen = sum(1 for s in students if in_period(s.get("last_seen")))

    class_rows = []
    for c in classes:
        kids = [s for s in students if s.get("class_id") == c["id"]]
        stars = per_class[c["id"]]["stars"]
        class_rows.append({
            "id": c["id"], "name": c["name"], "parallel": parallel_of(c["name"]),
            "students": len(kids), "active": per_class[c["id"]]["active"], "stars": stars,
            "avg": round(stars / len(kids), 1) if kids else 0,
            "lessons": lessons_per_class.get(c["id"], 0),
            "planned": planned_lessons(database, [c["id"]], start, now),
        })

    class_names = {c["id"]: c["name"] for c in classes}
    student_rows = []
    for s in students:
        act = student_activity(database, s, now)
        period = sum(int(h["delta"]) for h in s.get("history", []) if is_earning(h) and in_period(h.get("date")))
        student_rows.append({
            "id": s["id"], "name": s["name"], "class_id": s["class_id"],
            "class_name": class_names.get(s["class_id"], ""), "parallel": parallel_of(class_names.get(s["class_id"], "")),
            "earned": earned(s), "period": period, "balance": s.get("total_stars", 0),
            "hidden": bool(s.get("rating_hidden")), "idle": act["idle"], "idle_lessons": act["idle_lessons"],
            "last_star": act["last_star"], "last_seen": s.get("last_seen"),
            "avatar": s.get("avatar"),
        })

    names = {u["id"]: u.get("name", "") for u in database.get("users", [])}
    teachers: Dict[Any, Dict[str, Any]] = {}
    for l in lessons:
        key = l.get("user_id")
        row = teachers.setdefault(key, {"user_id": key, "name": names.get(key, "") if key else "", "lessons": 0, "stars": 0})
        row["lessons"] += 1
        row["stars"] += int(l.get("total_stars", 0))

    return {
        "school_id": school_id,
        "days": days,
        "from": start.isoformat() if start else None,
        "to": now.isoformat(),
        "totals": {
            "students": len(students), "active": len(active_ids), "awarded": awarded, "spent": spent,
            "prizes": prizes, "lessons": len(lessons),
            "planned": planned_lessons(database, class_ids, start, now), "seen": seen,
        },
        "weeks": weeks,
        "classes": class_rows,
        "students": student_rows,
        "teachers": sorted(teachers.values(), key=lambda t: -t["lessons"]),
    }
