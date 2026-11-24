"""Бот преподавателей целиком: обновления Telegram идут через настоящий Dispatcher
и роутер build_router(), а исходящие запросы ловит подменная сессия — без сети.

Понедельник 23 ноября 2026; урок 09:00–09:45 в школе 1 (41.311, 69.279).
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, GetMe, SendMessage
from aiogram.types import Chat, Message, Update, User

from backend import db
from backend.checkin import clock, service, store
from backend.checkin.handlers import build_group_router, build_router
from conftest import load_db

MON = date(2026, 11, 23)
TG = 900
LAT, LON = 41.311, 69.279
METRE = 1 / 111_195


class FakeSession(BaseSession):
    """Записывает всё, что бот отправил бы в Telegram, и отвечает как Telegram."""

    def __init__(self):
        super().__init__()
        self.calls: List[Any] = []
        self.message_ids: Dict[int, int] = {}       # номер вызова → номер отправленного сообщения
        self.forbidden: set = set()                 # чаты, где бота заблокировали или выгнали

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        chat_id = getattr(method, "chat_id", None)
        if chat_id in self.forbidden:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if isinstance(method, GetMe):
            return User(id=654321, is_bot=True, first_name="Отметки", username="test_checkin_bot")
        if method.__returning__ is Message:
            message_id = 1000 + len(self.calls)
            self.message_ids[len(self.calls) - 1] = message_id
            return Message(message_id=message_id, date=datetime.now(timezone.utc),
                           chat=Chat(id=chat_id, type="private" if chat_id and chat_id > 0 else "supergroup"),
                           text=getattr(method, "text", None))
        return True

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError
        yield b""

    async def close(self):
        pass


class TeacherChat:
    """Переписка одного преподавателя с ботом."""

    def __init__(self, tg_id=TG, chat_type="private"):
        self.session = FakeSession()
        self.bot = Bot(token="654321:CHECKIN-test-token", session=self.session)
        self.dp = Dispatcher()
        self.dp.include_router(build_router())
        self.dp.include_router(build_group_router())      # как в настоящем боте
        self.tg_id = tg_id
        self.chat_type = chat_type
        self.update_id = 0

    def _feed(self, payload: Dict[str, Any]) -> List[Any]:
        before = len(self.session.calls)
        update = Update.model_validate(payload, context={"bot": self.bot})
        asyncio.run(self.dp.feed_update(self.bot, update))
        return self.session.calls[before:]

    def _base(self, at: datetime) -> Dict[str, Any]:
        self.update_id += 1
        return {"message_id": self.update_id, "date": int(at.timestamp()),
                "chat": {"id": self.tg_id if self.chat_type == "private" else -100500, "type": self.chat_type},
                "from": {"id": self.tg_id, "is_bot": False, "first_name": "Учитель", "username": "uchitel"}}

    def send(self, at: datetime, **fields) -> List[str]:
        message = dict(self._base(at), **fields)
        calls = self._feed({"update_id": self.update_id, "message": message})
        own_chat = message["chat"]["id"]
        return [c.text for c in calls if isinstance(c, (SendMessage, EditMessageText)) and c.chat_id == own_chat]

    def text(self, value: str, at: datetime = None) -> List[str]:
        entities = [{"type": "bot_command", "offset": 0, "length": len(value.split()[0])}] \
            if value.startswith("/") else None
        extra = {"entities": entities} if entities else {}
        return self.send(at or clock.now_utc(), text=value, **extra)

    def location(self, at: datetime, lat=LAT, lon=LON, **extra) -> List[str]:
        loc = {"latitude": lat, "longitude": lon, "horizontal_accuracy": 12}
        loc.update(extra.pop("loc", {}))
        return self.send(at, location=loc, **extra)

    def video_note(self, at: datetime, unique="vid-1", **extra) -> List[str]:
        return self.send(at, video_note={"file_id": "file-" + unique, "file_unique_id": unique,
                                         "length": 240, "duration": 5}, **extra)

    def press(self, data: str) -> List[Any]:
        self.update_id += 1
        payload = {"update_id": self.update_id, "callback_query": {
            "id": str(self.update_id), "chat_instance": "1", "data": data,
            "from": {"id": self.tg_id, "is_bot": False, "first_name": "Учитель"},
            "message": {"message_id": 5, "date": int(clock.now_utc().timestamp()),
                        "chat": {"id": self.tg_id, "type": "private"}, "text": "…"}}}
        return self._feed(payload)

    def member(self, status: str) -> None:
        self.update_id += 1
        bot_user = {"id": 654321, "is_bot": True, "first_name": "Отметки"}
        self._feed({"update_id": self.update_id, "my_chat_member": {
            "chat": {"id": self.tg_id, "type": "private"}, "date": int(clock.now_utc().timestamp()),
            "from": {"id": self.tg_id, "is_bot": False, "first_name": "Учитель"},
            "old_chat_member": {"status": "member", "user": bot_user},
            "new_chat_member": {"status": status, "user": bot_user, **({"until_date": 0} if status == "kicked" else {})}}})

    def keyboard_of_last(self):
        sent = [c for c in self.session.calls if isinstance(c, SendMessage)]
        return sent[-1].reply_markup if sent else None


def at(hhmm: str) -> datetime:
    moment = clock.at(MON, hhmm)
    clock.freeze(moment)
    return moment


def prepare(bound=True, consent=True, lang="ru", tracked=True, coords=True):
    def _apply(database):
        teacher = db.find_user(database, 2)
        teacher.update(checkin_tracked=tracked, lang=lang,
                       tg_id=TG if bound else None,
                       consent_at="2026-11-01T00:00:00+00:00" if consent else None,
                       invite_code=None if bound else "INVITE_code1",
                       invite_created_at=None if bound else clock.iso(clock.now_utc()))
        database["schools"][0].update(lat=LAT if coords else None, lon=LON if coords else None, radius_m=150)
        database["schedule"] = [
            {"id": 1, "class_id": 1, "weekday": 1, "start": "09:00", "end": "09:45", "room": "12", "user_id": 2,
             "created_at": "2026-01-01", "valid_from": None, "valid_to": None}]
    db.transaction(_apply)
    clock.freeze(clock.at(MON, "07:00"))
    service.tick()


def lesson_status():
    with store.tx() as conn:
        row = conn.execute("SELECT status, late_minutes, flag FROM planned_lessons ORDER BY starts_at").fetchone()
    return tuple(row) if row else None


# ── Привязка ─────────────────────────────────────────────────────────────────

def test_invite_language_consent_menu():
    prepare(bound=False, consent=False)
    chat = TeacherChat()
    replies = chat.text("/start inv_INVITE_code1")
    assert "Telegram привязан" in replies[0] and "Учитель" in replies[0]
    assert "Выберите язык" in replies[1]
    assert db.find_user(load_db(), 2)["tg_id"] == TG

    calls = chat.press("lang:uz")
    texts = [c.text for c in calls if isinstance(c, (SendMessage, EditMessageText))]
    assert texts[0] == "Til: oʻzbekcha." and "Rozimisiz?" in texts[1]
    assert any(isinstance(c, AnswerCallbackQuery) for c in calls), "кнопка не «висит»"

    calls = chat.press("consent")
    assert any(isinstance(c, EditMessageReplyMarkup) for c in calls), "кнопка согласия убрана"
    assert "Tayyor" in [c.text for c in calls if isinstance(c, SendMessage)][0]
    keyboard = chat.keyboard_of_last()
    assert keyboard.keyboard[0][0].text == "📍 Darsga keldim" and keyboard.keyboard[0][0].request_location
    assert db.find_user(load_db(), 2)["consent_at"]


def test_bad_invite_and_stranger():
    prepare(bound=False, consent=False)
    replies = TeacherChat().text("/start inv_nonexistent")
    assert "Ссылка не подошла" in replies[0] and "Havola ishlamadi" in replies[0]
    stranger = TeacherChat(tg_id=777)
    assert "ссылку-приглашение" in stranger.text("/start")[0]
    assert "ссылку-приглашение" in stranger.location(at("08:50"))[0], "чужой не отмечается"


def test_no_menu_without_consent():
    prepare(consent=False)
    replies = TeacherChat().location(at("08:50"))
    assert "Согласны?" in replies[0]
    assert lesson_status() == ("planned", None, "")


def test_untracked_teacher_is_told_so():
    prepare(tracked=False)
    assert "не отмечаетесь" in TeacherChat().text("/start")[0]


# ── Отметка ──────────────────────────────────────────────────────────────────

def test_checkin_on_time():
    prepare()
    chat = TeacherChat()
    reply = chat.location(at("08:50"))[0]
    assert "09:00–09:45 5-А" in reply and "около 0 м" in reply and "2 минуты" in reply
    reply = chat.video_note(at("08:51"))[0]
    assert reply.startswith("✅ Кругляшок принят.") and "09:00 5-А — вовремя" in reply
    assert lesson_status() == ("checked_in", 0, "")


def test_checkin_late():
    prepare()
    chat = TeacherChat()
    chat.location(at("09:07"))
    assert "опоздание 7 мин" in chat.video_note(at("09:08"))[0]
    assert lesson_status() == ("late", 7, "")


def test_time_comes_from_telegram_not_server():
    """Отметку в 08:55 доставили после перезапуска сервера в 09:30 — это не опоздание."""
    prepare()
    chat = TeacherChat()
    sent = clock.at(MON, "08:55")
    clock.freeze(clock.at(MON, "09:30"))
    chat.location(sent)
    chat.video_note(sent + timedelta(seconds=40))
    assert lesson_status() == ("checked_in", 0, "")


def test_far_from_school():
    prepare()
    chat = TeacherChat()
    assert "далеко от школы (около 2000 м)" in chat.location(at("08:55"), lat=LAT + 2000 * METRE)[0]
    assert "на проверке: далеко от школы" in chat.video_note(at("08:56"))[0]
    assert lesson_status() == ("needs_review", 0, "far")


def test_school_without_coords():
    prepare(coords=False)
    chat = TeacherChat()
    assert "не отмечено место на карте" in chat.location(at("08:55"))[0]


def test_forwarded_and_late_video():
    prepare()
    chat = TeacherChat()
    chat.location(at("08:50"))
    forwarded = {"forward_origin": {"type": "user", "date": int(at("08:50").timestamp()),
                                    "sender_user": {"id": 1, "is_bot": False, "first_name": "Друг"}}}
    assert "Пересланный кругляшок" in chat.video_note(at("08:50"), **forwarded)[0]
    assert "кругляшок (видеосообщение)" in chat.send(at("08:51"), photo=[
        {"file_id": "p", "file_unique_id": "pu", "width": 10, "height": 10}])[0]
    assert "Время на кругляшок вышло" in chat.video_note(at("08:53"))[0], "через 3 минуты"
    assert lesson_status() == ("needs_review", 0, "no_video")


def test_bad_locations():
    prepare()
    chat = TeacherChat()
    venue = {"venue": {"location": {"latitude": LAT, "longitude": LON}, "title": "Школа", "address": "x"}}
    assert "место из списка" in chat.location(at("08:50"), **venue)[0]
    assert "Трансляция" in chat.location(at("08:50"), loc={"live_period": 900})[0]
    forwarded = {"forward_origin": {"type": "hidden_user", "date": 1, "sender_user_name": "x"}}
    assert "Пересланную геолокацию" in chat.location(at("08:50"), **forwarded)[0]
    assert lesson_status() == ("planned", None, "")


def test_no_lesson_and_already():
    prepare()
    chat = TeacherChat()
    assert "за 60 мин до начала" in chat.location(at("07:30"))[0]
    chat.location(at("08:50"))
    chat.video_note(at("08:51"))
    assert "уже отмечены" in chat.location(at("08:58"))[0]
    assert "Сначала нажмите" in chat.video_note(at("08:59"), unique="vid-2")[0]


def test_uzbek_texts_in_checkin():
    prepare(lang="uz")
    chat = TeacherChat()
    assert "5 soniyalik dumaloq video" in chat.location(at("08:50"))[0]
    assert "oʻz vaqtida" in chat.video_note(at("08:51"))[0]


# ── Меню ─────────────────────────────────────────────────────────────────────

def test_menu_today_and_month():
    prepare()
    chat = TeacherChat()
    chat.location(at("08:50"))
    chat.video_note(at("08:51"))
    today = chat.text("📅 Мои уроки сегодня", at("10:00"))[0]
    assert today.startswith("📅 Сегодня, 23 ноября:") and "09:00–09:45" in today and "отмечен" in today
    month = chat.text("⏱ Мои часы за месяц", at("10:00"))[0]
    assert "ноябрь 2026" in month and "Подтверждено: 1" in month and "Часы: 0.75" in month
    assert "сум" not in month


def test_menu_buttons_work_in_both_languages():
    """Сменили язык, а клавиатура в Telegram осталась старой — кнопки всё равно понимаются."""
    prepare(lang="uz")
    reply = TeacherChat().text("📅 Мои уроки сегодня", at("10:00"))[0]
    assert reply.startswith("📅 Bugun, 23-noyabr:")


def test_location_button_text_from_desktop_explains():
    prepare()
    assert "с телефона" in TeacherChat().text("📍 Я на месте", at("08:50"))[0]


def test_language_button_and_unknown_text():
    prepare()
    chat = TeacherChat()
    assert "Выберите язык" in chat.text("🌐 Язык")[0]
    assert "Не понял" in chat.text("привет")[0]


# ── Служебное ────────────────────────────────────────────────────────────────

def test_blocking_the_bot_is_recorded():
    prepare()
    chat = TeacherChat()
    chat.member("kicked")
    assert db.find_user(load_db(), 2)["tg_blocked_at"]
    chat.member("member")
    assert db.find_user(load_db(), 2)["tg_blocked_at"] is None


def test_groups_are_ignored():
    prepare()
    assert TeacherChat(chat_type="supergroup").location(at("08:50")) == []
    assert lesson_status() == ("planned", None, "")


def test_names_are_escaped_for_html():
    prepare(bound=False, consent=False)

    def _rename(database):
        db.find_user(database, 2)["name"] = "<b>Учитель</b>"
    db.transaction(_rename)
    reply = TeacherChat().text("/start inv_INVITE_code1")[0]
    assert "&lt;b&gt;" in reply
