"""Общие помощники роутеров панели учителя."""
from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException, status

from .. import db, images


def not_found(message: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, message)


def bad_request(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


# Область видимости. user=None — проверка без учёта прав (бот, служебный код).
# Учителю чужая школа отвечает тем же 404, что и несуществующая: так по ответам
# нельзя понять, какие ещё школы и ученики есть в системе.

User = Optional[Dict[str, Any]]

# need — какая должность нужна в школе: TEACH — журнал, звёзды, ученики, опросы
# (только учитель этой школы); None — любая должность в ней (расписание, карточки, тема).
# Своя школа без нужной должности — 403 с объяснением, чужая — 404.
TEACH = "teach"
NO_TEACH_TEXT = "В этой школе вы не ведёте уроки — журнал и звёзды здесь недоступны"


def _school_visible(database: Dict[str, Any], user: User, school_id: Optional[int]) -> bool:
    if user is None or db.is_superadmin(user):
        return True
    return school_id in db.allowed_school_ids(database, user)


def _check_need(user: User, school_id: Optional[int], need: Optional[str]) -> None:
    if need == TEACH and user is not None and not db.teaches_in(user, school_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NO_TEACH_TEXT)


def school_or_404(database: Dict[str, Any], school_id: int, user: User = None,
                  need: Optional[str] = None) -> Dict[str, Any]:
    school = db.find_school(database, school_id)
    if not school or not _school_visible(database, user, school_id):
        raise not_found("Школа не найдена")
    _check_need(user, school_id, need)
    return school


def class_or_404(database: Dict[str, Any], class_id: int, user: User = None,
                 need: Optional[str] = None) -> Dict[str, Any]:
    cls = db.find_class(database, class_id)
    if not cls or not _school_visible(database, user, cls["school_id"]):
        raise not_found("Класс не найден")
    _check_need(user, cls["school_id"], need)
    return cls


def student_or_404(database: Dict[str, Any], student_id: int, user: User = None,
                   need: Optional[str] = None) -> Dict[str, Any]:
    student = db.find_student(database, student_id)
    cls = None
    if student and user is not None and not db.is_superadmin(user):
        cls = db.find_class(database, student.get("class_id"))
        if not cls or not _school_visible(database, user, cls["school_id"]):
            student = None
    if not student:
        raise not_found("Ученик не найден")
    if cls is not None:
        _check_need(user, cls["school_id"], need)
    return student


NO_REWARDS_TEXT = ("В этой школе система наград выключена — звёзд и призов здесь нет. "
                   "Включить её может администратор: «Школы» → школа → «Система наград».")


def rewards_or_400(database: Dict[str, Any], school_id: Optional[int]) -> None:
    """Звёзды, призы, карточки с QR и кабинет ученика — только в школе с системой наград."""
    if not db.rewards_on(db.find_school(database, school_id)):
        raise bad_request(NO_REWARDS_TEXT)


def clean_name(value: Optional[str], message: str) -> str:
    """Название без пробелов по краям; пустое — ошибка с понятным текстом."""
    name = (value or "").strip()
    if not name:
        raise bad_request(message)
    return name


def drop_photos(urls: Optional[Iterable[Optional[str]]]) -> None:
    """Удаляет загруженные фото учеников, которых больше нет.

    Вызывается после записи базы: если запись не удалась, файлы остаются на месте.
    """
    for url in urls or []:
        images.drop_upload(url)
