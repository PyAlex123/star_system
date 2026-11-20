"""Главная с выбором входа, фото призов без полос, кеш и сжатие."""
import io
import re

from PIL import Image

from backend import config
from conftest import jpeg_bytes


# ── Главная и журнал ─────────────────────────────────────────────────────────

def test_landing_open_to_everyone(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert 'href="/enter"' in r.text, "кнопка входа ученика"
    assert 'href="/teacher"' in r.text, "кнопка входа учителя"


def test_journal_moved_to_teacher(client):
    r = client.get("/teacher", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login?next=/teacher"


def test_journal_opens_for_teacher(teacher):
    r = teacher.get("/teacher")
    assert r.status_code == 200
    assert "Журнал звёзд" in r.text


def test_after_login_teacher_goes_to_journal(client):
    assert 'BASE + "/teacher"' in client.get("/login").text


def test_login_pages_link_back_to_choice(client):
    for page in ("/login", "/enter"):
        assert 'href="/"' in client.get(page).text, page


def test_cards_link_to_journal(teacher):
    assert 'href="/teacher"' in teacher.get("/cards/1").text


# ── Фото призов заполняют рамку ──────────────────────────────────────────────

def corner_colors(image):
    w, h = image.size
    return [image.getpixel(xy) for xy in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))]


def test_portrait_upload_fills_frame(teacher):
    """Вертикальное фото с телефона: раньше по бокам были белые полосы."""
    pid = teacher.post("/api/prizes", json={"name": "Приз", "cost": 5}).json()["prize"]["id"]
    red = jpeg_bytes(size=(300, 600), color=(200, 30, 30))
    url = teacher.post(f"/api/prizes/{pid}/photo",
                       files={"file": ("p.jpg", red, "image/jpeg")}).json()["image"]
    image = Image.open(io.BytesIO(teacher.get(url).content)).convert("RGB")
    assert image.size == (config.PRIZE_IMAGE_WIDTH, config.PRIZE_IMAGE_WIDTH * 3 // 4)
    for r, g, b in corner_colors(image):
        assert r > 150 and g < 90 and b < 90, "в углу не фото, а поле"


# Снято на белом студийном фоне: белые края — часть самого фото, а не поле.
# По пикселям такое не отличить от поля, поэтому список явный.
STUDIO_WHITE_BACKGROUND = {"znatok-320.webp"}


def test_photo_script_fills_frame():
    """Скрипт, который собирает встроенные фото, заполняет рамку, а не вписывает."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fetch_prize_photos", config.BASE_DIR / "scripts" / "fetch_prize_photos.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    out = config.DATA_DIR / "script-check.webp"
    script.to_webp(jpeg_bytes(size=(400, 900), color=(30, 90, 200)), out, focus=(0.5, 0.1))
    image = Image.open(out).convert("RGB")
    assert image.size == script.SIZE
    for r, g, b in corner_colors(image):
        assert b > 150 and r < 90, "в углу поле, а не фото"
    out.unlink()


def test_default_prize_photos_have_no_white_bars():
    """Встроенные фото тоже без полос: по краям кадра — само фото, а не белый фон."""
    for path in sorted((config.STATIC_DIR / "img" / "prizes").glob("*.webp")):
        if path.name in STUDIO_WHITE_BACKGROUND:
            continue
        image = Image.open(path).convert("RGB")
        w, h = image.size
        assert w * 3 == h * 4, f"{path.name}: пропорция не 4:3"
        # Поле — это целый столбец (или строка) чисто белых пикселей у края: старый скрипт
        # заливал фон ровно (255, 255, 255). Светло-серый студийный фон «Знатока 999»
        # (246, 246, 246) полем не считается — это часть самого снимка.
        for name, line in (("слева", [image.getpixel((1, y)) for y in range(0, h, 7)]),
                           ("справа", [image.getpixel((w - 2, y)) for y in range(0, h, 7)]),
                           ("сверху", [image.getpixel((x, 1)) for x in range(0, w, 7)]),
                           ("снизу", [image.getpixel((x, h - 2)) for x in range(0, w, 7)])):
            white = sum(1 for px in line if min(px) >= 254)
            assert white < len(line) * 0.9, f"{path.name}: белая полоса {name}"


# ── Кеш: версии в адресах ────────────────────────────────────────────────────

def test_assets_are_versioned(teacher):
    html = teacher.get("/teacher").text
    for asset in ("css/theme.css", "css/teacher.css", "js/common.js", "js/teacher.js"):
        assert re.search(rf'/static/{re.escape(asset)}\?v=\w+"', html), asset


def test_versioned_assets_cached_forever(client):
    html = client.get("/app").text
    url = re.search(r'src="(/static/js/app\.js\?v=\w+)"', html).group(1)
    cache = client.get(url).headers["cache-control"]
    assert "immutable" in cache and "max-age=31536000" in cache


def test_plain_assets_still_revalidated(client):
    assert client.get("/static/js/app.js").headers["cache-control"] == "no-cache"


def test_version_follows_content(tmp_path, monkeypatch):
    """Изменили файл — другой адрес; только «потрогали» без изменений — адрес прежний,
    и кеш у детей не сбрасывается при каждой пересборке образа."""
    import os
    from backend import assets
    (tmp_path / "css").mkdir()
    css = tmp_path / "css" / "x.css"
    css.write_text("a{color:red}", encoding="utf-8")
    monkeypatch.setattr(config, "STATIC_DIR", tmp_path)

    first = assets.version("css/x.css")
    os.utime(css, (css.stat().st_atime, css.stat().st_mtime + 100))
    assert assets.version("css/x.css") == first, "время изменилось, содержимое нет"
    css.write_text("a{color:blue}", encoding="utf-8")
    assert assets.version("css/x.css") != first, "содержимое изменилось"


# ── Сжатие ───────────────────────────────────────────────────────────────────

def test_text_compressed(client, teacher):
    for c, path in ((client, "/static/js/teacher.js"), (client, "/static/css/app.css"),
                    (client, "/app"), (teacher, "/teacher")):
        r = c.get(path, headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") == "gzip", path


def test_images_and_api_not_compressed(client, teacher):
    for c, path in ((client, "/static/img/prizes/gaming-mouse.webp"),
                    (client, "/static/fonts/golos-latin.woff2"),
                    (teacher, "/api/state")):
        r = c.get(path, headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in r.headers, path


def test_headers_survive_compression(client):
    r = client.get("/app", headers={"Accept-Encoding": "gzip"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-cache"


# ── Картинки, которых не видно, не грузятся заранее ─────────────────────────

def test_prize_images_lazy():
    """Фото призов грузятся, только когда видны. Журнал учителя раньше тянул все
    восемь фото (~480 КБ) сразу, хотя вкладка «Призы» даже не открыта."""
    js = config.STATIC_DIR / "js"
    for name in ("app.js", "teacher.js"):
        source = (js / name).read_text(encoding="utf-8")
        tags = re.findall(r'<img src="\$\{escapeHtml\(assetUrl\(prize\.image\)\)\}"[^>]*>', source)
        assert tags, f"{name}: не нашёл тег фото приза — проверка устарела"
        assert all('loading="lazy"' in t for t in tags), f"{name}: {tags}"
