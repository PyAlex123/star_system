"""API панели учителя: школы, классы, ученики, уроки, списание звёзд."""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from .. import config, db, passwords, schemas
from ..security import require_teacher

router = APIRouter(prefix="/api", tags=["teacher"], dependencies=[Depends(require_teacher)])


@router.get("/state")
def get_state():
    """Полный снимок базы — только для учителя."""
    return db.load_db()


# ── Школы ─────────────────────────────────────────────────────────────────────

@router.post("/schools")
def add_school(item: schemas.SchoolCreate):
    name = item.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        if any(s["name"].strip().lower() == name.lower() for s in database["schools"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Такая школа уже есть")
        school = {"id": db.next_id(database["schools"]), "name": name}
        database["schools"].append(school)
        return school

    return {"ok": True, "school": db.transaction(_apply)}


@router.patch("/schools/{school_id}")
def rename_school(school_id: int, item: schemas.SchoolUpdate):
    name = item.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название школы")

    def _apply(database: Dict[str, Any]):
        school = db.find_school(database, school_id)
        if not school:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
        school["name"] = name
        return school

    return {"ok": True, "school": db.transaction(_apply)}


@router.delete("/schools/{school_id}")
def delete_school(school_id: int):
    """Удаляет школу вместе со всеми её классами и учениками."""
    def _apply(database: Dict[str, Any]):
        if not db.find_school(database, school_id):
            return db.NO_SAVE
        if len(database["schools"]) == 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Нельзя удалить единственную школу")

        class_ids = {c["id"] for c in db.school_classes(database, school_id)}
        database["students"] = [s for s in database["students"]
                                if s.get("class_id") not in class_ids]
        database["classes"] = [c for c in database["classes"] if c["school_id"] != school_id]
        database["lessons"] = [l for l in database["lessons"]
                               if l.get("class_id") not in class_ids]
        database["schools"] = [s for s in database["schools"] if s["id"] != school_id]
        return None

    db.transaction(_apply)
    return {"ok": True}


# ── Классы ────────────────────────────────────────────────────────────────────

@router.post("/classes")
def add_class(item: schemas.ClassCreate):
    name = item.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название класса")

    def _apply(database: Dict[str, Any]):
        if not db.find_school(database, item.school_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
        if db.find_class_by_name(database, item.school_id, name):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "В этой школе такой класс уже есть")
        cls = {"id": db.next_id(database["classes"]), "school_id": item.school_id, "name": name}
        database["classes"].append(cls)
        return cls

    return {"ok": True, "class": db.transaction(_apply)}


@router.patch("/classes/{class_id}")
def update_class(class_id: int, item: schemas.ClassUpdate):
    def _apply(database: Dict[str, Any]):
        cls = db.find_class(database, class_id)
        if not cls:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Класс не найден")

        if item.school_id is not None:
            if not db.find_school(database, item.school_id):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
            cls["school_id"] = item.school_id

        if item.name is not None:
            name = item.name.strip()
            if not name:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Введите название класса")
            twin = db.find_class_by_name(database, cls["school_id"], name)
            if twin and twin["id"] != class_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "В этой школе такой класс уже есть")
            cls["name"] = name
        return cls

    return {"ok": True, "class": db.transaction(_apply)}


@router.delete("/classes/{class_id}")
def delete_class(class_id: int):
    def _apply(database: Dict[str, Any]):
        if not db.find_class(database, class_id):
            return db.NO_SAVE
        database["students"] = [s for s in database["students"]
                                if s.get("class_id") != class_id]
        database["classes"] = [c for c in database["classes"] if c["id"] != class_id]
        return None

    db.transaction(_apply)
    return {"ok": True}


# ── Ученики ───────────────────────────────────────────────────────────────────

def _make_student(database: Dict[str, Any], class_id: int, name: str, stars: int) -> Dict[str, Any]:
    tokens = {s.get("token") for s in database["students"]}
    student_id = db.next_student_id(database)
    student = {
        "id": student_id,
        "class_id": class_id,
        "name": name,
        "total_stars": max(0, stars),
        "history": [],
        "token": db.new_token(tokens),
        "tg_id": None,
        "tg_username": None,
        "bound_at": None,
        "avatar": db.default_avatar(student_id),
        "login": passwords.suggest_login(name, db.logins_taken(database)),
    }
    passwords.set_credentials(student)   # заполняет password_hash и password_shown
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
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Укажите фамилию и имя ученика")

    def _apply(database: Dict[str, Any]):
        if not db.find_class(database, item.class_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Класс не найден")
        student = _make_student(database, item.class_id, name, item.total_stars)
        database["students"].append(student)
        return student

    return {"ok": True, "student": db.transaction(_apply)}


@router.post("/students/bulk")
def bulk_import(items: List[schemas.BulkImportItem]):
    """Импорт из Excel/CSV. Классы, которых ещё нет, создаются в указанной школе."""
    def _apply(database: Dict[str, Any]):
        added = 0
        for item in items:
            name = item.name.strip()
            if not name:
                continue
            if not db.find_school(database, item.school_id):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
            cls = db.ensure_class(database, item.school_id, item.class_name.strip() or "Общий")
            student = _make_student(database, cls["id"], name, item.total_stars)
            if student["history"]:
                student["history"][0]["reason"] = "Импорт из таблицы"
            database["students"].append(student)
            added += 1
        return added

    added = db.transaction(_apply)
    return {"ok": True, "added": added}


@router.patch("/students/{student_id}")
def update_student(student_id: int, item: schemas.StudentUpdate):
    """Правка имени и логина. Звёзды и история не затрагиваются."""
    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")

        changed = False
        if item.name is not None:
            name = item.name.strip()
            if not name:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Имя не может быть пустым")
            student["name"] = name
            changed = True

        if item.login is not None:
            login = passwords.normalize_login(item.login)
            if not passwords.LOGIN_RE.match(login):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Логин: латинские буквы, цифры, точка, дефис; от 3 до 31 символа")
            if login in db.logins_taken(database, except_id=student_id):
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "Такой логин уже занят другим учеником")
            student["login"] = login
            changed = True

        return student if changed else db.NO_SAVE

    result = db.transaction(_apply)
    return {"ok": True, "student": None if result is db.NO_SAVE else result}


@router.post("/students/{student_id}/password")
def reset_password(student_id: int, item: schemas.PasswordReset):
    """Выдать ученику новый пароль. Пустое поле — система придумает сама.

    Пароль возвращается в открытом виде: учитель должен продиктовать его
    ребёнку и перепечатать карточку.
    """
    custom = (item.password or "").strip()
    if custom and len(custom) < 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Пароль слишком короткий — хотя бы 5 символов")
    if " " in custom:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "В пароле не должно быть пробелов")

    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        return passwords.set_credentials(student, custom or None)

    return {"ok": True, "password": db.transaction(_apply)}


@router.delete("/students/{student_id}")
def delete_student(student_id: int):
    def _apply(database: Dict[str, Any]):
        before = len(database["students"])
        database["students"] = [s for s in database["students"] if s["id"] != student_id]
        return db.NO_SAVE if len(database["students"]) == before else None

    db.transaction(_apply)
    return {"ok": True}


@router.post("/students/{student_id}/move")
def move_student(student_id: int, class_id: int):
    """Перевести ученика в другой класс — звёзды и история остаются при нём."""
    def _apply(database: Dict[str, Any]):
        student = db.find_student(database, student_id)
        if not student:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
        if not db.find_class(database, class_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Класс не найден")
        student["class_id"] = class_id
        return student

    return {"ok": True, "student": db.transaction(_apply)}


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
        cls = db.find_class(database, payload.class_id)
        if not cls:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Класс не найден")

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
            "class_id": cls["id"],
            "class_name": cls["name"],       # для истории: класс могут переименовать
            "school_name": db.school_name_of_class(database, cls["id"]),
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
