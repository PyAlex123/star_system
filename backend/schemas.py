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


Theme = Annotated[str, Field(pattern="^(notebook|quest|sky|clear|sticker)$")]


class SchoolTheme(BaseModel):
    """Тема оформления школы — одна из пяти из docs/design."""
    theme: Theme


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
    prize_id: Optional[int] = None      # выдан приз с витрины — для «Мои призы» у ребёнка


class LessonUndo(BaseModel):
    """Отменить или вернуть начисление: одному ученику или всему уроку (student_id пуст)."""
    student_id: Optional[int] = None


class PrizeCreate(BaseModel):
    name: Name
    cost: int = Field(ge=1, le=100_000)
    desc: Text = ""
    category: Annotated[str, Field(pattern="^(small|medium|large)$")] = "small"
    active: bool = True


class PrizeUpdate(BaseModel):
    name: Optional[Name] = None
    cost: Optional[int] = Field(default=None, ge=1, le=100_000)
    desc: Optional[Text] = None
    category: Optional[Annotated[str, Field(pattern="^(small|medium|large)$")]] = None
    active: Optional[bool] = None


Login = Annotated[str, Field(max_length=64)]


class TeacherLogin(BaseModel):
    """Вход в панель: суперадмин или учитель."""
    login: Login
    password: Secret


StaffRole = Annotated[str, Field(pattern="^(teacher|school_admin)$")]


class UserCreate(BaseModel):
    """Новый сотрудник: учитель или администрация школы. Пароль пустой — система придумает сама."""
    login: Login
    name: Name
    school_ids: List[int] = Field(default_factory=list, max_length=200)
    password: Optional[Secret] = None
    role: StaffRole = "teacher"


class UserUpdate(BaseModel):
    name: Optional[Name] = None
    school_ids: Optional[List[int]] = Field(default=None, max_length=200)
    role: Optional[StaffRole] = None


Clock = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class ScheduleCreate(BaseModel):
    """Занятие класса — сразу на несколько дней недели (1 — понедельник … 7 — воскресенье)."""
    class_id: int
    weekdays: List[Annotated[int, Field(ge=1, le=7)]] = Field(min_length=1, max_length=7)
    start: Clock
    end: Clock
    room: Annotated[str, Field(max_length=40)] = ""
    user_id: Optional[int] = None


class ScheduleUpdate(BaseModel):
    weekday: Optional[Annotated[int, Field(ge=1, le=7)]] = None
    start: Optional[Clock] = None
    end: Optional[Clock] = None
    room: Optional[Annotated[str, Field(max_length=40)]] = None
    user_id: Optional[int] = None


class Bell(BaseModel):
    start: Clock
    end: Clock


class SchoolBells(BaseModel):
    """Время уроков школы (номер урока — место в списке) и учебные дни: 5 — Пн–Пт, 6 — Пн–Сб."""
    bells: List[Bell] = Field(min_length=1, max_length=12)
    days: Annotated[int, Field(ge=5, le=6)] = 5


class ScheduleCell(BaseModel):
    """Ячейка таблицы расписания: день недели + номер урока.

    В ячейке один урок; в нём один класс или несколько (объединённые классы).
    class_ids — классы урока целиком (пустой список — убрать урок);
    class_id — один класс вместо прежних (null — убрать). Если пришли оба, главный class_ids."""
    school_id: int
    weekday: Annotated[int, Field(ge=1, le=7)]
    lesson: Annotated[int, Field(ge=1, le=12)]
    class_id: Optional[int] = None
    class_ids: Optional[List[int]] = Field(default=None, max_length=30)
    user_id: Optional[int] = None


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
    rating_hidden: Optional[bool] = None    # «скрыт из рейтинга»: звёзды копятся, в топе не виден


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


# ── Мониторинг ───────────────────────────────────────────────────────────────

MonitorText = Annotated[str, Field(max_length=120)]


class MonitorRunCreate(BaseModel):
    """Запуск опроса: готовый набор (set_key) или своя форма (form_id).
    class_ids пуст — все классы школы; minutes None — до ручного закрытия."""
    school_id: int
    set_key: Optional[Annotated[str, Field(max_length=20)]] = None
    form_id: Optional[int] = None
    class_ids: List[int] = Field(default_factory=list, max_length=60)
    topic: MonitorText = ""
    goals: List[MonitorText] = Field(default_factory=list, max_length=5)
    minutes: Optional[int] = Field(default=None, ge=1, le=90)
    use_code: bool = False


class MonitorFormCreate(BaseModel):
    """Новая форма: пустая, копия готового набора (from_set) или своей формы (from_form)."""
    school_id: int
    title: Optional[MonitorText] = None
    from_set: Optional[Annotated[str, Field(max_length=20)]] = None
    from_form: Optional[int] = None


class MonitorFormSave(BaseModel):
    """Форма из конструктора. Подробная проверка — monitoring.logic.clean_form."""
    title: Annotated[str, Field(max_length=200)] = ""
    kind: Annotated[str, Field(max_length=10)] = "survey"
    settings: dict = Field(default_factory=dict)
    questions: List[dict] = Field(default_factory=list, max_length=40)


class MonitorExtend(BaseModel):
    minutes: int = Field(default=5, ge=1, le=30)


class MonitorJoin(BaseModel):
    last_name: Annotated[str, Field(max_length=60)]
    first_name: Annotated[str, Field(max_length=60)]
    class_id: int
    code: Optional[Annotated[str, Field(max_length=8)]] = None


class MonitorAnswer(BaseModel):
    token: Annotated[str, Field(max_length=64)]
    question_id: Annotated[str, Field(max_length=20)]
    value: dict


class MonitorToken(BaseModel):
    token: Annotated[str, Field(max_length=64)]
