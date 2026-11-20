"""Панель учителя: начисление звёзд за урок и списание при выдаче приза."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from .. import config, db, schemas
from ..security import require_teacher
from .common import bad_request, class_or_404, student_or_404

router = APIRouter(prefix="/api", tags=["lessons"], dependencies=[Depends(require_teacher)])


@router.post("/lessons/complete")
def complete_lesson(payload: schemas.LessonComplete):
    """Звёзды за урок: не больше банка урока на класс и лимита на одного ученика."""
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
        cls = class_or_404(database, payload.class_id)

        # Сначала проверяем всех, потом начисляем: урок либо проходит целиком, либо никак
        students = []
        for award in payload.awards:
            if award.stars <= 0:
                continue
            student = student_or_404(database, award.student_id)
            if student.get("class_id") != cls["id"]:
                raise bad_request(f"{student['name']} учится не в классе {cls['name']}")
            students.append((student, award.stars))

        for student, stars in students:
            student["total_stars"] += stars
            student["history"].insert(0, {
                "date": payload.date,
                "delta": stars,
                "reason": f"Урок ({payload.date})",
            })

        database["lessons"].insert(0, {
            "class_id": cls["id"],
            "class_name": cls["name"],       # для истории: класс могут переименовать
            "school_name": db.school_name_of_class(database, cls["id"]),
            "date": payload.date,
            "total_stars": total_stars,
            "students_rewarded": len(students),
        })
        return len(students)

    return {"ok": True, "students_rewarded": db.transaction(_apply)}


@router.post("/students/deduct")
def deduct_stars(payload: schemas.DeductRequest):
    """Списание при выдаче приза. Уйти в минус нельзя."""
    if payload.amount <= 0:
        raise bad_request("Количество списания должно быть больше 0")

    def _apply(database: Dict[str, Any]):
        student = student_or_404(database, payload.student_id)
        if student["total_stars"] < payload.amount:
            raise bad_request("Недостаточно звёзд на балансе ученика")
        student["total_stars"] -= payload.amount
        student["history"].insert(0, {
            "date": db.today(),
            "delta": -payload.amount,
            "reason": payload.reason.strip() or "Списание учителем",
        })
        return None

    db.transaction(_apply)
    return {"ok": True}
