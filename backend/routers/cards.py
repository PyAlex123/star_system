"""Карточки класса с QR-кодами: то, что учитель печатает и раздаёт детям."""
import html
import io
from typing import Any, Dict, List

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from qrcode.image.pil import PilImage

from .. import config, db
from ..security import require_teacher, require_teacher_page

router = APIRouter(tags=["cards"])


def _qr_png(data: str, box_size: int = 10) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(image_factory=PilImage, fill_color="#111827", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@router.get("/api/students/{student_id}/qr.png", dependencies=[Depends(require_teacher)])
def student_qr(student_id: int, size: int = 10):
    student = db.find_student(db.load_db(), student_id)
    if not student:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")

    png = _qr_png(config.deep_link(student["token"]), box_size=max(4, min(20, size)))
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


def _card_html(student: Dict[str, Any], school: str) -> str:
    name = html.escape(student["name"])
    class_name = html.escape(student["class_name"])
    token = html.escape(student["token"])
    base = config.URL_PREFIX
    bound = "✅ вход выполнен" if student.get("tg_id") else "ждёт первого входа"
    return f"""
    <div class="card">
      <div class="card-top">
        <div class="school">{html.escape(school)}</div>
        <div class="class-badge">{class_name}</div>
      </div>
      <div class="name">{name}</div>
      <img class="qr" src="{base}/api/students/{student['id']}/qr.png?size=8" alt="QR-код для входа">
      <div class="code">код: <b>{token}</b></div>
      <div class="hint">Наведи камеру телефона на QR — откроется бот.<br>
        Если камера не читает, отправь боту код выше.</div>
      <div class="status">{bound}</div>
    </div>
    """


@router.get("/cards/{class_name}", response_class=HTMLResponse,
            dependencies=[Depends(require_teacher_page)])
def class_cards(class_name: str):
    """Печатная страница A4: 8 карточек на лист."""
    database = db.load_db()
    students: List[Dict[str, Any]] = [s for s in database["students"] if s["class_name"] == class_name]
    students.sort(key=lambda s: s["name"].lower())

    if not students:
        body = '<p class="empty">В этом классе пока нет учеников.</p>'
    else:
        body = '<div class="sheet">' + "".join(
            _card_html(s, config.SCHOOL_NAME) for s in students
        ) + "</div>"

    warning = ""
    if not config.BOT_USERNAME:
        warning = ('<div class="warn no-print">⚠ В файле .env не заполнено <b>BOT_USERNAME</b>. '
                   'QR-коды пока ведут на сайт, а не в бота. Заполните имя бота и распечатайте заново.</div>')

    base = config.URL_PREFIX
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Карточки класса {html.escape(class_name)}</title>
<link rel="stylesheet" href="{base}/static/css/theme.css">
<link rel="stylesheet" href="{base}/static/css/cards.css">
</head>
<body>
<div class="toolbar no-print">
  <div>
    <strong>Карточки класса {html.escape(class_name)}</strong>
    <span class="muted">· учеников: {len(students)}</span>
  </div>
  <div class="toolbar-actions">
    <a class="btn btn-ghost" href="{base}/">← В журнал</a>
    <button class="btn" onclick="window.print()">🖨 Печать</button>
  </div>
</div>
{warning}
{body}
</body>
</html>""")
