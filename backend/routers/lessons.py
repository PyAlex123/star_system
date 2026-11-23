"""Панель учителя: начисление звёзд за урок и списание при выдаче приза."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from .. import config, db, schemas
from ..security import require_teacher
from .common import bad_request, class_or_404, not_found, student_or_404

router = APIRouter(prefix="/api", tags=["lessons"], dependencies=[Depends(require_teacher)])


def lesson_reason(day: str) -> str:
    return f"Урок ({day})"


def class_stars_on(database: Dict[str, Any], class_id: int, day: str) -> int:
    """Сколько звёзд класс уже получил за уроки в этот день."""
    return sum(lesson.get("total_stars", 0) for lesson in database.get("lessons", [])
               if lesson.get("class_id") == class_id and lesson.get("date") == day)


def student_stars_on(student: Dict[str, Any], day: str) -> int:
    """Сколько звёзд ученик уже получил за уроки в этот день.

    Считаем по истории ученика, а не по записям уроков: уроки до обновления
    не хранят раскладку по ученикам, а история есть всегда."""
    reason = lesson_reason(day)
    return sum(entry.get("delta", 0) for entry in student.get("history", [])
               if entry.get("date") == day and entry.get("reason") == reason
               and entry.get("delta", 0) > 0)


@router.post("/lessons/complete")
def complete_lesson(payload: schemas.LessonComplete, user: Dict[str, Any] = Depends(require_teacher)):
    """Звёзды за урок. Лимиты считаются на дату, а не на нажатие кнопки:
    классу — не больше MAX_LESSON_BANK за день, ученику — не больше MAX_STUDENT_STARS.
    Иначе «Завершить урок» второй раз за ту же дату давал бы новый полный банк."""
    total_stars = sum(a.stars for a in payload.awards)
    if total_stars > config.MAX_LESSON_BANK:
        raise bad_request(f"Превышен лимит {config.MAX_LESSON_BANK} звёзд на урок")
    for award in payload.awards:
        if award.stars < 0 or award.stars > config.MAX_STUDENT_STARS:
            raise bad_request(f"Ученику можно поставить от 0 до {config.MAX_STUDENT_STARS} звёзд")

    # Один ученик дважды в списке — это обход лимита «не больше 5 за урок»
    ids = [a.student_id for a in payload.awards]
    if len(ids) != len(set(ids)):
        raise bad_request("Ученик указан в уроке дважды")

    def _apply(database: Dict[str, Any]):
        cls = class_or_404(database, payload.class_id, user)

        # Сначала проверяем всех, потом начисляем: урок либо проходит целиком, либо никак
        students = []
        for award in payload.awards:
            if award.stars <= 0:
                continue
            student = student_or_404(database, award.student_id)
            if student.get("class_id") != cls["id"]:
                raise bad_request(f"{student['name']} учится не в классе {cls['name']}")
            students.append((student, award.stars))

        day = payload.date
        already = class_stars_on(database, cls["id"], day)
        if total_stars and already + total_stars > config.MAX_LESSON_BANK:
            left = max(0, config.MAX_LESSON_BANK - already)
            raise bad_request(
                f"За {day} классу {cls['name']} уже начислено {already} из "
                f"{config.MAX_LESSON_BANK} звёзд. " +
                (f"Можно добавить ещё не больше {left}." if left else "Банк этого дня исчерпан."))
        for student, stars in students:
            got = student_stars_on(student, day)
            if got + stars > config.MAX_STUDENT_STARS:
                left = max(0, config.MAX_STUDENT_STARS - got)
                raise bad_request(
                    f"{student['name']} уже получил(а) за {day} {got} из "
                    f"{config.MAX_STUDENT_STARS} звёзд. " +
                    (f"Можно добавить ещё не больше {left}." if left else "Больше за этот день нельзя."))

        for student, stars in students:
            student["total_stars"] += stars
            student["history"].insert(0, {
                "date": day,
                "delta": stars,
                "reason": lesson_reason(day),
            })

        settings = database.setdefault("settings", {})
        settings["last_lesson_id"] = settings.get("last_lesson_id", 0) + 1
        database["lessons"].insert(0, {
            "id": settings["last_lesson_id"],
            "class_id": cls["id"],
            "user_id": user["id"],          # кто провёл — для статистики по учителям
            "class_name": cls["name"],       # для истории: класс могут переименовать
            "school_name": db.school_name_of_class(database, cls["id"]),
            "date": payload.date,
            "total_stars": total_stars,
            "students_rewarded": len(students),
            "awards": {str(student["id"]): stars for student, stars in students},
            "undone": {},
        })
        return len(students)

    return {"ok": True, "students_rewarded": db.transaction(_apply)}


@router.post("/students/deduct")
def deduct_stars(payload: schemas.DeductRequest, user: Dict[str, Any] = Depends(require_teacher)):
    """Списание при выдаче приза. Уйти в минус нельзя."""
    if payload.amount <= 0:
        raise bad_request("Количество списания должно быть больше 0")

    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, payload.student_id, user)
        if student["total_stars"] < payload.amount:
            raise bad_request("Недостаточно звёзд на балансе ученика")
        entry = {
            "date": db.today(),
            "delta": -payload.amount,
            "reason": payload.reason.strip() or "Списание учителем",
        }
        if payload.prize_id is not None:
            prize = next((p for p in database["prizes"] if p["id"] == payload.prize_id), None)
            if not prize:
                raise bad_request("Такого приза нет на витрине")
            entry["prize_id"] = prize["id"]
        student["total_stars"] -= payload.amount
        student["history"].insert(0, entry)
        return None

    db.transaction(_apply)
    return {"ok": True}


# ── Отмена ошибочного начисления ──────────────────────────────────────────────

def _lesson_or_404(database: Dict[str, Any], lesson_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    lesson = next((x for x in database.get("lessons", []) if x.get("id") == lesson_id), None)
    if not lesson:
        raise not_found("Урок не найден")
    class_or_404(database, lesson.get("class_id"), user)   # чужой урок — как несуществующий
    if "awards" not in lesson:
        raise bad_request("Этот урок проведён до обновления: в нём не записано, кому сколько "
                          "звёзд начислено. Отменить его нельзя.")
    lesson.setdefault("undone", {})
    return lesson


def _recount(lesson: Dict[str, Any]) -> None:
    lesson["total_stars"] = sum(lesson["awards"].values())
    lesson["students_rewarded"] = len(lesson["awards"])


def _drop_lesson_entry(student: Dict[str, Any], day: str, stars: int) -> None:
    """Убирает из истории ученика запись именно этого начисления."""
    reason = lesson_reason(day)
    history = student.get("history", [])
    for index, entry in enumerate(history):
        if entry.get("date") == day and entry.get("reason") == reason and entry.get("delta") == stars:
            del history[index]
            return


@router.post("/lessons/{lesson_id}/undo")
def undo_lesson(lesson_id: int, payload: schemas.LessonUndo,
                user: Dict[str, Any] = Depends(require_teacher)):
    """Отменить начисление одному ученику или всему уроку: звёзды списываются
    с баланса, запись пропадает из истории. Если звёзды уже потрачены на приз,
    отменять нельзя — баланс ушёл бы в минус."""
    def _apply(database: Dict[str, Any]):
        lesson = _lesson_or_404(database, lesson_id, user)
        keys = list(lesson["awards"]) if payload.student_id is None else [str(payload.student_id)]
        if not keys or any(k not in lesson["awards"] for k in keys):
            raise bad_request("Это начисление уже отменено")

        plan = []
        for key in keys:
            stars = lesson["awards"][key]
            student = db.find_student(database, int(key))
            if student and student["total_stars"] < stars:
                raise bad_request(
                    f"У ученика {student['name']} на балансе {student['total_stars']} ★ — "
                    f"часть звёзд уже потрачена на приз. Отменить начисление нельзя.")
            plan.append((key, stars, student))

        for key, stars, student in plan:
            if student:
                student["total_stars"] -= stars
                _drop_lesson_entry(student, lesson["date"], stars)
            lesson["undone"][key] = lesson["awards"].pop(key)
        _recount(lesson)
        return sum(stars for _, stars, _ in plan)

    return {"ok": True, "stars": db.transaction(_apply)}


@router.post("/lessons/{lesson_id}/restore")
def restore_lesson(lesson_id: int, payload: schemas.LessonUndo,
                   user: Dict[str, Any] = Depends(require_teacher)):
    """Вернуть отменённое начисление. Лимиты на дату те же, что при уроке."""
    def _apply(database: Dict[str, Any]):
        lesson = _lesson_or_404(database, lesson_id, user)
        keys = list(lesson["undone"]) if payload.student_id is None else [str(payload.student_id)]
        if not keys or any(k not in lesson["undone"] for k in keys):
            raise bad_request("Возвращать нечего")

        day = lesson["date"]
        adding = sum(lesson["undone"][k] for k in keys)
        already = class_stars_on(database, lesson["class_id"], day)
        if already + adding > config.MAX_LESSON_BANK:
            raise bad_request(f"За {day} классу уже начислено {already} из "
                              f"{config.MAX_LESSON_BANK} звёзд — вернуть нельзя.")
        plan = []
        for key in keys:
            stars = lesson["undone"][key]
            student = db.find_student(database, int(key))
            if not student:
                raise bad_request("Этого ученика больше нет в журнале")
            got = student_stars_on(student, day)
            if got + stars > config.MAX_STUDENT_STARS:
                raise bad_request(f"{student['name']} уже получил(а) за {day} {got} из "
                                  f"{config.MAX_STUDENT_STARS} звёзд — вернуть нельзя.")
            plan.append((key, stars, student))

        for key, stars, student in plan:
            student["total_stars"] += stars
            student["history"].insert(0, {"date": day, "delta": stars, "reason": lesson_reason(day)})
            lesson["awards"][key] = lesson["undone"].pop(key)
        _recount(lesson)
        return adding

    return {"ok": True, "stars": db.transaction(_apply)}
