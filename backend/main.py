"""Точка сборки приложения."""
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import assets, config, db, themes
from .bot.bot import start_bot, stop_bot
from .routers import (auth, cards, excel, lessons, me, prizes, schedule, schools, stats, students,
                      telegram, users)
from .security import panel_user, require_superadmin_page, require_teacher_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stars")


def _startup_banner() -> None:
    log.info("Адрес сайта:      %s", config.site_url("/"))
    log.info("Кабинет учеников: %s", config.webapp_url())
    log.info("Данные:           %s", config.DATA_DIR)
    if config.URL_PREFIX:
        log.info("Подпуть:          %s (nginx должен срезать его перед проксированием)",
                 config.URL_PREFIX)
    if config.TEACHER_PASSWORD_IS_GENERATED:
        log.warning("=" * 62)
        log.warning("  Пароль учителя не задан в .env. Временный пароль (только для первого запуска):")
        log.warning("      %s", config.TEACHER_PASSWORD)
        log.warning("  Пропишите TEACHER_PASSWORD в .env, чтобы он не менялся.")
        log.warning("=" * 62)
    if config.DEV_LOGIN:
        log.warning("DEV_LOGIN включён: вход в кабинет возможен без Telegram. "
                    "На боевом сервере выключите его!")
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN пуст — бот выключен. Заполните .env и перезапустите.")
    elif not config.BOT_USERNAME:
        log.warning("BOT_USERNAME пуст — QR-коды на карточках ведут на сайт, а не в бота.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    for directory in (config.AVATARS_UPLOAD_DIR, config.PRIZES_UPLOAD_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    _startup_banner()
    await start_bot()
    try:
        yield
    finally:
        await stop_bot()


# root_path намеренно НЕ задаём. nginx срезает подпуть до того, как запрос
# доходит сюда, поэтому внутри приложение живёт в корне. Если выставить
# root_path, Starlette начнёт вычитать префикс второй раз, и смонтированные
# каталоги (/static, /static/uploads) станут отдавать 404.
# Все внешние ссылки собираются через {{BASE}} и config.site_url().
app = FastAPI(
    title="Система мотивации и учёта звёзд",
    lifespan=lifespan,
    # Схему и интерактивные доки на боевом сервере не публикуем
    docs_url=None if config.IS_HTTPS else "/docs",
    redoc_url=None,
    openapi_url=None if config.IS_HTTPS else "/openapi.json",
)


def _validation_message(error: dict) -> str:
    """Одна ошибка проверки данных — по-русски, без технических подробностей."""
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    if kind == "string_too_long":
        return f"Слишком длинное значение — не больше {ctx.get('max_length')} символов"
    if kind == "too_long":
        return f"Слишком много строк за раз — не больше {ctx.get('max_length')}"
    if kind in ("greater_than_equal", "greater_than"):
        return f"Значение должно быть не меньше {ctx.get('ge', ctx.get('gt'))}"
    if kind in ("less_than_equal", "less_than"):
        return f"Значение должно быть не больше {ctx.get('le', ctx.get('lt'))}"
    if kind == "value_error":
        return str(ctx.get("error") or error.get("msg", ""))
    if kind == "missing":
        return "Не заполнено обязательное поле"
    if kind.startswith(("int_", "float_")):
        return "Ожидалось число"
    return "Неверные данные в запросе"


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc: RequestValidationError):
    """FastAPI по умолчанию отдаёт список объектов на английском — интерфейс показал
    бы «[object Object]». Собираем одну понятную строку."""
    messages = []
    for error in exc.errors():
        text = _validation_message(error)
        if text not in messages:
            messages.append(text)
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})


# Кабинет ученика веб-версия Telegram открывает во фрейме — ей встраивание
# разрешаем. Всё остальное (панель учителя, вход, карточки) встраивать нельзя:
# иначе чужой сайт может наложить на нашу страницу невидимые кнопки (кликджекинг).
TELEGRAM_FRAME_ANCESTORS = "https://web.telegram.org https://*.telegram.org"


def _cache_control(path: str, query: str) -> Optional[str]:
    if not path.startswith("/static/"):
        return None
    if path.endswith((".css", ".js")):
        # С отметкой версии — навсегда: обновился файл — изменился и адрес.
        # Без отметки (старые ссылки) — перепроверять, чтобы не застрять на старой версии.
        return "public, max-age=31536000, immutable" if "v=" in query else "no-cache"
    if path.startswith("/static/uploads/"):
        return "public, max-age=300"
    return "public, max-age=604800"


class ResponseHeaders:
    """Заголовки кеша и безопасности — одним лёгким ASGI-слоем.

    Раньше это были два слоя @app.middleware. Они построены на BaseHTTPMiddleware,
    который пропускает каждый ответ через свою обёртку — в том числе картинки и
    шрифты, отдаваемые потоком. Здесь мы только дописываем заголовки к началу ответа.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin")]
        if path == "/app":
            extra.append((b"content-security-policy",
                          f"frame-ancestors {TELEGRAM_FRAME_ANCESTORS}".encode()))
        else:
            extra += [(b"content-security-policy", b"frame-ancestors 'none'"),
                      (b"x-frame-options", b"DENY")]
        cache = _cache_control(path, query)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if not (cache and k.lower() == b"cache-control")]
                if cache:
                    headers.append((b"cache-control", cache.encode()))
                message["headers"] = headers + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


# Сжимаем только текст: страницы, CSS, JS, SVG. Картинки и шрифты уже сжаты —
# повторное сжатие только тратит процессор. Ответы API не трогаем: они маленькие,
# а сжатие ответов с секретами рядом с текстом от пользователя — лишний риск (BREACH).
_COMPRESSIBLE_STATIC = (".css", ".js", ".svg")
_COMPRESSIBLE_PAGES = {"/", "/teacher", "/login", "/enter", "/app", "/_ui"}


def _compressible(path: str) -> bool:
    if path.startswith("/static/"):
        return path.endswith(_COMPRESSIBLE_STATIC)
    return path in _COMPRESSIBLE_PAGES or path.startswith("/cards/")


class SelectiveGZip:
    """gzip для текстовых ответов — обычно в 3–5 раз меньше по сети."""

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=500)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _compressible(scope["path"]):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(SelectiveGZip)
app.add_middleware(ResponseHeaders)


# Загруженные картинки лежат в каталоге данных, а не внутри static/.
# Монтируем их раньше общего /static — совпадение ищется по порядку.
app.mount("/static/uploads", StaticFiles(directory=str(config.UPLOADS_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(stats.router)
app.include_router(schools.router)
app.include_router(schedule.router)
app.include_router(students.router)
app.include_router(lessons.router)
app.include_router(prizes.router)
app.include_router(me.router)
app.include_router(cards.router)
app.include_router(users.router)
app.include_router(excel.router)
app.include_router(telegram.router)


# ── Отдача страниц ───────────────────────────────────────────────────────────

_page_cache: Dict[str, Tuple[tuple, str]] = {}


ICONS_FILE = config.STATIC_DIR / "img" / "icons.svg"


def render_page(name: str) -> str:
    """Готовит HTML к отдаче.

    {{BASE}}  — подпуть, иначе ссылки /static/... ушли бы в корень чужого сайта.
    {{ICONS}} — набор иконок вставляется прямо в страницу: внешний спрайт через
                <use href="файл.svg#id"> не всегда грузится в WebView Telegram.

    К ссылкам на CSS и JS дописывается отметка версии (см. backend/assets.py).

    Результат кешируем по времени изменения файлов, чтобы правки подхватывались
    без перезапуска сервера. В отметку входят и подключаемые CSS/JS: поменяли
    стиль — страница получит новую ссылку на него.
    """
    path = config.STATIC_DIR / name
    stamp = (path.stat().st_mtime_ns, ICONS_FILE.stat().st_mtime_ns, assets.files_stamp(),
             config.URL_PREFIX)
    cached = _page_cache.get(name)
    if cached and cached[0] == stamp:
        return cached[1]

    html = assets.add_versions(path.read_text(encoding="utf-8"))
    html = html.replace("{{BASE}}", config.URL_PREFIX)
    if "{{ICONS}}" in html:
        html = html.replace("{{ICONS}}", ICONS_FILE.read_text(encoding="utf-8"))
    _page_cache[name] = (stamp, html)
    return html


def _page(name: str, request: Request, theme: str) -> HTMLResponse:
    """Страница в нужной теме. Тема подставляется на каждый запрос, поверх
    общего кеша: шаблон один, а школы у посетителей разные."""
    html = render_page(name).replace(
        'data-theme="{{THEME}}" data-mode="{{MODE}}"',
        themes.html_attrs(theme, themes.page_mode(request)))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def entry_page(request: Request):
    """Главная: «Я ученик» или «Я учитель». Сюда попадает каждый, кто набрал адрес сайта."""
    return _page("index.html", request, themes.device_theme(request, db.load_db()))


@app.get("/teacher", response_class=HTMLResponse, dependencies=[Depends(require_teacher_page)])
def teacher_page(request: Request):
    """Журнал учителя. Без входа — перенаправление на /login.
    Тема — школы, с которой учитель работал последней."""
    database = db.load_db()
    return _page("teacher.html", request, themes.panel_theme(request, database, panel_user(request)))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _page("login.html", request, themes.device_theme(request, db.load_db()))


@app.get("/enter", response_class=HTMLResponse)
def student_login_page(request: Request):
    """Вход ученика по логину и паролю — для тех, у кого нет Telegram."""
    return _page("enter.html", request, themes.device_theme(request, db.load_db()))


@app.get("/app", response_class=HTMLResponse)
def student_app(request: Request):
    """Кабинет ученика (Telegram Web App). Данные отдаются только после проверки initData."""
    return _page("app.html", request, themes.student_page_theme(request, db.load_db()))


@app.get("/_ui", response_class=HTMLResponse, dependencies=[Depends(require_superadmin_page)])
def ui_showcase(request: Request):
    """Витрина компонентов во всех темах — для проверки дизайна. Только администратор."""
    theme = themes.clean_theme(request.query_params.get("theme")) or themes.DEFAULT_THEME
    return _page("ui.html", request, theme)


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "bot_enabled": config.BOT_ENABLED,
        "bot_mode": ("webhook" if config.USE_WEBHOOK else "polling") if config.BOT_ENABLED else None,
        "bot_username": config.BOT_USERNAME or None,
        "base_url": config.site_url("/"),
    }
