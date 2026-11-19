"""Загрузка фотографий призов.

Берёт снимки конкретных товаров, приводит их к одному формату (WebP 800×600
на белом фоне) и складывает в static/img/prizes/. Рядом пишет CREDITS.md
с источниками и лицензиями.

Запуск:  .venv/Scripts/python.exe scripts/fetch_prize_photos.py

Любое фото учитель может заменить своим снимком прямо в панели
(вкладка «Призы» → наведите на карточку → «📷 Фото»).
"""
import io
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "static" / "img" / "prizes"
SIZE = (800, 600)

# Викимедиа требует «описательный» User-Agent и блокирует браузерный,
# остальные сайты наоборот отдают картинку только браузеру.
UA_WIKIMEDIA = "school-stars-app/1.0 (educational school reward system)"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# имя файла → (что это, источник, лицензия, [ссылки на картинку по убыванию качества])
SOURCES = {
    "znatok-999": (
        "Электронный конструктор «Знаток» 999 схем",
        "Официальный сайт производителя znatok.ru",
        "Изображение производителя (используется для внутришкольной системы)",
        [
            "https://znatok.ru/wp-content/uploads/2025/12/Znatok-999-shem-box-2.jpg",
            "https://znatok.ru/wp-content/uploads/2025/12/Znatok-999-shem-box-2-300x225.jpg",
        ],
    ),
    "znatok-320": (
        "Электронный конструктор «Знаток» 320 схем",
        "Официальный сайт производителя znatok.ru",
        "Изображение производителя (используется для внутришкольной системы)",
        [
            "https://znatok.ru/wp-content/uploads/2020/05/znatok-320-full-1.jpg",
            "https://znatok.ru/wp-content/uploads/2020/05/znatok-320-full-1-300x225.jpg",
        ],
    ),
    "keyboard-rgb": (
        "Игровая клавиатура с RGB-подсветкой (Mars Gaming MK6)",
        "Wikimedia Commons: File:Mars Gaming- MK6 gaming keyboard.jpg",
        "CC BY 2.0",
        ["https://thumb.wikimedia.org/wikipedia/commons/thumb/3/31/"
         "Mars_Gaming-_MK6_gaming_keyboard.jpg/1280px-Mars_Gaming-_MK6_gaming_keyboard.jpg"],
    ),
    "soldering-kit": (
        "Паяльник, припой и провода",
        "Wikimedia Commons: File:Solderin Iron, Tin-Solder and wires (14943579150).jpg",
        "CC BY 2.0",
        ["https://thumb.wikimedia.org/wikipedia/commons/thumb/7/7b/"
         "Solderin_Iron%2C_Tin-Solder_and_wires_%2814943579150%29.jpg/"
         "1280px-Solderin_Iron%2C_Tin-Solder_and_wires_%2814943579150%29.jpg"],
    ),
    "gaming-mouse": (
        "Игровая мышь Logitech MX518",
        "Wikimedia Commons: File:Logitech MX-518.JPG",
        "CC0 (общественное достояние)",
        ["https://thumb.wikimedia.org/wikipedia/commons/thumb/2/2a/"
         "Logitech_MX-518.JPG/1280px-Logitech_MX-518.JPG"],
    ),
    "arduino-sensors": (
        "Arduino UNO с датчиками на макетной плате",
        "Wikimedia Commons: File:Arduino UNO + HC-SR04 + HC 05 + breadboard - bird's eye 00.jpg",
        "CC BY-SA 4.0",
        ["https://thumb.wikimedia.org/wikipedia/commons/thumb/8/8c/"
         "Arduino_UNO_%2B_HC-SR04_%2B_HC_05_%2B_breadboard_-_bird%27s_eye_00.jpg/"
         "1280px-Arduino_UNO_%2B_HC-SR04_%2B_HC_05_%2B_breadboard_-_bird%27s_eye_00.jpg"],
    ),
    "headset-mousepad": (
        "Игровая гарнитура",
        "Wikimedia Commons: File:Headset computer.webp",
        "CC0 (общественное достояние)",
        ["https://thumb.wikimedia.org/wikipedia/commons/thumb/0/03/"
         "Headset_computer.webp/1280px-Headset_computer.webp"],
    ),
    "led-kit": (
        "Светодиод RGB 5 мм",
        "Wikimedia Commons: File:5mm Cycling RGB LED.jpg",
        "CC BY-SA 4.0",
        ["https://thumb.wikimedia.org/wikipedia/commons/thumb/4/41/"
         "5mm_Cycling_RGB_LED.jpg/1280px-5mm_Cycling_RGB_LED.jpg"],
    ),
}


def download(url: str, attempts: int = 3) -> bytes:
    agent = UA_WIKIMEDIA if "wikimedia.org" in url else UA_BROWSER
    request = urllib.request.Request(url, headers={"User-Agent": agent})

    last_error: Exception = RuntimeError("не выполнено ни одной попытки")
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 503):
                raise
            time.sleep(3 * (attempt + 1))   # сервер просит подождать
        except urllib.error.URLError as exc:
            last_error = exc
            time.sleep(2)
    raise last_error


def to_webp(data: bytes, destination: Path) -> None:
    """Вписываем снимок в 800×600 на белом фоне — чтобы витрина выглядела ровно."""
    image = Image.open(io.BytesIO(data))
    image.load()
    image = image.convert("RGB")
    image.thumbnail(SIZE, Image.LANCZOS)

    canvas = Image.new("RGB", SIZE, (255, 255, 255))
    canvas.paste(image, ((SIZE[0] - image.width) // 2, (SIZE[1] - image.height) // 2))
    canvas.save(destination, "WEBP", quality=86, method=5)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    credits = ["# Источники фотографий призов", "",
               "Файлы в этой папке загружены скриптом `scripts/fetch_prize_photos.py`.",
               "Любую фотографию можно заменить своей прямо в панели учителя:",
               "вкладка «Призы» → навести на карточку → «📷 Фото».", ""]
    failures = []

    for name, (title, source, license_name, urls) in SOURCES.items():
        destination = OUT_DIR / f"{name}.webp"
        saved = False

        for url in urls:
            try:
                to_webp(download(url), destination)
                print(f"[OK]   {name}.webp  <-  {url.split('/')[-1][:60]}")
                credits.append(f"- **{name}.webp** — {title}  \n"
                               f"  Источник: {source}  \n"
                               f"  Лицензия: {license_name}  \n"
                               f"  Ссылка: {url}")
                saved = True
                break
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                print(f"[..]   {name}: не вышло с {url.split('/')[-1][:40]} ({exc})")

        if not saved:
            failures.append(name)
            print(f"[FAIL] {name}: ни один источник не ответил")

    (OUT_DIR / "CREDITS.md").write_text("\n".join(credits) + "\n", encoding="utf-8")

    print()
    print(f"Готово: {len(SOURCES) - len(failures)} из {len(SOURCES)} фотографий.")
    if failures:
        print("Не загрузились:", ", ".join(failures))
        print("Эти призы покажут заглушку — загрузите фото вручную в панели учителя.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
