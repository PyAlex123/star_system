"""Преподаватель в общем боте: привязка по ссылке, язык, согласие, меню и отметка.

Бот у системы один (backend/bot/bot.py). Роутер преподавателя берёт сообщение, только если
Telegram-аккаунт привязан к сотруднику или пришёл код сотрудника — как у ученика: QR карточки
(/start inv_<код>), /start <код> или код текстом. Остальное уходит роутеру ученика.

Бот только переводит сообщения Telegram в вызовы service.py и отвечает человеку.
Время отметки — время сообщения в Telegram (message.date), а не сервера:
обновление, пришедшее после перезапуска, не превращается в опоздание.

Сервис синхронный (JSON и SQLite), поэтому зовётся через asyncio.to_thread —
цикл событий, на котором живут оба бота и сайт, не ждёт диска.
Работает только в личных сообщениях; группа «Отметки» — отдельный роутер (этап 6).
"""
import asyncio
import html
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, BotCommandScopeChat, CallbackQuery, ChatMemberUpdated, ForceReply,
                           InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonCommands,
                           MenuButtonWebApp, Message,
                           ReplyKeyboardMarkup, ReplyParameters, WebAppInfo)

from .. import config, db
from . import notify, service, store
from .locales import LANGS, day_title, lang_of, minutes, month_title, t

log = logging.getLogger("stars.checkin.bot")

# Код с карточки сотрудника, набранный руками: 8 букв и цифр, можно с пробелом или дефисом
CODE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{6,10}[A-Za-z0-9]$")
MENU_ACTIONS = ("menu_here", "menu_today", "menu_month", "menu_lang", "menu_panel")


def _menu_lookup() -> Dict[str, str]:
    """Текст кнопки меню → действие, на обоих языках: язык мог смениться, а клавиатура — остаться."""
    return {texts[key]: key for texts in LANGS.values() for key in MENU_ACTIONS}


MENU_BY_TEXT = _menu_lookup()


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Меню преподавателя. По https панель открывает кнопка «Панель» слева от поля ввода —
    второй такой же клавиши нет. Без https та кнопка не ставится, и клавиша присылает адрес сайта.
    Текст «🖥 Панель» со старой клавиатуры по-прежнему понимается (MENU_ACTIONS)."""
    last = [KeyboardButton(text=t(lang, "menu_lang"))]
    if not config.staff_panel_url():
        last.insert(0, KeyboardButton(text=t(lang, "menu_panel")))
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t(lang, "menu_here"), request_location=True)],
        [KeyboardButton(text=t(lang, "menu_today")), KeyboardButton(text=t(lang, "menu_month"))],
        last,
    ], resize_keyboard=True, is_persistent=True)


def panel_markup(lang: str) -> Optional[InlineKeyboardMarkup]:
    """Кнопка «Открыть панель» — панель учителя как Telegram Web App. Кнопкой под сообщением,
    а не кнопкой клавиатуры: Web App с клавиатуры не получает подписи Telegram и не войдёт сам."""
    url = config.staff_panel_url()
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "panel_button"), web_app=WebAppInfo(url=url)),
    ]])


async def send_panel(message: Message, lang: str, text: Optional[str] = None) -> None:
    """Сообщение с кнопкой панели; без https (локальный запуск) — адрес сайта текстом."""
    markup = panel_markup(lang)
    if markup:
        await message.answer(text or t(lang, "panel_text"), reply_markup=markup)
    else:
        prefix = f"{text}\n\n" if text else ""
        await message.answer(prefix + t(lang, "panel_no_https", url=config.site_url("/login")))


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="Oʻzbekcha", callback_data="lang:uz"),
    ]])


def consent_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "consent_button"), callback_data="consent"),
    ]])


def _when(message: Message) -> datetime:
    moment = message.date
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


async def _call(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


def _not_tracked_text(user: Dict[str, Any]) -> str:
    """Администратору отметок Telegram нужен для группы, а не для отметок — подсказываем, что делать."""
    lang = lang_of(user.get("lang"))
    if db.can_manage_checkins(user):
        return t(lang, "admin_bound", name=esc(user.get("name")))
    return t(lang, "not_tracked")


async def _ready_user(message: Message) -> Optional[Dict[str, Any]]:
    """Сотрудник, который может отмечаться, или None — тогда человек уже получил объяснение."""
    user = await _call(service.user_by_tg, message.from_user.id)
    if not user:
        await message.answer(t("ru", "start_unbound"))
        return None
    lang = lang_of(user.get("lang"))
    if not user.get("checkin_tracked"):
        await message.answer(_not_tracked_text(user))
        return None
    if not user.get("consent_at"):
        await message.answer(t(lang, "consent"), reply_markup=consent_keyboard(lang))
        return None
    return user


def _lessons_text(lessons) -> str:
    return ", ".join(f"{l['start']}–{l['end']} {esc(l['class'])}" for l in lessons)


# ── Ответы на отметку ─────────────────────────────────────────────────────────

LOCATION_PROBLEMS = {"forwarded": "loc_forwarded", "venue": "loc_venue", "live": "loc_live"}
VIDEO_PROBLEMS = {"no_pending": "vid_no_pending", "not_video_note": "vid_not_note",
                  "forwarded": "vid_forwarded", "duplicate": "vid_duplicate", "late": "vid_late"}


def location_reply(lang: str, result: Dict[str, Any], window: int) -> str:
    kind = result["result"]
    if kind == "problem":
        return t(lang, LOCATION_PROBLEMS.get(result["problem"], "loc_forwarded"))
    if kind == "already":
        return t(lang, "loc_already")
    if kind == "no_lesson":
        return t(lang, "loc_no_lesson", window=window)
    if kind == "not_tracked":
        return t(lang, "not_tracked")
    geo = result["geo"]
    if geo == "ok":
        distance = t(lang, "dist_ok", meters=result["distance_m"])
    elif geo == "far":
        distance = t(lang, "dist_far", meters=result["distance_m"])
    else:
        distance = t(lang, "dist_no_coords")
    return t(lang, "loc_await", school=esc(result["school"]), lessons=_lessons_text(result["lessons"]),
             distance=distance, minutes=minutes(lang, math.ceil(result["deadline_sec"] / 60)))


def video_reply(lang: str, result: Dict[str, Any]) -> str:
    if result["result"] == "problem":
        return t(lang, VIDEO_PROBLEMS.get(result["problem"], "vid_not_note"))
    lines = [t(lang, "done_header")]
    for lesson in result.get("lessons", []):
        args = {"time": lesson["start"], "cls": esc(lesson["class"])}
        if lesson["status"] == "late":
            lines.append(t(lang, "line_late", minutes=lesson["late_minutes"], **args))
        elif lesson["status"] == "needs_review":
            reasons = ", ".join(t(lang, f"reason_{flag}") for flag in lesson["flag"].split(",") if flag)
            lines.append(t(lang, "line_review", reasons=reasons, **args))
        else:
            lines.append(t(lang, "line_ok", **args))
    return "\n".join(lines)


def today_reply(lang: str, data: Dict[str, Any]) -> str:
    if not data["lessons"]:
        return t(lang, "today_empty")
    lines = [t(lang, "today_header", date=day_title(lang, data["date"]))]
    for lesson in data["lessons"]:
        status = t(lang, f"status_{lesson['status']}", late=lesson.get("late_minutes") or 0)
        lines.append(t(lang, "today_line", start=lesson["start"], end=lesson["end"], school=esc(lesson["school"]),
                       cls=esc(lesson["class"]), status=status))
    return "\n".join(lines)


def month_reply(lang: str, data: Dict[str, Any]) -> str:
    hours = f"{data['hours']:.2f}".rstrip("0").rstrip(".")
    return t(lang, "month", month=month_title(lang, data["year"], data["month"]), planned=data["planned"],
             confirmed=data["confirmed"], late=data["late"], pending=data["pending"], hours=hours)


# ── Роутер ────────────────────────────────────────────────────────────────────

def code_from_text(text: Optional[str]) -> str:
    """Код сотрудника, набранный руками: «ABCD 2345», «abcd-2345». Иначе пусто."""
    text = (text or "").strip()
    if not CODE_TEXT_RE.match(text):
        return ""
    code = db.normalize_code(text)
    return code if len(code) == 8 else ""


def start_code(args: str) -> str:
    """Код из /start: inv_<код> — из QR и ссылки карточки сотрудника, или сам код."""
    args = (args or "").strip()
    return args[len(config.STAFF_START_PREFIX):] if args.startswith(config.STAFF_START_PREFIX) else args


async def is_staff_message(message: Message) -> bool:
    """Сообщение — для роутера преподавателя: код сотрудника (QR карточки, ссылка, код текстом)
    или привязанный сотрудник. /start с кодом ученика уходит ученику (карточку сканируют и в
    чужом телефоне)."""
    if not message.from_user:
        return False
    text = (message.text or "").strip()
    if text.startswith("/start"):
        args = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if args.startswith(config.STAFF_START_PREFIX):
            return True
        if args:
            return await _call(service.user_by_code, args) is not None
    elif code_from_text(text) and await _call(service.user_by_code, code_from_text(text)) is not None:
        return True
    return await _call(service.user_by_tg, message.from_user.id) is not None


async def staff_chat(bot, chat_id: int, lang: str = "ru") -> None:
    """У сотрудника кнопка меню слева от поля ввода — «Панель» (панель учителя как Telegram
    Web App, вход по подписи Telegram), а не «Кабинет» ученика; и свои команды.
    Без https (локальный запуск) — кнопка команд."""
    url = config.staff_panel_url()
    button = (MenuButtonWebApp(text=t(lang, "panel_menu_button"), web_app=WebAppInfo(url=url))
              if url else MenuButtonCommands())
    try:
        await bot.set_chat_menu_button(chat_id=chat_id, menu_button=button)
        await bot.set_my_commands([
            BotCommand(command="start", description="Меню / Menyu"),
            BotCommand(command="help", description="Как отмечаться / Qanday belgilash"),
        ], scope=BotCommandScopeChat(chat_id=chat_id))
    except Exception as exc:                          # не критично — меню останется общим
        log.warning("Не удалось настроить меню сотрудника: %s", exc)


def build_router() -> Router:
    """Новый роутер на каждый Dispatcher: aiogram не даёт подключить один роутер дважды."""
    router = Router(name="checkin-teacher")
    router.message.filter(F.chat.type == "private", is_staff_message)

    async def welcome(message: Message) -> None:
        """Привязанному сотруднику — то, что ему сейчас нужно: согласие или меню."""
        user = await _call(service.user_by_tg, message.from_user.id)
        if not user:
            await message.answer(t("ru", "start_unbound") + "\n\n" + t("uz", "start_unbound"))
            return
        lang = lang_of(user.get("lang"))
        await staff_chat(message.bot, message.chat.id, lang)
        if not user.get("checkin_tracked"):
            await send_panel(message, lang, _not_tracked_text(user))     # панель нужна и без отметок
        elif not user.get("consent_at"):
            await message.answer(t(lang, "consent"), reply_markup=consent_keyboard(lang))
        else:
            await message.answer(t(lang, "menu_hint"), reply_markup=menu_keyboard(lang))

    async def bind(message: Message, code: str) -> None:
        """Код сотрудника — из QR, ссылки или набранный руками — как карточка ученика."""
        tg = message.from_user
        status, user = await _call(service.bind_code, code, tg.id, tg.username)
        if status in ("not_found", "taken", "other_user"):
            await message.answer(t("ru", f"code_{status}") + "\n\n" + t("uz", f"code_{status}"))
            return
        if status == "ok":
            lang = lang_of(user.get("lang"))
            await staff_chat(message.bot, message.chat.id, lang)
            if not user.get("checkin_tracked"):
                await send_panel(message, lang, _not_tracked_text(user))
                return
            await message.answer(t(lang, "code_ok", name=esc(user.get("name"))))
            await message.answer(t(lang, "choose_lang"), reply_markup=lang_keyboard())
            return
        await welcome(message)                         # already_me — свой код ещё раз

    @router.message(CommandStart())
    async def on_start(message: Message, command: CommandObject) -> None:
        code = start_code(command.args or "")
        if code:
            await bind(message, code)
            return
        await welcome(message)

    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        user = await _call(service.user_by_tg, message.from_user.id)
        lang = lang_of((user or {}).get("lang"))
        await message.answer(t(lang, "help"), reply_markup=menu_keyboard(lang) if user else None)

    @router.callback_query(F.data.startswith("lang:"))
    async def on_lang(callback: CallbackQuery) -> None:
        user = await _call(service.user_by_tg, callback.from_user.id)
        if not user:
            await callback.answer()
            return
        lang = lang_of(callback.data.split(":", 1)[1])
        user = await _call(service.set_lang, user["id"], lang)
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(t(lang, "lang_set"))
            await staff_chat(callback.bot, callback.message.chat.id, lang)     # «Панель» / «Panel»
            if user.get("consent_at"):
                await callback.message.answer(t(lang, "menu_hint"), reply_markup=menu_keyboard(lang))
            else:
                await callback.message.answer(t(lang, "consent"), reply_markup=consent_keyboard(lang))

    @router.callback_query(F.data == "consent")
    async def on_consent(callback: CallbackQuery) -> None:
        user = await _call(service.user_by_tg, callback.from_user.id)
        if not user:
            await callback.answer()
            return
        lang = lang_of(user.get("lang"))
        await _call(service.give_consent, user["id"])
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(t(lang, "consent_ok"), reply_markup=menu_keyboard(lang))

    @router.message(F.location)
    async def on_location(message: Message) -> None:
        user = await _ready_user(message)
        if not user:
            return
        lang = lang_of(user.get("lang"))
        loc = message.location
        result = await _call(service.start_checkin, user["id"], loc.latitude, loc.longitude, _when(message),
                             accuracy=loc.horizontal_accuracy, live_period=loc.live_period,
                             venue=message.venue is not None, forwarded=message.forward_origin is not None,
                             message_id=message.message_id)
        settings = await _call(store.get_settings)
        await message.answer(location_reply(lang, result, settings["checkin_window_before_min"]),
                             reply_markup=menu_keyboard(lang))

    @router.message(F.video_note | F.video | F.photo | F.document | F.animation)
    async def on_media(message: Message) -> None:
        user = await _ready_user(message)
        if not user:
            return
        lang = lang_of(user.get("lang"))
        note = message.video_note
        kind = "video_note" if note else ("video" if message.video else "other")
        result = await _call(service.finish_checkin, user["id"], kind, message.forward_origin is not None,
                             note.file_id if note else None, note.file_unique_id if note else None,
                             _when(message), message.message_id)
        await message.answer(video_reply(lang, result), reply_markup=menu_keyboard(lang))
        if result["result"] == "accepted" or result.get("problem") == "late":
            await notify.drain(message.bot)              # копия в группу — сразу, а не через минуту

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        action = MENU_BY_TEXT.get((message.text or "").strip())
        code = "" if action else code_from_text(message.text)
        if code and await _call(service.user_by_code, code):
            await bind(message, code)                  # код с карточки сотрудника — текстом
            return
        if action == "menu_here":
            # С компьютера кнопка геолокации не работает — Telegram присылает её текст
            user = await _ready_user(message)
            if user:
                lang = lang_of(user.get("lang"))
                await message.answer(t(lang, "help"), reply_markup=menu_keyboard(lang))
            return
        if action == "menu_panel":
            # Панель — любому привязанному сотруднику: и без отметок, и до согласия
            user = await _call(service.user_by_tg, message.from_user.id)
            if not user:
                await message.answer(t("ru", "start_unbound") + "\n\n" + t("uz", "start_unbound"))
                return
            await send_panel(message, lang_of(user.get("lang")))
            return
        if action == "menu_lang":
            await message.answer(t("ru", "choose_lang"), reply_markup=lang_keyboard())
            return
        user = await _ready_user(message)
        if not user:
            return
        lang = lang_of(user.get("lang"))
        if action == "menu_today":
            data = await _call(service.today_for, user["id"])
            await message.answer(today_reply(lang, data), reply_markup=menu_keyboard(lang))
        elif action == "menu_month":
            data = await _call(service.month_for, user["id"])
            await message.answer(month_reply(lang, data), reply_markup=menu_keyboard(lang))
        else:
            await message.answer(t(lang, "unknown"), reply_markup=menu_keyboard(lang))

    @router.my_chat_member(F.chat.type == "private")
    async def on_member(update: ChatMemberUpdated) -> None:
        """Преподаватель остановил бота — администратор увидит «заблокировал бота»."""
        blocked = update.new_chat_member.status == "kicked"
        await _call(service.set_blocked, update.from_user.id, blocked)

    return router


# ── Группа «Отметки» ──────────────────────────────────────────────────────────

GROUPS = {"group", "supergroup"}
NOT_ADMIN = ("Решать уроки могут только администратор и ответственный за отметки. "
             "Привяжите Telegram кодом со своей карточки сотрудника.")


def build_group_router() -> Router:
    """Закрытая группа администраторов: копии отметок, карточки уроков и кнопки решений.
    Нажимать кнопки может только администратор или ответственный за отметки."""
    router = Router(name="checkin-group")
    router.message.filter(F.chat.type.in_(GROUPS))

    @router.message(Command("setchat"))
    async def on_setchat(message: Message) -> None:
        admin = await _call(service.set_report_chat, message.from_user.id, message.chat.id, message.chat.title or "")
        if admin:
            await message.reply("✅ Готово: сюда будут приходить отметки преподавателей и карточки уроков "
                                "без отметки. Решать по карточкам могут администратор и ответственные за отметки.")
        else:
            await message.reply("Эта команда — для администратора отметок. Сначала привяжите Telegram: "
                                "напишите боту в личные сообщения код со своей карточки сотрудника "
                                "(«Сотрудники» → вы → «Карточка»).")

    @router.message(F.migrate_to_chat_id)
    async def on_migrate(message: Message) -> None:
        await _call(service.migrate_report_chat, message.chat.id, message.migrate_to_chat_id)

    @router.message(F.reply_to_message)
    async def on_reason(message: Message) -> None:
        """Ответ администратора с причиной «Не засчитывать»."""
        pending = await _call(service.take_reason, message.chat.id, message.reply_to_message.message_id,
                              message.from_user.id)
        if not pending:
            return
        reason = (message.text or "").strip()
        if not reason:
            await _call(service.ask_reason, message.chat.id, message.reply_to_message.message_id,
                        pending["lesson_id"], pending["action"], message.from_user.id)
            await message.reply("Нужна причина текстом — ответьте на вопрос ещё раз.")
            return
        try:
            await _call(service.decide_from_chat, message.from_user.id, pending["lesson_id"], pending["action"],
                        reason)
        except service.CheckinError as exc:
            await message.reply(esc(exc))
            return
        await notify.refresh_card(message.bot, pending["lesson_id"], reason)
        await message.reply("Готово: урок не засчитан, причина записана.")

    @router.callback_query(F.data.startswith("ck:"))
    async def on_card(callback: CallbackQuery) -> None:
        try:
            _, action, raw_id = callback.data.split(":")
            lesson_id = int(raw_id)
        except ValueError:
            await callback.answer()
            return
        admin = await _call(service.chat_admin, callback.from_user.id)
        if not admin:
            await callback.answer(NOT_ADMIN, show_alert=True)
            return
        if action == "approve":
            try:
                await _call(service.decide_from_chat, callback.from_user.id, lesson_id, "approve",
                            "Засчитано в группе «Отметки»")
            except service.CheckinError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            await callback.answer("Засчитано")
            await notify.refresh_card(callback.bot, lesson_id, extra_message=callback.message)
        elif action == "reject" and callback.message:
            lesson = await _call(service.card_for, lesson_id)
            mention = f'<a href="tg://user?id={callback.from_user.id}">{esc(admin.get("name"))}</a>'
            prompt = await callback.message.answer(
                f"{mention}, почему не засчитываем урок {lesson['start']} ({esc(lesson['teacher'])})? "
                "Ответьте на это сообщение — причина попадёт в историю правок.",
                reply_markup=ForceReply(selective=True),
                reply_parameters=ReplyParameters(message_id=callback.message.message_id))
            await _call(service.ask_reason, prompt.chat.id, prompt.message_id, lesson_id, "reject",
                        callback.from_user.id)
            await callback.answer()
        else:
            await callback.answer()

    @router.my_chat_member(F.chat.type.in_(GROUPS))
    async def on_group_member(update: ChatMemberUpdated) -> None:
        if update.new_chat_member.status in ("left", "kicked"):
            await _call(service.drop_report_chat, update.chat.id)

    return router
