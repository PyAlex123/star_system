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


@pytest.mark.parametrize("page_js,page_html", [("app.js", "app.html"), ("teacher.js", "teacher.html")])
def test_every_handler_in_markup_exists(page_js, page_html):
    """Каждая функция, на которую ссылается кнопка в разметке, объявлена в скриптах."""
    names = sorted(html_handlers(page_html))
    assert len(names) > 5, "в разметке почти нет обработчиков — проверка сломалась"
    r = run_node(ROOT / "tests" / "js_load_check.js", JS / "common.js", JS / page_js, *names)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("name", ["common.js", "app.js", "teacher.js"])
def test_syntax(name):
    r = run_node("--check", JS / name)
    assert r.returncode == 0, r.stderr


def eval_common(expression: str):
    script = (
        "const vm = require('vm'), fs = require('fs');"
        "const ctx = vm.createContext({window: {APP_BASE: '/stars'}, document: {}});"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(JS / 'common.js'))}, 'utf8'), ctx);"
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
