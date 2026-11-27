"""Бот для учеников: привязка по QR-коду и единственная кнопка «Зайти в свой кабинет».

Роутер ученика стоит в боте последним (см. bot.py): сюда попадает всё, что не взяли
группа «Отметки» и роутер преподавателя. Работает только в личных сообщениях —
в группе «Отметки» бот не отвечает на обычные сообщения."""
import html
import logging
import re
from typing import Any, Dict, Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           WebAppInfo)

from .. import config, db, themes

log = logging.getLogger("stars.bot")

CODE_RE = re.compile(r"^[A-Za-z0-9]{6,16}$")


def cabinet_keyboard(student: Optional[Dict[str, Any]] = None) -> Optional[InlineKeyboardMarkup]:
    """Одна кнопка, открывающая Telegram Web App.

    Telegram принимает web_app только по HTTPS. Если адрес пока локальный
    (туннель не запущен), кнопку не отдаём — иначе Telegram отклонит сообщение.
    """
    url = config.webapp_url()
    if not url.startswith("https://"):
        return None
    if student:
        # Тема школы — кабинет сразу открывается в своём оформлении, без мигания
        url += "?theme=" + themes.theme_of_class(db.load_db(), student.get("class_id"))
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Зайти в свой кабинет", web_app=WebAppInfo(url=url))
    ]])


NO_REWARDS_TEXT = (
    "🏫 В твоей школе система звёзд пока не работает — кабинета со звёздами и призами нет.\n\n"
    "Если учитель говорит, что звёзды уже есть, — пусть обратится к администратору."
)


async def send_cabinet(message: Message, text: str,
                       student: Optional[Dict[str, Any]] = None) -> None:
    if student:
        database = db.load_db()
        if not db.rewards_on(db.school_of_student(database, student)):
            await message.answer(NO_REWARDS_TEXT)
            return
    keyboard = cabinet_keyboard(student)
    if keyboard is None:
        await message.answer(
            f"{text}\n\n⏳ Кабинет временно недоступен. Загляни чуть позже."
        )
        return
    await message.answer(text, reply_markup=keyboard)


def bind_by_code(code: str, tg_id: int, tg_username: Optional[str]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Привязывает Telegram-аккаунт к ученику по коду с карточки.

    Возвращает (статус, ученик). Статусы:
      ok          — привязали впервые
      already_me  — этот же аккаунт уже привязан, просто пускаем
      taken       — код уже занят другим аккаунтом
      other_card  — этот аккаунт уже привязан к другой карточке (ученик — та карточка)
      not_found   — такого кода нет
    """
    result: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        student = db.find_by_token(database, code)
        if not student:
            result["status"] = "not_found"
            return db.NO_SAVE

        result["student"] = student
        bound_to = student.get("tg_id")

        # Один аккаунт — одна карточка. Иначе ребёнок, отсканировавший карточку
        # друга «посмотреть», забирает её себе, а при входе попадает в один
        # из двух кабинетов наугад.
        if bound_to is None:
            mine = db.find_by_tg_id(database, tg_id)
            if mine and mine["id"] != student["id"]:
                result["status"] = "other_card"
                result["student"] = mine
                return db.NO_SAVE

        if bound_to is None:
            student["tg_id"] = tg_id
            student["tg_username"] = tg_username
            student["bound_at"] = db.now_iso()
            result["status"] = "ok"
            return None

        if bound_to == tg_id:
            result["status"] = "already_me"
            if tg_username and student.get("tg_username") != tg_username:
                student["tg_username"] = tg_username
                return None
            return db.NO_SAVE

        result["status"] = "taken"
        return db.NO_SAVE

    db.transaction(_apply)
    return result.get("status", "not_found"), result.get("student")


async def handle_code(message: Message, code: str) -> None:
    user = message.from_user
    status, student = bind_by_code(code, user.id, user.username)

    if status == "not_found":
        await message.answer(
            "😕 Такой код не найден.\n\n"
            "Проверь код на своей карточке или подойди к учителю — он выдаст новую."
        )
        return

    if status == "other_card":
        await message.answer(
            f"🔗 Твой Telegram уже привязан к карточке <b>{html.escape(student['name'])}</b>.\n\n"
            "Одним аккаунтом можно войти только в один кабинет. Если это не твоя карточка — "
            "открой кабинет, вкладка «Профиль» → «Выйти из аккаунта», и отсканируй свою."
        )
        return

    if status == "taken":
        await message.answer(
            "🔒 Этот код уже привязан к другому аккаунту Telegram.\n\n"
            "Если это твоя карточка — подойди к учителю, он выпустит новый код."
        )
        return

    # Сообщения уходят в режиме HTML: угловая скобка в имени сломала бы отправку
    name = html.escape(student["name"].split()[-1]) if student["name"] else "друг"
    if status == "ok":
        text = (
            f"🎉 Отлично, {name}! Карточка привязана.\n\n"
            f"Класс: <b>{html.escape(db.class_name(db.load_db(), student.get('class_id')))}</b>\n"
            f"Сейчас у тебя: <b>{student['total_stars']} ⭐</b>\n\n"
            "Нажми кнопку ниже — откроется твой кабинет со звёздами, рейтингом класса и витриной призов."
        )
    else:
        text = (
            f"С возвращением, {name}! 👋\n\n"
            f"У тебя <b>{student['total_stars']} ⭐</b>. Открывай кабинет."
        )
    await send_cabinet(message, text, student)


async def on_start(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip()
    if code:
        await handle_code(message, code)
        return

    student = db.find_by_tg_id(db.load_db(), message.from_user.id)
    if student:
        name = html.escape(student["name"].split()[-1]) if student["name"] else "друг"
        await send_cabinet(
            message,
            f"Привет, {name}! 👋\nУ тебя <b>{student['total_stars']} ⭐</b>. Открывай кабинет.",
            student,
        )
        return

    await message.answer(
        "👋 Привет! Это бот школьной системы звёзд.\n\n"
        "Чтобы войти в свой кабинет, наведи камеру телефона на <b>QR-код со своей карточки</b> — "
        "её выдаёт учитель.\n\n"
        "Если камера не читает код, просто отправь мне <b>код с карточки</b> (8 символов).\n\n"
        "<i>Вы сотрудник школы? Отсканируйте QR со своей карточки сотрудника или пришлите код с неё.</i>"
    )


async def on_help(message: Message) -> None:
    await message.answer(
        "ℹ️ Как пользоваться:\n\n"
        "1. Возьми карточку у учителя.\n"
        "2. Отсканируй QR-код или пришли мне код с карточки.\n"
        "3. Нажми кнопку «Зайти в свой кабинет».\n\n"
        "В кабинете видно твои звёзды, место в рейтинге класса, призы и аватар.\n"
        "Потерял карточку — подойди к учителю, он выдаст новый код."
    )


async def on_cabinet(message: Message) -> None:
    student = db.find_by_tg_id(db.load_db(), message.from_user.id)
    if not student:
        await message.answer("Сначала отсканируй QR-код со своей карточки 🙂")
        return
    await send_cabinet(message, f"Твои звёзды: <b>{student['total_stars']} ⭐</b>", student)


async def on_text(message: Message) -> None:
    """Код, присланный текстом — для тех, у кого камера не читает QR."""
    text = (message.text or "").strip()
    if CODE_RE.match(text):
        await handle_code(message, text)
        return

    await message.answer(
        "Не понял 🤔\n\nОтсканируй QR-код с карточки или пришли код с неё (8 символов). "
        "Команда /help — короткая инструкция."
    )


async def on_location(message: Message) -> None:
    """Геолокация от того, кто не привязан как сотрудник: отмечаются только преподаватели."""
    await message.answer(
        "📍 Отмечаться в боте могут только преподаватели.\n\n"
        "Отсканируйте QR со своей карточки сотрудника или пришлите код с неё — после этого бот узнает вас."
    )


def build_router() -> Router:
    """Новый роутер на каждый Dispatcher: aiogram не даёт подключить один роутер дважды."""
    router = Router(name="student")
    router.message.filter(F.chat.type == "private")
    router.message.register(on_start, CommandStart())
    router.message.register(on_help, Command("help"))
    router.message.register(on_cabinet, Command("cabinet"))
    router.message.register(on_location, F.location)
    router.message.register(on_text, F.text)
    return router
