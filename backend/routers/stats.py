"""Панель, только чтение: снимок журнала, статистика школы, активность ученика.

Здесь — всё, что можно показывать любой роли панели, включая администрацию школы
(директор, завуч). Маршруты, которые меняют данные, живут в других роутерах на
require_teacher и администрации школы недоступны.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from .. import db, stats
from ..security import require_panel
from .common import bad_request, school_or_404, student_or_404

router = APIRouter(prefix="/api", tags=["stats"], dependencies=[Depends(require_panel)])

PERIODS = (7, 30, 90)


@router.get("/state")
def get_state(user: Dict[str, Any] = Depends(require_panel)):
    """Снимок базы для панели: только видимые пользователю школы.
    Хеши паролей и ключи сессий не покидают сервер; администрации школы не отдаются
    и данные для входа учеников."""
    return db.public_state(db.load_db(), user)


@router.get("/stats")
def school_stats(school_id: int, days: Optional[int] = None,
                 user: Dict[str, Any] = Depends(require_panel)):
    """Статистика школы за 7, 30, 90 дней или за всё время (days не указан)."""
    if days is not None and days not in PERIODS:
        raise bad_request("Период — 7, 30 или 90 дней")
    database = db.load_db()
    school_or_404(database, school_id, user)
    return stats.school_stats(database, school_id, days)


@router.get("/stats/student/{student_id}")
def student_stats(student_id: int, user: Dict[str, Any] = Depends(require_panel)):
    """Активность одного ученика: звёзды по неделям, серия, давно ли без звёзд."""
    database = db.load_db()
    student = student_or_404(database, student_id, user)
    history = student.get("history", [])[:30]
    return {
        "student": {"id": student["id"], "name": student["name"], "class_id": student.get("class_id"),
                    "class_name": db.class_name(database, student.get("class_id")),
                    "total_stars": student.get("total_stars", 0), "avatar": student.get("avatar")},
        "activity": stats.student_activity(database, student),
        "history": history,
    }
