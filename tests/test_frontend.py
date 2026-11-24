"""Проверки клиентского кода через Node.js — без браузера и сборки.

Зачем: синтаксическая проверка (node --check) не видит, если незакрытый
комментарий «съел» кусок кода до следующего «*/». Файл остаётся корректным,
а функций в нём уже нет — панель ломается только в браузере. Однажды так
и случилось при выносе общих функций в common.js.

Если Node.js не установлен, тесты пропускаются.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "static" / "js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js не установлен")


def run_node(*args, script=None):
    cmd = [NODE, *(["-e", script] if script else []), *map(str, args)]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60)


BUILTINS = {"if", "parseInt"}


def html_handlers(page: str) -> set:
    """Наши функции из onclick/onchange/oninput в разметке страницы.

    Вызовы методов (location.reload(), this.value) пропускаем: это не наши функции.
    """
    html = (ROOT / "static" / page).read_text(encoding="utf-8")
    names = set()
    for call in re.findall(r'on(?:click|change|input|submit)="([^"]+)"', html):
        names.update(re.findall(r"(?<![\w.])([a-zA-Z_]\w*)\(", call))
    return names - BUILTINS


def page_scripts(page: str) -> list:
    """Скрипты страницы в порядке подключения, кроме common.js."""
    html = (ROOT / "static" / page).read_text(encoding="utf-8")
    return [JS / name for name in re.findall(r'src="[^"]*/static/js/([\w.-]+\.js)"', html) if name != "common.js"]


@pytest.mark.parametrize("page_html", ["app.html", "teacher.html"])
def test_every_handler_in_markup_exists(page_html):
    """Каждая функция, на которую ссылается кнопка в разметке, объявлена в скриптах."""
    names = sorted(html_handlers(page_html))
    assert len(names) > 5, "в разметке почти нет обработчиков — проверка сломалась"
    scripts = page_scripts(page_html)
    assert scripts, "не нашли скрипты страницы"
    r = run_node(ROOT / "tests" / "js_load_check.js", JS / "common.js", *scripts, "--", *names)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("name", sorted(p.name for p in JS.glob("*.js")))
def test_syntax(name):
    r = run_node("--check", JS / name)
    assert r.returncode == 0, r.stderr


def eval_common(expression: str, extra=()):
    """Выражение после common.js (и дополнительных скриптов без кода на верхнем уровне)."""
    loads = "".join(f"vm.runInContext(fs.readFileSync({json.dumps(str(JS / name))}, 'utf8'), ctx);"
                    for name in ("common.js", *extra))
    script = (
        "const vm = require('vm'), fs = require('fs');"
        "const ctx = vm.createContext({window: {APP_BASE: '/stars'}, document: {}});"
        f"{loads}"
        f"console.log(JSON.stringify(vm.runInContext({json.dumps(expression)}, ctx)));"
    )
    r = run_node(script=script)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("n,expected", [(1, "звезда"), (2, "звезды"), (5, "звёзд"), (11, "звёзд"),
                                        (21, "звезда"), (22, "звезды"), (111, "звёзд")])
def test_plural(n, expected):
    assert eval_common(f'plural({n}, "звезда", "звезды", "звёзд")') == expected


def test_escape_html():
    assert eval_common("escapeHtml(`<b onclick=\"x\">'&`)") == "&lt;b onclick=&quot;x&quot;&gt;&#39;&amp;"


def test_asset_url_respects_prefix():
    assert eval_common('assetUrl("/static/img/avatars/fox.svg")') == "/stars/static/img/avatars/fox.svg"
    assert eval_common('assetUrl("https://t.me/i/userpic/1.jpg")') == "https://t.me/i/userpic/1.jpg"
    assert eval_common('assetUrl("")') == ""


def test_error_text_handles_lists():
    """Иначе на экране оказалось бы «[object Object]»."""
    assert eval_common('errorText([{msg: "a"}, {msg: "b"}])') == "a; b"
    assert eval_common('errorText("Готово")') == "Готово"


# ── Отметки преподавателей: помощники панели (static/js/checkin.js) ───────────

CHECKIN = ("schedule.js", "checkin.js")


@pytest.mark.parametrize("text,expected", [
    ("41.311081, 69.279737", {"lat": 41.311081, "lon": 69.279737}),
    ("41.311081 69.279737", {"lat": 41.311081, "lon": 69.279737}),
    ("  41.3;69.2 ", {"lat": 41.3, "lon": 69.2}),
    ("41.311081° N, 69.279737° E", {"lat": 41.311081, "lon": 69.279737}),
    ("https://www.google.com/maps/place/x/@41.3110811,69.2797372,17z/data=x", {"lat": 41.3110811, "lon": 69.2797372}),
    ("", None),
])
def test_parse_coords(text, expected):
    assert eval_common(f"parseCoords({json.dumps(text)})", CHECKIN) == expected


@pytest.mark.parametrize("text", ["Ташкент", "41.3", "141.3, 69.2", "41.3, 200", "41,311 69,279"])
def test_parse_coords_rejects(text):
    result = eval_common(f"(() => {{ try {{ parseCoords({json.dumps(text)}); return 'ok'; }} "
                         f"catch (e) {{ return e.message; }} }})()", CHECKIN)
    assert result != "ok" and len(result) > 20, "понятное объяснение, а не молчание"


def test_entry_active_on_matches_server_rule():
    """Как backend/checkin/domain.entry_active_on: пустое «с» — с того дня, как внесли."""
    entry = '{created_at: "2026-11-10", valid_from: null, valid_to: "2026-12-31"}'
    assert eval_common(f"[entryActiveOn({entry}, '2026-11-09'), entryActiveOn({entry}, '2026-11-10'),"
                       f" entryActiveOn({entry}, '2026-12-31'), entryActiveOn({entry}, '2027-01-01')]",
                       CHECKIN) == [False, True, True, False]
    assert eval_common("entryActiveOn({valid_from: '2026-12-01', created_at: '2026-11-30'}, '2026-11-30')",
                       CHECKIN) is False
    assert eval_common("entryActiveOn({weekday: 1}, '2000-01-01')", CHECKIN) is True


def test_entry_bounds_label_and_day_before():
    assert eval_common("entryBoundsLabel({valid_to: '2026-12-31'}, '2026-11-24')", CHECKIN).startswith("до 31.12")
    assert eval_common("entryBoundsLabel({valid_from: '2026-12-01'}, '2026-11-24')", CHECKIN).startswith("с 01.12")
    assert eval_common("entryBoundsLabel({valid_from: '2026-11-01'}, '2026-11-24')", CHECKIN) == "", \
        "уже действующее «с» не показываем"
    assert eval_common("dayBefore('2026-03-01')", CHECKIN) == "2026-02-28"
    assert eval_common("dayBefore('2027-01-01')", CHECKIN) == "2026-12-31"


def test_invite_state():
    now = "Date.parse('2026-11-24T10:00:00Z')"
    cases = {
        "{tg_id: 900}": "bound",
        "{tg_id: 900, tg_blocked_at: '2026-11-20'}": "blocked",
        "{}": "none",
        "{invite_code: 'x', invite_created_at: '2026-11-20T10:00:00+00:00'}": "pending",
        "{invite_code: 'x', invite_created_at: '2026-11-16T10:00:00+00:00'}": "expired",
    }
    for user, expected in cases.items():
        assert eval_common(f"inviteState({user}, {now})", CHECKIN) == expected, user
