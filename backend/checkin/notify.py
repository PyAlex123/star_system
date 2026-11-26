"""Отправка в Telegram — всё, что копится в очереди сервиса (service.outbox):

  * копия каждой отметки в группу «Отметки»: текст, ответом на него — кругляшок;
  * карточка урока без отметки или со спорной отметкой — с кнопками решения;
  * карточка закрывается (без кнопок, с итогом), когда урок решён — в группе или в панели;
  * напоминание преподавателю, что урок идёт, а отметки нет;
  * 1-го числа — сводка за прошлый месяц и Excel-отчёт.

drain() зовут планировщик раз в минуту и бот сразу после отметки. Одновременно
работает только один drain (блокировка), поэтому одно и то же не уходит дважды.
Карточки для группы — по-русски: её читают администраторы.
"""
import asyncio
import html
import logging
import weakref
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

from .. import config, excel
from . import clock, service
from .locales import lang_of, month_title, t

log = logging.getLogger("stars.checkin.notify")

SEND_PAUSE_SEC = 0.05            # Telegram терпит ~30 сообщений в секунду; в группу — меньше
MAX_TEACHER_LINES = 40           # длинная сводка обрезается — полный список в файле

_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _lock() -> asyncio.Lock:
    """Своя блокировка на каждый цикл событий: одна и та же карточка не уйдёт дважды,
    даже если drain позвали одновременно планировщик и бот."""
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    return _locks[loop]


def e(value: Any) -> str:
    return html.escape(str(value or ""))


def money(value: Optional[int]) -> str:
    return f"{value:,}".replace(",", " ") + " сум" if value is not None else ""


def hours_text(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def short_day(iso: str) -> str:
    _, m, d = iso.split("-")
    return f"{d}.{m}"


# ── Тексты ────────────────────────────────────────────────────────────────────

HEADS = {
    "no_checkin": "🔴 <b>Нет отметки</b>",
    "needs_review": "🟡 <b>На проверке</b>",
    "approved": "✅ <b>Засчитан</b>",
    "rejected": "❌ <b>Не засчитан</b>",
    "checked_in": "🟢 <b>Отметился</b>",
    "late": "🟠 <b>Отметился с опозданием</b>",
    "cancelled": "⚪ <b>Урок отменён</b>",
    "planned": "⏳ <b>Урок впереди</b>",
}


def card_text(lesson: Dict[str, Any], decision: str = "") -> str:
    """Карточка урока в группе. decision — строка итога, когда карточка закрывается."""
    lines = [f"{HEADS.get(lesson['status'], e(lesson['status_label']))} · {short_day(lesson['date'])} "
             f"{lesson['start']}–{lesson['end']}",
             f"👤 {e(lesson['teacher'])}",
             f"🏫 {e(lesson['school'])}, {e(lesson['class'])}"]
    if lesson.get("original_teacher"):
        lines.append(f"🔁 Замена за {e(lesson['original_teacher'])}")
    if lesson.get("flag_labels"):
        lines.append("Причина: " + ", ".join(e(f) for f in lesson["flag_labels"]))
    checkin = lesson.get("checkin")
    if checkin:
        extra = f", {checkin['distance_m']} м от школы" if checkin.get("distance_m") is not None else ""
        video = "" if checkin.get("video") else ", без кругляшка"
        lines.append(f"📍 Отметка в {checkin['at']}{extra}{video}")
    if lesson.get("late_minutes"):
        lines.append(f"⏰ Опоздание: {lesson['late_minutes']} мин")
    if lesson.get("stars_given"):
        lines.append("⭐ В журнале звёзд урок проведён")
    if decision:
        lines.append("")
        lines.append(decision)
    return "\n".join(lines)


def decision_line(lesson: Dict[str, Any], reason: str = "") -> str:
    who = f" — {e(lesson['reviewed_by'])}" if lesson.get("reviewed_by") else ""
    why = f": {e(reason)}" if reason else ""
    status = lesson["status"]
    if status == "approved":
        return f"✅ Засчитан{who}{why}"
    if status == "rejected":
        return f"❌ Не засчитан{who}{why}"
    if status == "cancelled":
        return f"⚪ Урок отменён{who}{why}"
    if status in ("checked_in", "late"):
        late = f", опоздание {lesson['late_minutes']} мин" if lesson.get("late_minutes") else ", вовремя"
        return f"🟢 Преподаватель отметился{late}"
    return f"Статус: {e(lesson['status_label'])}"


def card_keyboard(lesson_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✅ Засчитать", callback_data=f"ck:approve:{lesson_id}"),
             InlineKeyboardButton(text="❌ Не засчитывать", callback_data=f"ck:reject:{lesson_id}")]]
    if config.IS_HTTPS:
        # Telegram принимает в кнопках только настоящие адреса; локально кнопки нет
        rows.append([InlineKeyboardButton(text="🔁 Открыть в панели",
                                          url=config.site_url(f"/teacher#checkin-lesson-{lesson_id}"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_text(report: Dict[str, Any]) -> str:
    checkin = report["checkin"]
    at = clock.hhmm(clock.parse(checkin["created_at"]))
    lines = [f"📍 <b>Отметка</b> · {at} · {e(report['teacher'])}"]
    if report["school"]:
        lines.append(f"🏫 {e(report['school'])}")
    for lesson in report["lessons"]:
        if lesson["status"] == "late":
            state = f"опоздание {lesson['late_minutes']} мин"
        elif lesson["status"] == "needs_review":
            state = "на проверке: " + ", ".join(e(f) for f in lesson["flag_labels"])
        elif lesson["status"] == "checked_in":
            state = "вовремя"
        else:
            state = e(lesson["status_label"])
        lines.append(f"• {lesson['start']}–{lesson['end']} {e(lesson['class'])} — {state}")
    geo, distance = checkin.get("geo"), checkin.get("distance_m")
    if geo == "ok":
        lines.append(f"📏 {distance} м от школы")
    elif geo == "far":
        lines.append(f"⚠️ Далеко от школы: {distance} м")
    elif geo == "no_coords":
        lines.append("ℹ️ У школы нет точки на карте")
    if checkin["status"] == "no_video":
        lines.append("❌ Кругляшок не прислан")
    return "\n".join(lines)


def summary_text(report: Dict[str, Any], year: int, month: int) -> str:
    totals = report["totals"]
    lines = [f"📊 <b>Итоги: {month_title('ru', year, month)}</b>",
             f"Уроков по плану: {totals['lessons']} · подтверждено: {totals['confirmed']} · "
             f"опозданий: {totals['late']} · ждут решения: {totals['pending']}",
             f"Часы: {hours_text(totals['hours'])}" + (f" · к выплате: {money(totals['amount'])}"
                                                       if totals.get("amount") is not None else ""),
             ""]
    teachers = report["teachers"]
    for row in teachers[:MAX_TEACHER_LINES]:
        parts = [f"{hours_text(row['hours'])} ч"]
        if row["late"]:
            parts.append(f"опозданий {row['late']}")
        if row["pending"]:
            parts.append(f"ждут решения {row['pending']}")
        if row.get("amount") is not None:
            parts.append(money(row["amount"]))
        lines.append(f"• {e(row['name'])} — " + ", ".join(parts))
    if len(teachers) > MAX_TEACHER_LINES:
        lines.append(f"… и ещё {len(teachers) - MAX_TEACHER_LINES} — в файле")
    if report["totals"]["pending"]:
        lines.append("")
        lines.append("Уроки, которые ждут решения, войдут в часы, когда их засчитают.")
    return "\n".join(lines)


# ── Отправка ──────────────────────────────────────────────────────────────────

async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def refresh_card(bot: Bot, lesson_id: int, reason: str = "", extra_message: Any = None) -> None:
    """Закрыть карточки урока: итог вместо кнопок. extra_message — карточка, на которой нажали
    кнопку, если её нет среди отправленных очередью (например, старая)."""
    notes = await _to_thread(service.close_cards, lesson_id)
    lesson = await _to_thread(service.card_for, lesson_id)
    text = card_text(lesson, decision_line(lesson, reason))
    targets = {(n["chat_id"], n["message_id"]) for n in notes if n.get("message_id")}
    if extra_message is not None:
        targets.add((extra_message.chat.id, extra_message.message_id))
    for chat_id, message_id in targets:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=None)
        except TelegramAPIError as exc:
            log.warning("Карточка урока %s не обновилась: %s", lesson_id, exc)


async def _send_reports(bot: Bot, chat_id: int, reports: List[Dict[str, Any]]) -> int:
    sent = 0
    for report in reports:
        checkin = report["checkin"]
        message = await bot.send_message(chat_id, report_text(report))
        video_id = None
        if checkin.get("video_file_id"):
            video = await bot.send_video_note(chat_id, checkin["video_file_id"],
                                              reply_parameters=ReplyParameters(message_id=message.message_id))
            video_id = video.message_id
        await _to_thread(service.mark_report, checkin["id"], chat_id, message.message_id, video_id)
        sent += 1
        await asyncio.sleep(SEND_PAUSE_SEC)
    return sent


async def _send_notifications(bot: Bot, chat_id: int, items: List[Dict[str, Any]]) -> int:
    sent = 0
    for item in items:
        note, lesson, teacher = item["note"], item["lesson"], item["teacher"]
        if note["kind"] == "review":
            message = await bot.send_message(chat_id, card_text(lesson), reply_markup=card_keyboard(lesson["id"]))
            await _to_thread(service.mark_notification, note["id"], "sent", chat_id, message.message_id)
            sent += 1
        elif note["kind"] == "late_teacher":
            if not teacher.get("tg_id") or teacher.get("tg_blocked_at") or not teacher.get("consent_at"):
                await _to_thread(service.mark_notification, note["id"], "skipped")
                continue
            lang = lang_of(teacher.get("lang"))
            text = t(lang, "late_reminder", time=lesson["start"], school=e(lesson["school"]), cls=e(lesson["class"]))
            try:
                message = await bot.send_message(teacher["tg_id"], text)
            except TelegramForbiddenError:
                await _to_thread(service.mark_blocked_by_user_id, teacher["id"])
                await _to_thread(service.mark_notification, note["id"], "failed")
                continue
            await _to_thread(service.mark_notification, note["id"], "sent", teacher["tg_id"], message.message_id)
            sent += 1
        await asyncio.sleep(SEND_PAUSE_SEC)
    return sent


async def _close_cards(bot: Bot, items: List[Dict[str, Any]]) -> int:
    for item in items:
        lesson_id = item["note"]["lesson_id"]
        detail = await _to_thread(service.lesson_detail, lesson_id)
        reason = next((h["reason"] for h in reversed(detail.get("history", [])) if h.get("reason")), "")
        await refresh_card(bot, lesson_id, reason)
    return len(items)


async def _send_summary(bot: Bot, chat_id: int) -> bool:
    due = await _to_thread(service.summary_due)
    if not due:
        return False
    year, month = due
    report = await _to_thread(service.month_report, year, month)
    await bot.send_message(chat_id, summary_text(report, year, month))
    name = f"hours-{year:04d}-{month:02d}.xlsx"
    data = await _to_thread(excel.build_hours_report, report, f"Часы преподавателей {year:04d}-{month:02d}")
    await bot.send_document(chat_id, BufferedInputFile(data, filename=name))
    await _to_thread(service.mark_summary, year, month)
    return True


async def drain(bot: Optional[Bot]) -> Dict[str, Any]:
    """Отправить всё, что накопилось. Без бота — ничего: очередь подождёт (и устареет)."""
    if bot is None:
        return {"skipped": True}
    async with _lock():
        box = await _to_thread(service.outbox)
        chat_id = box["chat_id"]
        result = {"reports": 0, "notifications": 0, "closed": 0, "summary": False}
        try:
            # Сначала личные напоминания — они не зависят от группы
            personal = [i for i in box["notifications"] if i["note"]["kind"] != "review"]
            result["notifications"] += await _send_notifications(bot, chat_id, personal)
            if chat_id:
                try:
                    result["reports"] = await _send_reports(bot, chat_id, box["reports"])
                    cards = [i for i in box["notifications"] if i["note"]["kind"] == "review"]
                    result["notifications"] += await _send_notifications(bot, chat_id, cards)
                    result["closed"] = await _close_cards(bot, box["closing"])
                    result["summary"] = await _send_summary(bot, chat_id)
                except TelegramForbiddenError:
                    # Бота убрали из группы — перестаём туда писать, в панели будет видно
                    log.warning("Группа «Отметки» недоступна — бот больше не участник")
                    await _to_thread(service.drop_report_chat, chat_id)
        except TelegramRetryAfter as exc:
            log.warning("Telegram просит подождать %s с — остальное отправим в следующий раз", exc.retry_after)
        except TelegramAPIError:
            log.exception("Отправка в Telegram не удалась — повторим в следующий раз")
        return result
