"""Темы оформления: у каждой школы своя.

Пять тем из docs/design/HANDOFF-Zvezdy.md. Значения цветов и шрифтов живут
только в static/css/tokens.css; здесь — какие темы бывают и какую тему
показать на странице, чтобы сервер сразу отдал <html data-theme="…">
и при загрузке не мигала чужая тема.

Cookie zv_theme / zv_mode / zv_school хранят только внешний вид на этом
устройстве: подделать в них можно лишь цвет собственной страницы.
"""
from typing import Any, Dict, Optional

THEMES = ("notebook", "quest", "sky", "clear", "sticker")
THEME_NAMES = {
    "notebook": "Тетрадь отличника",
    "quest": "Звёздный квест",
    "sky": "Созвездие",
    "clear": "Ясно и быстро",
    "sticker": "Стикербук",
}
DEFAULT_THEME = "notebook"

# Светлая/тёмная есть только у «Ясно и быстро». auto = как в системе или Telegram.
MODES = ("auto", "light", "dark")
DEFAULT_MODE = "auto"

THEME_COOKIE = "zv_theme"
MODE_COOKIE = "zv_mode"
SCHOOL_COOKIE = "zv_school"


def clean_theme(value: Optional[str]) -> Optional[str]:
    return value if value in THEMES else None


def clean_mode(value: Optional[str]) -> Optional[str]:
    return value if value in MODES else None


def school_theme(school: Optional[Dict[str, Any]]) -> str:
    return clean_theme((school or {}).get("theme")) or DEFAULT_THEME


def theme_of_class(db: Dict[str, Any], class_id: Optional[int]) -> str:
    from . import db as store
    cls = store.find_class(db, class_id) if class_id else None
    school = store.find_school(db, cls["school_id"]) if cls else None
    return school_theme(school)


def first_school_theme(db: Dict[str, Any]) -> str:
    schools = db.get("schools") or []
    return school_theme(schools[0] if schools else None)


# ── Какую тему показать на странице ──────────────────────────────────────────

def _cookie_theme(request) -> Optional[str]:
    return clean_theme(request.cookies.get(THEME_COOKIE))


def page_mode(request) -> str:
    return clean_mode(request.cookies.get(MODE_COOKIE)) or DEFAULT_MODE


def device_theme(request, db: Dict[str, Any]) -> str:
    """Страницы до входа (/, /login, /enter): последняя тема на этом устройстве,
    иначе тема первой школы."""
    return _cookie_theme(request) or first_school_theme(db)


def student_page_theme(request, db: Dict[str, Any]) -> str:
    """Кабинет ученика: ?theme= из кнопки бота или ссылки входа → cookie → первая школа.
    После загрузки кабинет сверяет тему с ответом /api/me."""
    return clean_theme(request.query_params.get("theme")) or device_theme(request, db)


def panel_school(request, db: Dict[str, Any], user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Школа, с которой учитель работал последней на этом устройстве (cookie),
    если она ему доступна; иначе первая из его школ."""
    from . import db as store
    allowed = store.allowed_school_ids(db, user)
    schools = [s for s in db.get("schools", []) if s["id"] in allowed]
    try:
        wanted = int(request.cookies.get(SCHOOL_COOKIE, ""))
    except ValueError:
        wanted = None
    return next((s for s in schools if s["id"] == wanted), schools[0] if schools else None)


def panel_theme(request, db: Dict[str, Any], user: Optional[Dict[str, Any]]) -> str:
    school = panel_school(request, db, user)
    return school_theme(school) if school else device_theme(request, db)


def html_attrs(theme: str, mode: str) -> str:
    return f'data-theme="{clean_theme(theme) or DEFAULT_THEME}" data-mode="{clean_mode(mode) or DEFAULT_MODE}"'
