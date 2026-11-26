"""Раздел «Отметки преподавателей» в панели: сегодня, очередь, решения, календарь,
настройки, отчёт и журнал правок.

Весь роутер — только администратор (require_checkin_admin), в том числе чтение: там часы,
ставки и суммы. Кабинет преподавателя (свои уроки и часы, без денег) — отдельный my_router:
его открывает любой сотрудник, и только про себя. Логика — в backend/checkin/service.py.
"""
import re
import secrets
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from .. import config, db, schemas
from ..checkin import clock, service
from ..excel import build_hours_report
from ..security import require_checkin_admin, require_panel
from .common import bad_request, not_found
from .excel import _xlsx

router = APIRouter(prefix="/api/checkin", tags=["checkin"], dependencies=[Depends(require_checkin_admin)])
my_router = APIRouter(prefix="/api/checkin/my", tags=["checkin"], dependencies=[Depends(require_panel)])

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _call(func, *args, **kwargs):
    """Ошибки сервиса — понятным текстом: «не найдено» — 404, остальное — 400."""
    try:
        return func(*args, **kwargs)
    except service.NotFound as exc:
        raise not_found(str(exc))
    except service.CheckinError as exc:
        raise bad_request(str(exc))


def _day(value: Optional[str]) -> date:
    if not value:
        return clock.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise bad_request("Дата должна быть в формате ГГГГ-ММ-ДД")


def _month(value: Optional[str]):
    if not value:
        today = clock.today()
        return today.year, today.month
    match = _MONTH.match(value)
    if not match:
        raise bad_request("Месяц должен быть в формате ГГГГ-ММ")
    return int(match.group(1)), int(match.group(2))


@router.get("/overview")
def overview():
    return service.overview()


@router.get("/day")
def day(date: Optional[str] = None):
    return service.day_view(_day(date))


@router.get("/queue")
def queue():
    return {"lessons": service.queue()}


@router.get("/lessons/{lesson_id}")
def lesson(lesson_id: int):
    return _call(service.lesson_detail, lesson_id)


@router.post("/lessons/decide")
def decide(item: schemas.CheckinDecision, user: Dict[str, Any] = Depends(require_checkin_admin)):
    lessons = _call(service.decide, user, item.lesson_ids, item.action, item.reason)
    return {"ok": True, "count": len(lessons)}


@router.patch("/lessons/{lesson_id}")
def set_hours(lesson_id: int, item: schemas.CheckinHours, user: Dict[str, Any] = Depends(require_checkin_admin)):
    _call(service.set_hours, user, lesson_id, item.hours, item.reason)
    return {"ok": True}


@router.post("/lessons")
def add_lesson(item: schemas.CheckinLesson, user: Dict[str, Any] = Depends(require_checkin_admin)):
    lesson = _call(service.add_lesson, user, item.user_id, item.class_id, item.date, item.start, item.end,
                   item.room, item.reason)
    return {"ok": True, "lesson_id": lesson["id"]}


@router.get("/exceptions")
def exceptions(date_from: Optional[str] = None, date_to: Optional[str] = None):
    start = _day(date_from) if date_from else None
    end = _day(date_to) if date_to else None
    if (start is None) != (end is None):
        raise bad_request("Укажите обе даты периода")
    return {"exceptions": service.list_exceptions(start, end)}


@router.post("/exceptions")
def add_exception(item: schemas.CheckinException, user: Dict[str, Any] = Depends(require_checkin_admin)):
    row = _call(service.add_exception, user, item.model_dump())
    return {"ok": True, "exception": row}


@router.delete("/exceptions/{exception_id}")
def delete_exception(exception_id: int, user: Dict[str, Any] = Depends(require_checkin_admin)):
    _call(service.delete_exception, user, exception_id)
    return {"ok": True}


@router.get("/settings")
def get_settings():
    return service.settings_view()


@router.put("/settings")
def put_settings(item: schemas.CheckinSettings, user: Dict[str, Any] = Depends(require_checkin_admin)):
    return _call(service.update_settings, user, item.values)


@router.get("/report")
def report(month: Optional[str] = None):
    year, number = _month(month)
    return _call(service.month_report, year, number)


@router.get("/report.xlsx")
def report_xlsx(month: Optional[str] = None):
    year, number = _month(month)
    data = _call(service.month_report, year, number)
    name = f"Часы преподавателей {year:04d}-{number:02d}.xlsx"
    return _xlsx(build_hours_report(data, name[:-5]), name, fallback=f"hours-{year:04d}-{number:02d}.xlsx")


@router.get("/audit")
def audit(before_id: Optional[int] = None, limit: int = 50):
    return {"items": service.audit_page(before_id, limit)}


@router.get("/teachers")
def teachers():
    return {"teachers": service.teachers()}


@router.put("/schools/{school_id}/location")
def school_location(school_id: int, item: schemas.SchoolLocation,
                    user: Dict[str, Any] = Depends(require_checkin_admin)):
    school = _call(service.set_school_location, user, school_id, item.address, item.lat, item.lon, item.radius_m)
    return {"ok": True, "school": {k: school.get(k) for k in ("id", "name", "address", "lat", "lon", "radius_m")}}


# ── Кабинет преподавателя: только свои данные ─────────────────────────────────

def _telegram(user: Dict[str, Any]) -> Dict[str, Any]:
    return {"bound": bool(user.get("tg_id")), "username": user.get("tg_username"),
            "blocked": bool(user.get("tg_blocked_at")), "consent": bool(user.get("consent_at"))}


@my_router.get("")
def my_work(month: Optional[str] = None, user: Dict[str, Any] = Depends(require_panel)):
    """Мои уроки сегодня и за месяц: статус отметки, часы, опоздания. Ставки и сумм нет."""
    year, number = _month(month)
    data = _call(service.teacher_cabinet, user["id"], year, number)
    data.update(tracked=bool(user.get("checkin_tracked")), telegram=_telegram(user),
                bot={"ready": bool(config.BOT_ENABLED and config.BOT_USERNAME), "username": config.BOT_USERNAME or None})
    return data


@my_router.post("/invite")
def my_invite(user: Dict[str, Any] = Depends(require_panel)):
    """Ссылка-приглашение в бот — себе. Только тем, у кого администратор включил отметки:
    кто отмечается, решает администратор, а привязать свой Telegram сотрудник может сам."""
    if not user.get("checkin_tracked"):
        raise bad_request("Отметки для вас не включены — их включает администратор")
    if user.get("tg_id"):
        raise bad_request("Telegram уже привязан")
    link_code = secrets.token_urlsafe(9)
    before: Dict[str, Any] = {}

    def _apply(database: Dict[str, Any]):
        me = db.find_user(database, user["id"])
        before.update(me)
        me["invite_code"] = link_code
        me["invite_created_at"] = clock.iso(clock.now_utc())
        return me

    after = db.transaction(_apply)
    service.audit_staff(user, user["id"], "invite", before, after)
    link = config.checkin_invite_link(link_code)
    return {"ok": True, "link": link or None, "days": config.CHECKIN_INVITE_DAYS}
