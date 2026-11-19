"""Точка сборки приложения."""
import logging
from contextlib import asynccontextmanager
from typing import Dict, Tuple

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .bot.bot import start_bot, stop_bot
from .routers import auth, cards, me, prizes, teacher, telegram
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


# Загруженные картинки лежат в каталоге данных, а не внутри static/.
# Монтируем их раньше общего /static — совпадение ищется по порядку.
app.mount("/static/uploads", StaticFiles(directory=str(config.UPLOADS_DIR)), name="uploads")
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(teacher.router)
app.include_router(prizes.router)
app.include_router(me.router)
app.include_router(cards.router)
app.include_router(telegram.router)


# ── Отдача страниц ───────────────────────────────────────────────────────────

_page_cache: Dict[str, Tuple[float, str]] = {}


def render_page(name: str) -> str:
    """Подставляет в HTML подпуть вместо {{BASE}}.

    Без этого при развёртывании на подпути ссылки вида /static/... ушли бы
    в корень чужого сайта. Результат кешируем по времени изменения файла,
    чтобы правки подхватывались без перезапуска.
    """
    path = config.STATIC_DIR / name
    stamp = path.stat().st_mtime
    cached = _page_cache.get(name)
    if cached and cached[0] == stamp:
        return cached[1]

    html = path.read_text(encoding="utf-8").replace("{{BASE}}", config.URL_PREFIX)
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
