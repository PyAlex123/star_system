"""Карточки класса с QR-кодами: то, что учитель печатает и раздаёт детям.

Вид — по макету docs/design/«Звёзды - Печать.dc.html»: лист А4, 8 карточек
(2×4, 95×69,25 мм), метки реза на полях, двойная рамка, QR 32 мм. Оформление —
тема школы; сам лист всегда светлый (data-light): печать в любой теме белая.
"""
import html
import io
from typing import Any, Dict, List

import qrcode
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from qrcode.image.pil import PilImage

from .. import assets, config, db, themes
from ..security import panel_user, require_teacher, require_teacher_page
from .common import class_or_404, student_or_404

router = APIRouter(tags=["cards"])

ICONS_FILE = config.STATIC_DIR / "img" / "icons.svg"
CARDS_PER_SHEET = 8


def _qr_png(data: str, box_size: int = 10) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=4,          # «тихая зона» в 4 модуля — так QR уверенно читают даже старые камеры
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(image_factory=PilImage, fill_color="#000000", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@router.get("/api/students/{student_id}/qr.png")
def student_qr(student_id: int, size: int = 10, user: Dict[str, Any] = Depends(require_teacher)):
    student = student_or_404(db.load_db(), student_id, user)
    png = _qr_png(config.deep_link(student["token"]), box_size=max(4, min(20, size)))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


# Метки реза на полях листа: вертикали по краям колонок, горизонтали по краям рядов
_MARKS = "".join(
    f'<span class="mark" style="left:{x - 0.1:g}mm;top:{t}mm;width:0.2mm;height:5mm"></span>'
    for x in (10, 105, 200) for t in (3, 289)) + "".join(
    f'<span class="mark" style="left:{left}mm;top:{y - 0.1:g}mm;width:5mm;height:0.2mm"></span>'
    for y in (10, 79.25, 148.5, 217.75, 287) for left in (3, 202))

_SKY_POINTS = ((6, 52), (10, 22), (24, 38), (34, 12), (46, 34), (56, 6), (66, 34), (78, 12), (90, 24), (94, 52))
_SKY_MARK = ('<svg class="deco-sky" aria-hidden="true" viewBox="0 0 100 60"><polygon points="'
             + " ".join(f"{x},{y}" for x, y in _SKY_POINTS) + '"/>'
             + "".join(f'<circle cx="{x}" cy="{y}" r="4.2"/>' for x, y in _SKY_POINTS) + "</svg>")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _card_html(student: Dict[str, Any], school: str, class_label: str, site: str) -> str:
    """Одна карточка: школа и класс, имя, QR, код 4+4, вход через сайт.
    Украшение темы — печать (тетрадь), лента (квест), созвездие, скотч (стикербук)."""
    name = html.escape(student["name"])
    token = html.escape(student["token"])
    base = config.URL_PREFIX
    login = html.escape(student.get("login") or "—")
    # Пароль печатаем, только пока ребёнок не сменил его сам: после этого он известен лишь ему
    shown = student.get("password_shown")
    password = f"<b>{html.escape(shown)}</b>" if shown else "<i>свой пароль</i>"
    cls = html.escape(class_label)
    long_login = " long" if len(student.get("login") or "") > 20 else ""
    size = " xlong" if len(student["name"]) > 28 else " long" if len(student["name"]) > 22 else ""
    return f"""
    <div class="cell" data-id="{student['id']}">
      <button class="pick no-print" type="button" aria-pressed="true" aria-label="Убрать из печати: {name}"
              onclick="togglePick(this)"><span class="box"><svg class="ic"><use href="#i-check"></use></svg></span></button>
      <div class="frame"><div class="card">
        <div class="deco-nb" aria-hidden="true"><span><svg class="ic"><use href="#i-star"></use></svg></span></div>
        {_SKY_MARK}
        <div class="deco-stk" aria-hidden="true"><span>{cls}</span></div>
        <div class="deco-q" aria-hidden="true">ПРОПУСК В ИГРУ «ЗВЁЗДЫ»</div>
        <div class="c-top"><span class="c-school">{html.escape(school)}</span><span class="c-class">{cls}</span></div>
        <div class="c-name{size}" title="{name}">{name}</div>
        <div class="c-row">
          <img class="qr" src="{base}/api/students/{student['id']}/qr.png?size=10" alt="QR-код входа: {name}">
          <div class="c-side">
            <span class="c-caps">Код для бота</span>
            <span class="c-code" aria-label="{token}">{token[:4]}<span class="gap"></span>{token[4:]}</span>
            <span class="c-hint">Наведи камеру на QR — откроется бот. Если камера не читает, отправь боту код.</span>
            <span class="c-web">
              <span class="c-caps">Без Telegram — через сайт</span>
              <span class="c-site">{site}</span>
              <span class="c-cred{long_login}">логин <b>{login}</b></span>
              <span class="c-cred">пароль {password}</span>
            </span>
          </div>
        </div>
      </div></div>
    </div>"""


_SCRIPT = """
<script>
function setBw(on) {
  document.getElementById("sheets")?.classList.toggle("bw", on);
  document.getElementById("modeColor").setAttribute("aria-checked", String(!on));
  document.getElementById("modeBw").setAttribute("aria-checked", String(on));
}
function pickedCount() { return document.querySelectorAll('.cell[data-id]:not([data-unsel="1"])').length; }
function updatePickLabel() {
  const on = document.body.classList.contains("picking");
  document.getElementById("pickLabel").textContent =
    on ? "Выбрано: " + pickedCount() + " · печатать всех" : "Печатать выбранных";
}
function togglePickMode() {
  const on = !document.body.classList.contains("picking");
  document.body.classList.toggle("picking", on);
  if (!on) document.querySelectorAll(".cell[data-unsel]").forEach((c) => c.removeAttribute("data-unsel"));
  document.querySelectorAll(".pick").forEach((b) => b.setAttribute("aria-pressed", "true"));
  updatePickLabel();
}
function togglePick(button) {
  const cell = button.closest(".cell");
  const off = cell.getAttribute("data-unsel") !== "1";
  if (off) cell.setAttribute("data-unsel", "1"); else cell.removeAttribute("data-unsel");
  button.setAttribute("aria-pressed", String(!off));
  updatePickLabel();
}
</script>"""


@router.get("/cards/{class_id}", response_class=HTMLResponse,
            dependencies=[Depends(require_teacher_page)])
def class_cards(class_id: int, request: Request):
    """Печатная страница: листы А4 по 8 карточек (2×4) с метками реза."""
    database = db.load_db()
    cls = class_or_404(database, class_id, panel_user(request))

    school = db.school_name_of_class(database, class_id) or config.SCHOOL_NAME
    theme = themes.theme_of_class(database, class_id)
    students: List[Dict[str, Any]] = db.class_students(database, class_id)
    students.sort(key=lambda s: s["name"].lower())
    site = html.escape(config.site_url("/enter").replace("https://", "").replace("http://", ""))

    sheets = []
    for start in range(0, len(students), CARDS_PER_SHEET):
        chunk = students[start:start + CARDS_PER_SHEET]
        cells = "".join(_card_html(s, school, cls["name"], site) for s in chunk)
        cells += '<div class="cell blank"></div>' * (CARDS_PER_SHEET - len(chunk))
        sheets.append(f'<section class="a4"><div class="a4-grid">{cells}</div>{_MARKS}</section>')

    count = len(students)
    pages = len(sheets)
    if students:
        body = '<div class="sheets" id="sheets" data-light>' + "".join(sheets) + "</div>"
        sheets_text = f" · {pages} {_plural(pages, 'лист', 'листа', 'листов')} А4, по {CARDS_PER_SHEET} карточек"
    else:
        body = '<p class="empty">В этом классе пока нет учеников.</p>'
        sheets_text = ""

    base = config.URL_PREFIX
    title = html.escape(f"{cls['name']} — {school}")
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru" {themes.html_attrs(theme, themes.page_mode(request))}>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Карточки: {title}</title>
<link rel="stylesheet" href="{base}{assets.versioned('css/tokens.css')}">
<link rel="stylesheet" href="{base}{assets.versioned('css/theme.css')}">
<link rel="stylesheet" href="{base}{assets.versioned('css/cards.css')}">
</head>
<body class="print-page">
{ICONS_FILE.read_text(encoding="utf-8")}
<div class="ptoolbar no-print">
  <div class="ptoolbar-in">
    <div class="ptitle">
      <span class="ptitle-main">Карточки класса {html.escape(cls['name'])}</span>
      <span class="ptitle-sub">{html.escape(school)} · учеников: {count}{sheets_text}</span>
    </div>
    <div class="seg" role="radiogroup" aria-label="Превью печати">
      <button type="button" role="radio" aria-checked="true" id="modeColor" onclick="setBw(false)">Цвет</button>
      <button type="button" role="radio" aria-checked="false" id="modeBw" onclick="setBw(true)">Ч/б-превью</button>
    </div>
    <a class="btn btn-ghost btn-md" href="{base}/teacher"><svg class="ic ic-sm"><use href="#i-arrow-left"></use></svg>В журнал</a>
    <button type="button" class="btn btn-ghost btn-md" id="pickBtn" onclick="togglePickMode()">
      <svg class="ic ic-sm"><use href="#i-square-check"></use></svg><span id="pickLabel">Печатать выбранных</span></button>
    <button type="button" class="btn btn-md" onclick="window.print()"><svg class="ic ic-sm"><use href="#i-printer"></use></svg>Печать</button>
  </div>
</div>
{body}
{_SCRIPT}
</body>
</html>""")
