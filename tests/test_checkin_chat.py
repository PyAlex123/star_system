"""Группа «Отметки»: копии отметок, карточки уроков с кнопками, причина отказа,
напоминания преподавателю, сводка за месяц. Через настоящий Dispatcher и подменную
сессию Telegram — без сети.

Понедельник 23 ноября 2026; урок 09:00–09:45; администратор — Telegram 500, преподаватель — 900.
"""
import asyncio
from datetime import date, timedelta

from aiogram.methods import (AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage, SendVideoNote)

from backend import db
from backend.checkin import clock, notify, service, store
from conftest import load_db
from test_checkin_bot import LAT, LON, METRE, TeacherChat, at, lesson_status, prepare

ADMIN_TG = 500
GROUP = -100500
MON = date(2026, 11, 23)


def bind_admin():
    def _apply(database):
        db.find_user(database, 1).update(tg_id=ADMIN_TG, name="Администратор")
    db.transaction(_apply)


class Group(TeacherChat):
    """Группа «Отметки»: сообщения в неё пишут администраторы."""

    def __init__(self, session_from=None, tg_id=ADMIN_TG):
        super().__init__(tg_id=tg_id, chat_type="supergroup")
        if session_from is not None:                 # одна «сеть» на преподавателя и группу
            self.session, self.bot, self.dp = session_from.session, session_from.bot, session_from.dp

    def _base(self, when):
        base = super()._base(when)
        base["chat"] = {"id": GROUP, "type": "supergroup", "title": "Отметки"}
        return base

    def say(self, text, **fields):
        entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] \
            if text.startswith("/") else None
        message = dict(self._base(clock.now_utc()), text=text, **({"entities": entities} if entities else {}), **fields)
        before = len(self.session.calls)
        self._feed({"update_id": self.update_id, "message": message})
        return [c for c in self.session.calls[before:]]

    def tap(self, data, message_id, tg_id=None):
        self.update_id += 1
        who = tg_id or self.tg_id
        before = len(self.session.calls)
        self._feed({"update_id": self.update_id, "callback_query": {
            "id": str(self.update_id), "chat_instance": "g", "data": data,
            "from": {"id": who, "is_bot": False, "first_name": "Админ"},
            "message": {"message_id": message_id, "date": int(clock.now_utc().timestamp()),
                        "chat": {"id": GROUP, "type": "supergroup", "title": "Отметки"}, "text": "карточка"}}})
        return self.session.calls[before:]


def drain(chat):
    before = len(chat.session.calls)
    result = asyncio.run(notify.drain(chat.bot))
    return result, chat.session.calls[before:]


def setup_group():
    prepare()
    bind_admin()
    teacher = TeacherChat()
    group = Group(session_from=teacher)
    calls = group.say("/setchat")
    assert "Готово" in calls[-1].text
    assert store.get_settings()["report_chat_id"] == GROUP
    return teacher, group


def sent_to(calls, chat_id, kind=SendMessage):
    return [c for c in calls if isinstance(c, kind) and c.chat_id == chat_id]


def card_message_id(chat):
    with store.tx() as conn:
        return conn.execute("SELECT message_id FROM notifications WHERE kind = 'review'").fetchone()[0]


# ── Подключение группы ───────────────────────────────────────────────────────

def test_setchat_only_by_admin():
    prepare()
    teacher_in_group = Group(tg_id=900)              # преподаватель, не ответственный за отметки
    assert "для администратора отметок" in teacher_in_group.say("/setchat")[-1].text
    assert "для администратора отметок" in Group(tg_id=777).say("/setchat")[-1].text
    assert store.get_settings()["report_chat_id"] == 0
    bind_admin()
    Group().say("/setchat")
    assert store.get_settings()["report_chat_id"] == GROUP
    with store.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'report_chat'").fetchone()[0] == 1


def test_teacher_cannot_setchat():
    """Группу «Отметки» подключает только суперадмин — поручения «ведёт отметки» больше нет."""
    prepare()
    Group(tg_id=900).say("/setchat")
    assert not store.get_settings()["report_chat_id"]


def test_supergroup_migration_and_removal():
    teacher, group = setup_group()
    message = dict(group._base(clock.now_utc()), migrate_to_chat_id=-100999)
    group._feed({"update_id": 999, "message": message})
    assert store.get_settings()["report_chat_id"] == -100999
    group.update_id += 1
    bot_user = {"id": 654321, "is_bot": True, "first_name": "Отметки"}
    group._feed({"update_id": 1000, "my_chat_member": {
        "chat": {"id": -100999, "type": "supergroup", "title": "Отметки"}, "date": 1,
        "from": {"id": ADMIN_TG, "is_bot": False, "first_name": "Админ"},
        "old_chat_member": {"status": "member", "user": bot_user},
        "new_chat_member": {"status": "left", "user": bot_user}}})
    assert store.get_settings()["report_chat_id"] == 0


# ── Копии отметок ────────────────────────────────────────────────────────────

def test_checkin_is_copied_with_video_note():
    teacher, group = setup_group()
    before = len(teacher.session.calls)
    teacher.location(at("08:50"))
    teacher.video_note(at("08:51"))
    calls = teacher.session.calls[before:]
    (report,) = sent_to(calls, GROUP)
    assert "Отметка" in report.text and "Учитель" in report.text and "09:00–09:45 5-А — вовремя" in report.text
    (video,) = sent_to(calls, GROUP, SendVideoNote)
    assert video.video_note == "file-vid-1"
    with store.tx() as conn:
        row = conn.execute("SELECT report_chat_id, report_message_id, report_video_id FROM checkins").fetchone()
    assert row[0] == GROUP and row[1] and row[2]
    assert drain(teacher)[1] == [], "второй раз не отправляется"


def test_far_checkin_copy_and_review_card():
    teacher, group = setup_group()
    teacher.location(at("08:55"), lat=LAT + 2000 * METRE)
    before = len(teacher.session.calls)
    teacher.video_note(at("08:56"))
    calls = teacher.session.calls[before:]
    texts = [c.text for c in sent_to(calls, GROUP)]
    assert any("Далеко от школы: 2000 м" in t for t in texts)
    card = next(c for c in sent_to(calls, GROUP) if "На проверке" in c.text)
    assert "далеко от школы" in card.text
    buttons = [b.callback_data for b in card.reply_markup.inline_keyboard[0]]
    assert buttons == [f"ck:approve:{1}", f"ck:reject:{1}"]
    assert len(card.reply_markup.inline_keyboard) == 1, "на http-адресе кнопки «Открыть в панели» нет"


def test_old_checkins_are_not_copied_when_group_connects_late():
    prepare()
    bind_admin()
    teacher = TeacherChat()
    teacher.location(at("08:50"))
    teacher.video_note(at("08:51"))
    clock.freeze(clock.at(MON, "08:51") + timedelta(hours=25))
    Group(session_from=teacher).say("/setchat")
    assert sent_to(drain(teacher)[1], GROUP) == [], "вчерашние отметки в новую группу не сыплются"


# ── Нет отметки: карточка, напоминание, решение ──────────────────────────────

def test_no_checkin_card_and_teacher_reminder():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    result, calls = drain(teacher)
    (card,) = sent_to(calls, GROUP)
    assert card.text.startswith("🔴 <b>Нет отметки</b> · 23.11 09:00–09:45")
    (reminder,) = sent_to(calls, 900)
    assert "Урок в 09:00" in reminder.text and "Я на месте" in reminder.text
    assert result["notifications"] == 2
    assert drain(teacher)[1] == [], "повторный тик ничего не дублирует"


def test_reminder_in_uzbek_and_not_to_blocked():
    prepare(lang="uz")
    teacher = TeacherChat()
    at("09:15")
    service.tick()
    (reminder,) = sent_to(drain(teacher)[1], 900)
    assert "Darsga keldim" in reminder.text


def test_teacher_who_blocked_bot_is_marked():
    prepare()
    teacher = TeacherChat()
    teacher.session.forbidden.add(900)
    at("09:15")
    service.tick()
    drain(teacher)
    assert db.find_user(load_db(), 2)["tg_blocked_at"]
    with store.tx() as conn:
        assert conn.execute("SELECT status FROM notifications WHERE kind = 'late_teacher'").fetchone()[0] == "failed"


def test_approve_from_group():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    drain(teacher)
    calls = group.tap("ck:approve:1", card_message_id(group))
    answer = next(c for c in calls if isinstance(c, AnswerCallbackQuery))
    assert answer.text == "Засчитано"
    assert lesson_status()[0] == "approved"
    edit = next(c for c in calls if isinstance(c, EditMessageText))
    assert "✅ Засчитан — Администратор" in edit.text and edit.reply_markup is None
    detail = service.lesson_detail(1)
    assert detail["history"][-1]["reason"] == "Засчитано в группе «Отметки»"
    with store.tx() as conn:
        assert conn.execute("SELECT status FROM notifications WHERE kind = 'review'").fetchone()[0] == "closed"


def test_only_admins_press_buttons():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    drain(teacher)
    calls = group.tap("ck:approve:1", card_message_id(group), tg_id=900)
    answer = next(c for c in calls if isinstance(c, AnswerCallbackQuery))
    assert answer.show_alert and "только администратор" in answer.text
    assert lesson_status()[0] == "no_checkin"


def test_reject_asks_for_reason():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    drain(teacher)
    calls = group.tap("ck:reject:1", card_message_id(group))
    index = next(i for i, c in enumerate(group.session.calls) if isinstance(c, SendMessage) and "почему" in (c.text or ""))
    prompt_id = group.session.message_ids[index]
    prompt = group.session.calls[index]
    assert prompt.reply_markup.force_reply and "tg://user?id=500" in prompt.text
    assert lesson_status()[0] == "no_checkin", "без причины не решаем"

    stranger = Group(session_from=group, tg_id=777)
    stranger.say("просто так", reply_to_message={"message_id": prompt_id, "date": 1,
                                                  "chat": {"id": GROUP, "type": "supergroup"}, "text": "?"})
    assert lesson_status()[0] == "no_checkin", "ответ постороннего не считается"

    calls = group.say("Не было в школе, директор подтвердил", reply_to_message={
        "message_id": prompt_id, "date": 1, "chat": {"id": GROUP, "type": "supergroup"}, "text": "?"})
    assert lesson_status()[0] == "rejected"
    assert service.lesson_detail(1)["history"][-1]["reason"] == "Не было в школе, директор подтвердил"
    edit = next(c for c in calls if isinstance(c, EditMessageText))
    assert "❌ Не засчитан — Администратор: Не было в школе" in edit.text
    assert "причина записана" in sent_to(calls, GROUP)[-1].text


def test_panel_decision_and_late_checkin_close_cards():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    drain(teacher)
    teacher.location(at("09:20"))
    before = len(teacher.session.calls)
    teacher.video_note(at("09:21"))
    edits = [c for c in teacher.session.calls[before:] if isinstance(c, EditMessageText)]
    assert edits and "Преподаватель отметился, опоздание 20 мин" in edits[0].text
    assert lesson_status()[0] == "late"


def test_decision_in_panel_closes_card_on_next_drain():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    drain(teacher)
    service.decide({"id": 1, "name": "Администратор"}, [1], "reject", "Урок перенесли")
    edits = [c for c in drain(teacher)[1] if isinstance(c, EditMessageText)]
    assert "❌ Не засчитан — Администратор: Урок перенесли" in edits[0].text


def test_stale_cards_are_skipped():
    teacher, group = setup_group()
    at("09:15")
    service.tick()
    with store.tx() as conn:
        conn.execute("UPDATE notifications SET created_at = ?", (clock.iso(clock.now_utc() - timedelta(hours=13)),))
    assert drain(teacher)[1] == []
    with store.tx() as conn:
        assert {r[0] for r in conn.execute("SELECT status FROM notifications")} == {"skipped"}


def test_cards_wait_until_group_is_connected():
    prepare()
    teacher = TeacherChat()
    at("09:15")
    service.tick()
    drain(teacher)
    with store.tx() as conn:
        assert conn.execute("SELECT status FROM notifications WHERE kind = 'review'").fetchone()[0] == "pending"
    bind_admin()
    Group(session_from=teacher).say("/setchat")
    assert any("Нет отметки" in c.text for c in sent_to(drain(teacher)[1], GROUP))


def test_bot_removed_from_group_stops_sending():
    teacher, group = setup_group()
    teacher.session.forbidden.add(GROUP)
    at("09:15")
    service.tick()
    drain(teacher)
    assert store.get_settings()["report_chat_id"] == 0


# ── Сводка за месяц ──────────────────────────────────────────────────────────

def test_monthly_summary_once():
    teacher, group = setup_group()
    teacher.location(at("08:50"))
    teacher.video_note(at("08:51"))
    clock.freeze(clock.at(date(2026, 12, 1), "08:59"))
    assert not drain(teacher)[0]["summary"], "до 09:00 рано"
    clock.freeze(clock.at(date(2026, 12, 1), "09:30"))
    result, calls = drain(teacher)
    assert result["summary"] is True
    summary = sent_to(calls, GROUP)[-1]
    assert "Итоги: ноябрь 2026" in summary.text and "Учитель — 0.75 ч" in summary.text
    (document,) = [c for c in calls if isinstance(c, SendDocument)]
    assert document.document.filename == "hours-2026-11.xlsx"
    assert drain(teacher)[0]["summary"] is False, "раз в месяц"


def test_drain_without_bot_does_nothing():
    assert asyncio.run(notify.drain(None)) == {"skipped": True}


# ── Администратор привязывает Telegram для группы ────────────────────────────

def test_admin_binds_by_code_and_is_not_tracked(teacher):
    """Администратору Telegram нужен для кнопок в группе, а не для отметок."""
    code = db.find_user(load_db(), 1)["bot_code"]
    chat = TeacherChat(tg_id=ADMIN_TG)
    reply = chat.text(f"/start inv_{code}", at("07:00"))
    assert len(reply) == 1 and "/setchat" in reply[0] and "администратор отметок" in reply[0]
    saved = db.find_user(load_db(), 1)
    assert saved["tg_id"] == ADMIN_TG and saved["checkin_tracked"] is False


def test_reason_only_from_who_pressed():
    """Нажал «Не засчитывать» один администратор — причину другого не принимаем."""
    teacher, group = setup_group()

    def _second_admin(database):
        db.find_user(database, 2)["checkin_manager"] = True     # преподаватель 900 — тоже ответственный
    db.transaction(_second_admin)
    at("09:15")
    service.tick()
    drain(teacher)
    group.tap("ck:reject:1", card_message_id(group))
    index = next(i for i, c in enumerate(group.session.calls) if isinstance(c, SendMessage) and "почему" in (c.text or ""))
    prompt_id = group.session.message_ids[index]
    other = Group(session_from=group, tg_id=900)
    other.say("Сам себе засчитаю", reply_to_message={"message_id": prompt_id, "date": 1,
                                                       "chat": {"id": GROUP, "type": "supergroup"}, "text": "?"})
    assert lesson_status()[0] == "no_checkin"
