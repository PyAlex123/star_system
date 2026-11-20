"""Точка сборки приложения."""
import logging
from contextlib import asynccontextmanager
from typing import Dict, Tuple

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .bot.bot import start_bot, stop_bot
from .routers import auth, cards, lessons, me, prizes, schools, students, telegram
from .security import require_teacher_page

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
        log.warning("  Пароль учителя не задан в .env. Временный пароль на эту сессию:")
        log.warning("      %s", config.TEACHER_PASSWORD)
        log.warning("  Пропишите TEACHER_PASSWORD в .env, чтобы он не менялся.")
        log.warning("=" * 62)
    if config.DEV_LOGIN:
        log.warning("DEV_LOGIN включён: вход в кабинет возможен без Telegram. "
                    "На боевом сервере выключите его!")
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN пуст — бот выключен. Заполните .env и перезапустите.")


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


@app.middleware("http")
async def cache_headers(request, call_next):
    """CSS и JS браузер обязан перепроверять, иначе после обновления системы
    у детей останется старая версия кабинета. Картинки и шрифты можно кешировать надолго.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        if path.endswith((".css", ".js")):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("/static/uploads/"):
            response.headers["Cache-Control"] = "public, max-age=300"
        else:
            response.headers["Cache-Control"] = "public, max-age=604800"
    return response


# Кабинет ученика веб-версия Telegram открывает во фрейме — ей встраивание
# разрешаем. Всё остальное (панель учителя, вход, карточки) встраивать нельзя:
# иначе чужой сайт может наложить на нашу страницу невидимые кнопки (кликджекинг).
TELEGRAM_FRAME_ANCESTORS = "https://web.telegram.org https://*.telegram.org"


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path == "/app":
        response.headers["Content-Security-Policy"] = f"frame-ancestors {TELEGRAM_FRAME_ANCESTORS}"
    else:
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Frame-Options"] = "DENY"
    return response


# Загруженные картинки лежат в каталоге данных, а не внутри static/.
# Монтируем их раньше общего /static — совпадение ищется по порядку.
app.mount("/static/uploads", StaticFiles(directory=str(config.UPLOADS_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(schools.router)
app.include_router(students.router)
app.include_router(lessons.router)
app.include_router(prizes.router)
app.include_router(me.router)
app.include_router(cards.router)
app.include_router(telegram.router)


# ── Отдача страниц ───────────────────────────────────────────────────────────

_page_cache: Dict[str, Tuple[float, str]] = {}


ICONS_FILE = config.STATIC_DIR / "img" / "icons.svg"


def render_page(name: str) -> str:
    """Готовит HTML к отдаче.

    {{BASE}}  — подпуть, иначе ссылки /static/... ушли бы в корень чужого сайта.
    {{ICONS}} — набор иконок вставляется прямо в страницу: внешний спрайт через
                <use href="файл.svg#id"> не всегда грузится в WebView Telegram.

    Результат кешируем по времени изменения файлов, чтобы правки подхватывались
    без перезапуска сервера.
    """
    path = config.STATIC_DIR / name
    stamp = path.stat().st_mtime + ICONS_FILE.stat().st_mtime
    cached = _page_cache.get(name)
    if cached and cached[0] == stamp:
        return cached[1]

    html = path.read_text(encoding="utf-8").replace("{{BASE}}", config.URL_PREFIX)
    if "{{ICONS}}" in html:
        html = html.replace("{{ICONS}}", ICONS_FILE.read_text(encoding="utf-8"))
    _page_cache[name] = (stamp, html)
    return html


def _page(name: str) -> HTMLResponse:
    return HTMLResponse(render_page(name), headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_teacher_page)])
def teacher_page():
    return _page("teacher.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _page("login.html")


@app.get("/enter", response_class=HTMLResponse)
def student_login_page():
    """Вход ученика по логину и паролю — для тех, у кого нет Telegram."""
    return _page("enter.html")


@app.get("/app", response_class=HTMLResponse)
def student_app():
    """Кабинет ученика (Telegram Web App). Данные отдаются только после проверки initData."""
    return _page("app.html")


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "bot_enabled": config.BOT_ENABLED,
        "bot_mode": ("webhook" if config.USE_WEBHOOK else "polling") if config.BOT_ENABLED else None,
        "bot_username": config.BOT_USERNAME or None,
        "base_url": config.site_url("/"),
    }
