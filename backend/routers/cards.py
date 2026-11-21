"""Карточки класса с QR-кодами: то, что учитель печатает и раздаёт детям."""
import html
import io
from typing import Any, Dict, List

import qrcode
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from qrcode.image.pil import PilImage

from .. import assets, config, db
from ..security import panel_user, require_teacher, require_teacher_page
from .common import class_or_404, student_or_404

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


@router.get("/api/students/{student_id}/qr.png")
def student_qr(student_id: int, size: int = 10, user: Dict[str, Any] = Depends(require_teacher)):
    student = student_or_404(db.load_db(), student_id, user)

    png = _qr_png(config.deep_link(student["token"]), box_size=max(4, min(20, size)))
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


def _card_html(student: Dict[str, Any], school: str, class_label: str) -> str:
    name = html.escape(student["name"])
    token = html.escape(student["token"])
    base = config.URL_PREFIX
    bound = "вход выполнен" if student.get("tg_id") else "ждёт первого входа"

    # Вход через сайт — для тех, у кого нет Telegram. Пароль печатаем, только
    # пока ребёнок не сменил его сам: после этого он известен лишь ему.
    site = html.escape(config.site_url("/enter").replace("https://", "").replace("http://", ""))
    login = html.escape(student.get("login") or "—")
    shown = student.get("password_shown")
    password = (f"<b>{html.escape(shown)}</b>" if shown
                else "<i>свой пароль</i>")

    return f"""
    <div class="card">
      <div class="card-top">
        <div class="school">{html.escape(school)}</div>
        <div class="class-badge">{html.escape(class_label)}</div>
      </div>
      <div class="name">{name}</div>
      <img class="qr" src="{base}/api/students/{student['id']}/qr.png?size=8" alt="QR-код для входа">
      <div class="code">код: <b>{token}</b></div>
      <div class="hint">Наведи камеру телефона на QR — откроется бот.<br>
        Если камера не читает, отправь боту код выше.</div>
      <div class="web">
        <div class="web-title">Без Telegram — через сайт</div>
        <div class="web-site">{site}</div>
        <div class="web-row"><span>логин</span><b>{login}</b></div>
        <div class="web-row"><span>пароль</span>{password}</div>
      </div>
      <div class="status">{bound}</div>
    </div>
    """


@router.get("/cards/{class_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_teacher_page)])
def class_cards(class_id: int, request: Request):
    """Печатная страница A4 с карточками класса."""
    database = db.load_db()
    cls = class_or_404(database, class_id, panel_user(request))

    school = db.school_name_of_class(database, class_id) or config.SCHOOL_NAME
    students: List[Dict[str, Any]] = db.class_students(database, class_id)
    students.sort(key=lambda s: s["name"].lower())

    if not students:
        body = '<p class="empty">В этом классе пока нет учеников.</p>'
    else:
        body = '<div class="sheet">' + "".join(
            _card_html(s, school, cls["name"]) for s in students
        ) + "</div>"

    # Если имя бота не задано, QR ведёт на сайт — об этом предупреждает лог
    # при запуске; на печатной странице служебных надписей нет.
    warning = ""

    base = config.URL_PREFIX
    title = f"{cls['name']} — {school}"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Карточки: {html.escape(title)}</title>
<link rel="stylesheet" href="{base}{assets.versioned('css/theme.css')}">
<link rel="stylesheet" href="{base}{assets.versioned('css/cards.css')}">
</head>
<body>
<div class="toolbar no-print">
  <div>
    <strong>Карточки класса {html.escape(cls['name'])}</strong>
    <span class="muted">· {html.escape(school)} · учеников: {len(students)}</span>
  </div>
  <div class="toolbar-actions">
    <a class="btn btn-ghost" href="{base}/teacher">В журнал</a>
    <button class="btn" onclick="window.print()">Печать</button>
  </div>
</div>
{warning}
{body}
</body>
</html>""")
