"""Обработка загружаемых картинок: аватары детей и фотографии призов."""
import io
import secrets
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}


async def read_upload(file: UploadFile) -> bytes:
    """Читает файл с ограничением по размеру."""
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Файл слишком большой, максимум {mb} МБ")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")
    return data


def _open(data: bytes) -> Image.Image:
    """Открывает картинку и проверяет, что это действительно изображение."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except UnidentifiedImageError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Не удалось распознать картинку. Подойдут JPG, PNG или WEBP. "
            "Фото с iPhone в формате HEIC сначала сохраните как JPG.",
        )
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл повреждён или это не картинка")

    if image.format and image.format.upper() not in ALLOWED_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Формат {image.format} не поддерживается. Нужен JPG, PNG или WEBP.")

    # Телефоны пишут ориентацию в EXIF — разворачиваем, иначе фото ляжет боком
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def save_square(data: bytes, destination: Path, size: int = config.AVATAR_SIZE) -> None:
    """Обрезает по центру в квадрат и сохраняет WEBP — для аватаров."""
    image = _open(data)
    square = ImageOps.fit(image, (size, size), method=Image.LANCZOS, centering=(0.5, 0.4))
    destination.parent.mkdir(parents=True, exist_ok=True)
    square.save(destination, "WEBP", quality=88, method=5)


def save_wide(data: bytes, destination: Path,
              box: Tuple[int, int] = (config.PRIZE_IMAGE_WIDTH, int(config.PRIZE_IMAGE_WIDTH * 0.75))) -> None:
    """Вписывает фото приза в прямоугольник 4:3 на белом фоне и сохраняет WEBP."""
    image = _open(data)
    image.thumbnail(box, Image.LANCZOS)
    canvas = Image.new("RGB", box, (255, 255, 255))
    canvas.paste(image, ((box[0] - image.width) // 2, (box[1] - image.height) // 2))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "WEBP", quality=86, method=5)


# ── Где лежат загрузки ───────────────────────────────────────────────────────

UPLOADS_URL = "/static/uploads/"


def new_avatar_name() -> str:
    """Случайное имя для фото ребёнка.

    Раньше файл назывался по номеру ученика (student-101.webp): адрес угадывался
    перебором, и фотографии детей мог собрать кто угодно без входа.
    """
    return f"avatar-{secrets.token_hex(12)}.webp"


def upload_path(url: Optional[str]) -> Optional[Path]:
    """Файл на диске по адресу из базы. Только внутри каталога загрузок."""
    if not url or not url.startswith(UPLOADS_URL):
        return None
    relative = url.split("?", 1)[0][len(UPLOADS_URL):]
    root = config.UPLOADS_DIR.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        return None
    return path


def drop_upload(url: Optional[str]) -> None:
    """Удаляет загруженный файл. Ссылки на встроенные картинки не трогает."""
    path = upload_path(url)
    if path:
        path.unlink(missing_ok=True)


def avatar_upload_url(student) -> Optional[str]:
    """Адрес загруженного фото ученика, если аватар — его собственное фото."""
    avatar = (student or {}).get("avatar") or {}
    return avatar.get("value") if avatar.get("type") == "photo" else None
