"""API панели учителя: классы, ученики, уроки, списание звёзд."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from .. import config, db, schemas
from ..security import require_teacher

router = APIRouter(prefix="/api", tags=["teacher"], dependencies=[Depends(require_teacher)])


@router.get("/state")
def get_state():
    """Полный снимок базы — только для учителя."""
    return db.load_db()


# ── Классы ────────────────────────────────────────────────────────────────────

@router.post("/classes")
def add_class(item: schemas.ClassCreate):
    name = item.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустое название класса")

    def _apply(database: Dict[str, Any]):
        if name in database["classes"]:
            return db.NO_SAVE
        database["classes"].append(name)
        return None

    db.transaction(_apply)
    return {"ok": True, "classes": db.load_db()["classes"]}


@router.delete("/classes/{class_name}")
def delete_class(class_name: str):
    def _apply(database: Dict[str, Any]):
        if class_name not in database["classes"]:
            return db.NO_SAVE
        database["classes"].remove(class_name)
        database["students"] = [s for s in database["students"] if s["class_name"] != class_name]
        return None

    db.transaction(_apply)
    return {"ok": True}


# ── Ученики ───────────────────────────────────────────────────────────────────

def _make_student(database: Dict[str, Any], class_name: str, name: str, stars: int) -> Dict[str, Any]:
    tokens = {s.get("token") for s in database["students"]}
    student_id = db.next_student_id(database)
    student = {
        "id": student_id,
        "class_name": class_name,
        "name": name,
        "total_stars": max(0, stars),
        "history": [],
        "token": db.new_token(tokens),
        "tg_id": None,
        "tg_username": None,
        "bound_at": None,
        "avatar": db.default_avatar(student_id),
    }
    if student["total_stars"] > 0:
        student["history"].append({
            "date": db.today(),
            "delta": student["total_stars"],
            "reason": "Стартовый баланс",
        })
    return student


@router.post("/students")
def add_student(item: schemas.StudentCreate):
    name = item.name.strip()
    class_name = item.class_name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите фамилию и имя ученика")

    def _apply(database: Dict[str, Any]):
        if class_name not in database["classes"]:
            database["classes"].append(class_name)
        student = _make_student(database, class_name, name, item.total_stars)
        database["students"].append(student)
        return student

    student = db.transaction(_apply)
    return {"ok": True, "student": student}


@router.post("/students/bulk")
def bulk_import(items: List[schemas.BulkImportItem]):
    def _apply(database: Dict[str, Any]):
        added = 0
        for item in items:
            class_name = item.class_name.strip() or "Общий"
            name = item.name.strip()
            if not name:
                continue
            if class_name not in database["classes"]:
                database["classes"].append(class_name)
            student = _make_student(database, class_name, name, item.total_stars)
            if student["history"]:
                student["history"][0]["reason"] = "Импорт из таблицы"
            database["students"].append(student)
            added += 1
        return added

    added = db.transaction(_apply)
    return {"ok": True, "added": added}


@router.delete("/students/{student_id}")
def delete_student(student_id: int):
    def _apply(database: Dict[str, Any]):
        before = len(database["students"])
        database["students"] = [s for s in database["students"] if s["id"] != student_id]
        return db.NO_SAVE if len(database["students"]) == before else None

    db.transaction(_apply)
    return {"ok": True}


@router.post("/students/{student_id}/reset-token")
def reset_token(student_id: int):
    """Новый QR-код: старая карточка перестаёт работать, Telegram отвязывается."""
    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        tokens = {s.get("token") for s in database["students"] if s["id"] != student_id}
        student["token"] = db.new_token(tokens)
        student["tg_id"] = None
        student["tg_username"] = None
        student["bound_at"] = None
        return student["token"]

    token = db.transaction(_apply)
    return {"ok": True, "token": token}


@router.post("/students/{student_id}/unbind")
def unbind_telegram(student_id: int):
    """Отвязать Telegram-аккаунт, код карточки остаётся прежним."""
    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        if student.get("tg_id") is None:
            return db.NO_SAVE
        student["tg_id"] = None
        student["tg_username"] = None
        student["bound_at"] = None
        return None

    db.transaction(_apply)
    return {"ok": True}


@router.post("/students/{student_id}/reset-avatar")
def reset_avatar(student_id: int):
    """Сбросить аватар на стандартный — если ребёнок поставил неуместное фото."""
    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        student["avatar"] = db.default_avatar(student_id)
        return student["avatar"]

    avatar = db.transaction(_apply)
    return {"ok": True, "avatar": avatar}


# ── Уроки и звёзды ────────────────────────────────────────────────────────────

@router.post("/lessons/complete")
def complete_lesson(payload: schemas.LessonComplete):
    total_stars = sum(a.stars for a in payload.awards)
    if total_stars > config.MAX_LESSON_BANK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Превышен лимит {config.MAX_LESSON_BANK} звёзд на урок")
    for award in payload.awards:
        if award.stars < 0 or award.stars > config.MAX_STUDENT_STARS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Ученику можно поставить от 0 до {config.MAX_STUDENT_STARS} звёзд")

    def _apply(database: Dict[str, Any]):
        awarded = 0
        for award in payload.awards:
            if award.stars <= 0:
                continue
            student = db.find_student(database, award.student_id)
            if not student:
                continue
            student["total_stars"] += award.stars
            student["history"].insert(0, {
                "date": payload.date,
                "delta": award.stars,
                "reason": f"Урок ({payload.date})",
            })
            awarded += 1

        database["lessons"].insert(0, {
            "class_name": payload.class_name,
            "date": payload.date,
            "total_stars": total_stars,
            "students_rewarded": awarded,
        })
        return awarded

    awarded = db.transaction(_apply)
    return {"ok": True, "students_rewarded": awarded}


@router.post("/students/deduct")
def deduct_stars(payload: schemas.DeductRequest):
    if payload.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Количество списания должно быть больше 0")

    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, payload.student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        if student["total_stars"] < payload.amount:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недостаточно звёзд на балансе ученика")
        student["total_stars"] -= payload.amount
        student["history"].insert(0, {
            "date": db.today(),
            "delta": -payload.amount,
            "reason": payload.reason.strip() or "Списание учителем",
        })
        return None

    db.transaction(_apply)
    return {"ok": True}
