"""Общие помощники роутеров панели учителя."""
from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException, status

from .. import db, images


def not_found(message: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, message)


def bad_request(message: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def student_or_404(database: Dict[str, Any], student_id: int) -> Dict[str, Any]:
    student = db.find_student(database, student_id)
    if not student:
        raise not_found("Ученик не найден")
    return student


def class_or_404(database: Dict[str, Any], class_id: int) -> Dict[str, Any]:
    cls = db.find_class(database, class_id)
    if not cls:
        raise not_found("Класс не найден")
    return cls


def school_or_404(database: Dict[str, Any], school_id: int) -> Dict[str, Any]:
    school = db.find_school(database, school_id)
    if not school:
        raise not_found("Школа не найдена")
    return school


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
