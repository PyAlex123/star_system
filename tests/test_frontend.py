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


def eval_schedule(expression: str):
    """Чистые функции расписания: schedule.js без страницы (DOM не нужен)."""
    script = (
        "const vm = require('vm'), fs = require('fs');"
        "const ctx = vm.createContext({window: {APP_BASE: ''}, document: {}});"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(JS / 'schedule.js'))}, 'utf8'), ctx);"
        f"console.log(JSON.stringify(vm.runInContext({json.dumps(expression)}, ctx)));"
    )
    r = run_node(script=script)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_make_bells():
    assert eval_schedule('makeBells("08:00", 45, 10, 3)') == [
        {"start": "08:00", "end": "08:45"}, {"start": "08:55", "end": "09:40"}, {"start": "09:50", "end": "10:35"}]
    assert len(eval_schedule('makeBells("22:00", 45, 15, 5)')) == 2, "за полночь не уходим"


def test_bells_error():
    assert eval_schedule('bellsError([{start: "08:00", end: "08:45"}, {start: "08:55", end: "09:40"}])') == ""
    assert eval_schedule('bellsError([])') == "Нужен хотя бы один урок"
    assert "2-й урок начинается раньше" in eval_schedule(
        'bellsError([{start: "08:00", end: "08:45"}, {start: "08:30", end: "09:15"}])')
    assert "позже" in eval_schedule('bellsError([{start: "09:00", end: "08:00"}])')
    assert "Заполните" in eval_schedule('bellsError([{start: "", end: "08:00"}])')


def test_visible_days():
    assert eval_schedule("visibleDays(5, [])") == 5
    assert eval_schedule("visibleDays(5, [{weekday: 6}])") == 6, "занятие в субботу видно и без учебной субботы"
    assert eval_schedule("visibleDays(6, [{weekday: 2}])") == 6


def test_combined_name():
    """Объединённые классы в клетке таблицы — коротко, как пишут в школьном расписании."""
    assert eval_schedule('combinedName(["5-А"])') == "5-А"
    assert eval_schedule('combinedName(["8-А", "8-Б", "8-В"])') == "8-АБВ"
    assert eval_schedule('combinedName(["7-А", "7-В", "9-Б"])') == "7-АВ + 9-Б"
    assert eval_schedule('combinedName(["10А", "Кружок робототехники"])') == "10-А + Кружок робототехники"


def test_error_text_handles_lists():
    """Иначе на экране оказалось бы «[object Object]»."""
    assert eval_common('errorText([{msg: "a"}, {msg: "b"}])') == "a; b"
    assert eval_common('errorText("Готово")') == "Готово"


def test_parallel_of_matches_server():
    """Плитки классов делят классы на параллели по тому же правилу, что статистика сервера."""
    from backend import stats
    for name in ["5-А", "5 Б", "10-Ф1", " 7 Б", "Робототехника", "123", "11А", ""]:
        assert eval_common(f"parallelOf({json.dumps(name)})") == stats.parallel_of(name), name


def test_group_by_parallel():
    groups = eval_common('groupByParallel([{name: "10-А"}, {name: "Кружок"}, {name: "2-Б"}, {name: "2-А"}, {name: "9-В"}])'
                         '.map(g => [g.parallel, g.classes.map(c => c.name)])')
    assert groups == [[2, ["2-Б", "2-А"]], [9, ["9-В"]], [10, ["10-А"]], [None, ["Кружок"]]]


def test_lesson_tile_state():
    day = "2026-11-26"
    call = lambda date, now, held=False: eval_schedule(
        f'lessonTileState("10:00", "10:45", "{date}", "{day}", {now}, {str(held).lower()})')
    assert call(day, 9 * 60) == "later"
    assert call(day, 10 * 60 + 5) == "now"
    assert call(day, 11 * 60) == "past", "время прошло, урок не отмечен"
    assert call(day, 11 * 60, held=True) == "done"
    assert call("2026-11-25", 9 * 60) == "past"
    assert call("2026-11-27", 23 * 60) == "later"


def test_initials():
    assert eval_common('initials("Иванова Мария Сергеевна")') == "ИМ"
    assert eval_common('initials("Алишер")') == "А"
    assert eval_common('initials("  ali  valiyev ")') == "AV"
    assert eval_common('initials("")') == "?"
    assert eval_common('initials("— Учитель")') == "У", "знаки без букв пропускаются"
