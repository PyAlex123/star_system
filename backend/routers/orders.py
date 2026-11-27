"""Покупки учеников (схема 13): ученик купил приз в кабинете — звёзды уже списаны,
приз ждёт выдачи. Учитель школы ученика (или администратор) отмечает «Выдан» или
отменяет покупку — тогда звёзды возвращаются, а запись истории покупки исчезает,
как при отмене начисления за урок. Положительной записи «возврат» нет: она считалась бы
заработком и подняла бы место в рейтинге."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from .. import db, schemas
from ..security import require_teacher
from .common import TEACH, bad_request, not_found, student_or_404

router = APIRouter(prefix="/api/orders", tags=["orders"], dependencies=[Depends(require_teacher)])


def find_order(database: Dict[str, Any], order_id: int) -> Optional[Dict[str, Any]]:
    return next((o for o in database.get("orders", []) if o.get("id") == order_id), None)


def _order_or_404(database: Dict[str, Any], order_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    """Покупка ученика, которому этот учитель выдаёт призы. Чужая — как несуществующая."""
    order = find_order(database, order_id)
    if not order:
        raise not_found("Покупка не найдена")
    student_or_404(database, order["student_id"], user, need=TEACH)
    if order.get("status") != db.ORDER_WAITING:
        raise bad_request("Эта покупка уже закрыта: приз выдан или покупка отменена")
    return order


def _close(order: Dict[str, Any], status: str, user: Dict[str, Any]) -> None:
    order["status"] = status
    order["closed_at"] = db.now_iso()
    order["closed_by"] = user["id"]
    order["closed_by_name"] = user.get("name") or user.get("login", "")


@router.get("/waiting")
def waiting_orders(user: Dict[str, Any] = Depends(require_teacher)):
    """Сколько призов ждут выдачи — для счётчика в меню. Только ученики своих школ."""
    database = db.load_db()
    mine = []
    for order in database.get("orders", []):
        if order.get("status") != db.ORDER_WAITING:
            continue
        student = db.find_student(database, order.get("student_id"))
        school = db.school_of_student(database, student)
        if school and db.teaches_in(user, school["id"]):
            mine.append(order)
    return {"orders": mine}


@router.post("/{order_id}/issue")
def issue_order(order_id: int, user: Dict[str, Any] = Depends(require_teacher)):
    def _apply(database: Dict[str, Any]):
        order = _order_or_404(database, order_id, user)
        _close(order, db.ORDER_ISSUED, user)
        return dict(order)

    return {"ok": True, "order": db.transaction(_apply)}


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, item: schemas.OrderCancel, user: Dict[str, Any] = Depends(require_teacher)):
    """Отменить покупку: приза нет или купили по ошибке. Звёзды возвращаются ученику."""
    def _apply(database: Dict[str, Any]):
        order = _order_or_404(database, order_id, user)
        student = db.find_student(database, order["student_id"])
        if not student:
            raise not_found("Ученик не найден")
        student["total_stars"] = int(student.get("total_stars", 0)) + int(order["cost"])
        history = student.get("history", [])
        student["history"] = [h for h in history if h.get("order_id") != order["id"]]
        _close(order, db.ORDER_CANCELLED, user)
        order["cancel_reason"] = (item.reason or "").strip()
        return dict(order)

    return {"ok": True, "order": db.transaction(_apply)}
