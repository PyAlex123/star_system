"""Настройки приложения. Читаются из .env один раз при старте."""
import hashlib
import os
import secrets
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

# Кодировку указываем явно: без неё python-dotenv читает файл в кодировке системы
# (на русской Windows это cp1251), и кириллица в названии школы или пароле ломается.
# utf-8-sig вдобавок срезает BOM — его добавляют Блокнот и PowerShell,
# иначе первая переменная в файле получает невидимый префикс и не находится.
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


def _prefix(raw: str) -> str:
    """Нормализует подпуть: '' | '/stars'. Без завершающего слэша."""
    value = (raw or "").strip().strip("/")
    return f"/{value}" if value else ""


# ── Где лежат изменяемые данные ──────────────────────────────────────────────
# Всё, что приложение пишет, собрано в одном каталоге: его достаточно
# смонтировать в Docker одним томом и копировать целиком для бэкапа.

DATA_DIR = Path(os.getenv("DATA_DIR", "").strip() or (BASE_DIR / "data")).resolve()
DB_FILE = DATA_DIR / "stars_db.json"
UPLOADS_DIR = DATA_DIR / "uploads"
AVATARS_UPLOAD_DIR = UPLOADS_DIR / "avatars"
PRIZES_UPLOAD_DIR = UPLOADS_DIR / "prizes"
SECRET_FILE = DATA_DIR / "jwt_secret.txt"


def _migrate_legacy_data() -> None:
    """Разовый перенос со старой раскладки (всё в корне проекта) в data/.

    Нужен, чтобы при обновлении не потерялись уже накопленные звёзды и фотографии.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    legacy_db = BASE_DIR / "stars_db.json"
    if legacy_db.exists() and not DB_FILE.exists():
        shutil.move(str(legacy_db), str(DB_FILE))

    legacy_secret = BASE_DIR / ".jwt_secret"
    if legacy_secret.exists() and not SECRET_FILE.exists():
        shutil.move(str(legacy_secret), str(SECRET_FILE))

    legacy_uploads = STATIC_DIR / "uploads"
    if legacy_uploads.is_dir() and not UPLOADS_DIR.exists():
        shutil.move(str(legacy_uploads), str(UPLOADS_DIR))

    for directory in (AVATARS_UPLOAD_DIR, PRIZES_UPLOAD_DIR):
        directory.mkdir(parents=True, exist_ok=True)


_migrate_legacy_data()


def _load_or_create_secret() -> str:
    """JWT-секрет: из .env, иначе из файла, иначе создаём новый.

    Храним в файле, чтобы при перезапуске сервера дети не вылетали из кабинета.
    """
    from_env = os.getenv("JWT_SECRET", "").strip()
    if from_env:
        return from_env

    if SECRET_FILE.exists():
        saved = SECRET_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return saved

    generated = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(generated, encoding="utf-8")
    return generated


# ── Основные настройки ───────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").strip().rstrip("/")
URL_PREFIX = _prefix(os.getenv("URL_PREFIX", ""))
SCHOOL_NAME = os.getenv("SCHOOL_NAME", "Наша школа").strip()
DEV_LOGIN = _bool("DEV_LOGIN", False)
USE_WEBHOOK = _bool("USE_WEBHOOK", False)
JWT_SECRET = _load_or_create_secret()

IS_HTTPS = BASE_URL.startswith("https://")

# Пароль учителя.
TEACHER_PASSWORD = os.getenv("TEACHER_PASSWORD", "").strip()
TEACHER_PASSWORD_IS_GENERATED = False
if not TEACHER_PASSWORD:
    if IS_HTTPS:
        # Боевой сервер. Молча придумать пароль и спрятать его в логи — ловушка:
        # после перезапуска контейнера учитель не сможет войти.
        sys.exit(
            "\n  ОСТАНОВЛЕНО: в .env не задан TEACHER_PASSWORD.\n"
            "  Сайт опубликован по HTTPS, поэтому панель учителя обязана иметь постоянный пароль.\n"
            "  Добавьте строку вида  TEACHER_PASSWORD=придумайте-длинный-пароль  в .env и запустите снова.\n"
        )
    TEACHER_PASSWORD = secrets.token_urlsafe(9)
    TEACHER_PASSWORD_IS_GENERATED = True

# Учётные записи панели. Из .env они берутся ОДИН раз — при первом запуске,
# когда в базе ещё нет ни одного пользователя. Дальше пароли живут только
# в базе (хешами) и меняются в самой панели.
ADMIN_LOGIN = (os.getenv("ADMIN_LOGIN", "admin").strip() or "admin").lower()
TEACHER_LOGIN = (os.getenv("TEACHER_LOGIN", "teacher").strip() or "teacher").lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
# Забыли добавить ADMIN_PASSWORD — суперадмин получит пароль учителя, а сайт
# не упадёт. В логе об этом будет предупреждение.
ADMIN_PASSWORD_IS_FALLBACK = not ADMIN_PASSWORD
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = TEACHER_PASSWORD

# Срок жизни сессий
STUDENT_SESSION_DAYS = 30
TEACHER_SESSION_HOURS = 12

# Правила начисления звёзд
MAX_LESSON_BANK = 10      # максимум звёзд классу за одну дату (все уроки этого дня вместе)
MAX_STUDENT_STARS = 5     # максимум звёзд одному ученику за одну дату

# Загрузка картинок
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 320
PRIZE_IMAGE_WIDTH = 640    # карточка приза на телефоне ~175 точек: 640 хватает и для плотных экранов

BOT_ENABLED = bool(BOT_TOKEN)

# Путь, на который Telegram шлёт обновления в режиме webhook.
# Выводим из токена: значение стабильное между перезапусками и не угадывается снаружи.
# Внутри приложения маршрут живёт без подпути: nginx срезает префикс,
# прежде чем передать запрос (location /stars/ { proxy_pass .../; }).
WEBHOOK_SECRET = (hashlib.sha256(f"webhook:{BOT_TOKEN}".encode()).hexdigest()[:40]
                  if BOT_TOKEN else "")
WEBHOOK_PATH = f"/telegram/{WEBHOOK_SECRET}" if WEBHOOK_SECRET else ""


# ── Отметки преподавателей ───────────────────────────────────────────────────
# Свой бот: преподаватели не пишут в бот учеников, а дети не видят меню отметок.
# Токен пустой — модуль работает (панель, расписание, отчёты), бот не запускается.

CHECKIN_BOT_TOKEN = os.getenv("CHECKIN_BOT_TOKEN", "").strip()
CHECKIN_BOT_USERNAME = os.getenv("CHECKIN_BOT_USERNAME", "").strip().lstrip("@")
# Один токен на два бота — два опроса getUpdates дерутся, и дети попадают в меню отметок.
CHECKIN_TOKEN_CLASH = bool(CHECKIN_BOT_TOKEN) and CHECKIN_BOT_TOKEN == BOT_TOKEN
CHECKIN_BOT_ENABLED = bool(CHECKIN_BOT_TOKEN) and not CHECKIN_TOKEN_CLASH
# Отметки, журналы и уроки по датам растут без предела — им не место в JSON,
# который переписывается целиком. Лежат в том же каталоге данных, бэкап — тот же.
CHECKIN_DB_FILE = DATA_DIR / "checkin.sqlite"
# Согласованный снимок для бэкапа: его делает планировщик ночью и deploy/backup.sh
CHECKIN_BACKUP_FILE = DATA_DIR / "checkin.backup.sqlite"
CHECKIN_INVITE_DAYS = 7          # ссылка-приглашение преподавателя действует неделю
# Все расчёты отметок — по часам школ. В Узбекистане нет перехода на летнее время.
TZ_NAME = os.getenv("TZ_NAME", "").strip() or "Asia/Tashkent"

CHECKIN_WEBHOOK_SECRET = (hashlib.sha256(f"checkin-webhook:{CHECKIN_BOT_TOKEN}".encode()).hexdigest()[:40]
                          if CHECKIN_BOT_TOKEN else "")
CHECKIN_WEBHOOK_PATH = f"/telegram/checkin/{CHECKIN_WEBHOOK_SECRET}" if CHECKIN_WEBHOOK_SECRET else ""


def site_url(path: str = "") -> str:
    """Полный внешний адрес страницы с учётом подпути."""
    base = os.getenv("BASE_URL", BASE_URL).strip().rstrip("/")
    return f"{base}{URL_PREFIX}{path}"


def webapp_url() -> str:
    """Адрес кабинета для кнопки в боте. Читается в момент отправки сообщения,
    поэтому смена адреса (например, туннеля) не требует правки кода."""
    return site_url("/app")


def webhook_url() -> str:
    """Адрес, который мы сообщаем Telegram. Здесь подпуть обязателен."""
    return site_url(WEBHOOK_PATH)


def checkin_invite_link(code: str) -> str:
    """Ссылка-приглашение преподавателя в бот отметок. Пусто — имя бота неизвестно."""
    if not CHECKIN_BOT_USERNAME:
        return ""
    return f"https://t.me/{CHECKIN_BOT_USERNAME}?start=inv_{code}"


def deep_link(token: str) -> str:
    """Ссылка, которая зашивается в QR-код карточки ученика."""
    if not BOT_USERNAME:
        return site_url(f"/app?code={token}")
    return f"https://t.me/{BOT_USERNAME}?start={token}"
