"""Модели запросов и ответов."""
from typing import List, Optional

from pydantic import BaseModel, Field


class SchoolCreate(BaseModel):
    name: str


class SchoolUpdate(BaseModel):
    name: str


class ClassCreate(BaseModel):
    school_id: int
    name: str


class ClassUpdate(BaseModel):
    name: Optional[str] = None
    school_id: Optional[int] = None


class StudentCreate(BaseModel):
    class_id: int
    name: str
    total_stars: int = 0


class BulkImportItem(BaseModel):
    """Строка из Excel. Класс приходит названием — его создаём в указанной школе."""
    school_id: int
    class_name: str
    name: str
    total_stars: int = 0


class LessonAward(BaseModel):
    student_id: int
    stars: int


class LessonComplete(BaseModel):
    class_id: int
    date: str
    awards: List[LessonAward]


class DeductRequest(BaseModel):
    student_id: int
    amount: int
    reason: str = ""


class PrizeCreate(BaseModel):
    name: str
    cost: int = Field(ge=1)
    desc: str = ""
    category: str = "small"
    active: bool = True


class PrizeUpdate(BaseModel):
    name: Optional[str] = None
    cost: Optional[int] = Field(default=None, ge=1)
    desc: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None


class TeacherLogin(BaseModel):
    password: str


class StudentLogin(BaseModel):
    """Вход через сайт — для детей без Telegram."""
    login: str
    password: str


class PasswordChange(BaseModel):
    """Ребёнок меняет себе пароль: старый нужен, чтобы чужой не поменял."""
    current: str
    new: str


class PasswordReset(BaseModel):
    """Учитель выдаёт новый пароль. Пусто — система придумает сама."""
    password: Optional[str] = None


class StudentUpdate(BaseModel):
    name: Optional[str] = None
    login: Optional[str] = None


class TwaAuth(BaseModel):
    init_data: str


class AvatarChoice(BaseModel):
    """Либо пресет из набора, либо фото профиля Telegram."""
    preset: Optional[str] = None
    use_telegram_photo: bool = False
    telegram_photo_url: Optional[str] = None
