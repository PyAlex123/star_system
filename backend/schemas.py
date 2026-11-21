"""Модели запросов и ответов.

Ограничения длины здесь не для красоты: без них в базу можно записать
мегабайтное имя, и оно разъедется по рейтингу, карточкам и выгрузке.
"""
from datetime import date
from typing import Annotated, List, Optional

from pydantic import BaseModel, Field, field_validator

MAX_NAME = 120          # ФИО, название школы, класса, приза
MAX_TEXT = 300          # описание приза, причина списания
MAX_IMPORT_ROWS = 500   # строк за один импорт из Excel

Name = Annotated[str, Field(max_length=MAX_NAME)]
Text = Annotated[str, Field(max_length=MAX_TEXT)]
Secret = Annotated[str, Field(max_length=128)]


class SchoolCreate(BaseModel):
    name: Name


class SchoolUpdate(BaseModel):
    name: Name


class ClassCreate(BaseModel):
    school_id: int
    name: Name


class ClassBulkCreate(BaseModel):
    """Несколько классов сразу — из сетки 1–11 × А–Д."""
    school_id: int
    names: List[Name] = Field(max_length=100)


class ClassUpdate(BaseModel):
    name: Optional[Name] = None
    school_id: Optional[int] = None


class StudentCreate(BaseModel):
    class_id: int
    name: Name
    total_stars: int = Field(default=0, ge=0, le=100_000)


class BulkImportItem(BaseModel):
    """Строка из Excel. Класс приходит названием — его создаём в указанной школе."""
    school_id: int
    class_name: Name
    name: Name
    total_stars: int = Field(default=0, ge=0, le=100_000)


class LessonAward(BaseModel):
    student_id: int
    stars: int


class LessonComplete(BaseModel):
    class_id: int
    date: str
    awards: List[LessonAward] = Field(max_length=200)

    @field_validator("date")
    @classmethod
    def real_date(cls, value: str) -> str:
        """Дата урока — настоящая дата в виде ГГГГ-ММ-ДД, как её отдаёт поле <input type=date>."""
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except (ValueError, AttributeError):
            raise ValueError("Дата урока должна быть в формате ГГГГ-ММ-ДД")


class DeductRequest(BaseModel):
    student_id: int
    amount: int
    reason: Text = ""


class PrizeCreate(BaseModel):
    name: Name
    cost: int = Field(ge=1, le=100_000)
    desc: Text = ""
    category: Annotated[str, Field(max_length=30)] = "small"
    active: bool = True


class PrizeUpdate(BaseModel):
    name: Optional[Name] = None
    cost: Optional[int] = Field(default=None, ge=1, le=100_000)
    desc: Optional[Text] = None
    category: Optional[Annotated[str, Field(max_length=30)]] = None
    active: Optional[bool] = None


Login = Annotated[str, Field(max_length=64)]


class TeacherLogin(BaseModel):
    """Вход в панель: суперадмин или учитель."""
    login: Login
    password: Secret


class UserCreate(BaseModel):
    """Новый учитель. Пароль пустой — система придумает сама."""
    login: Login
    name: Name
    school_ids: List[int] = Field(default_factory=list, max_length=200)
    password: Optional[Secret] = None


class UserUpdate(BaseModel):
    name: Optional[Name] = None
    school_ids: Optional[List[int]] = Field(default=None, max_length=200)


class StudentLogin(BaseModel):
    """Вход через сайт — для детей без Telegram."""
    login: Annotated[str, Field(max_length=64)]
    password: Secret


class PasswordChange(BaseModel):
    """Ребёнок меняет себе пароль: старый нужен, чтобы чужой не поменял."""
    current: Secret
    new: Secret


class PasswordReset(BaseModel):
    """Учитель выдаёт новый пароль. Пусто — система придумает сама."""
    password: Optional[Secret] = None


class StudentUpdate(BaseModel):
    name: Optional[Name] = None
    login: Optional[Annotated[str, Field(max_length=64)]] = None


class TwaAuth(BaseModel):
    init_data: Annotated[str, Field(max_length=8192)]


class AvatarChoice(BaseModel):
    """Либо пресет из набора, либо фото профиля Telegram.

    telegram_photo_url принимается ради старых версий кабинета, но не используется:
    адрес фото берётся только из подписанных Telegram данных, сохранённых при входе.
    """
    preset: Optional[Annotated[str, Field(max_length=40)]] = None
    use_telegram_photo: bool = False
    telegram_photo_url: Optional[Annotated[str, Field(max_length=500)]] = None
