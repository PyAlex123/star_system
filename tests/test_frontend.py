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


def test_entry_shown_on_ignores_created_at():
    """Таблица расписания (как _shown_on на сервере): поставленное в среду на понедельник
    видно и в понедельник этой недели; явный срок «с … по …» действует."""
    entry = '{created_at: "2026-11-25", valid_from: null, valid_to: "2026-12-31"}'
    assert eval_common(f"[entryShownOn({entry}, '2026-11-23'), entryShownOn({entry}, '2027-01-01'),"
                       f" entryShownOn({{valid_from: '2026-12-07'}}, '2026-11-30')]",
                       CHECKIN) == [True, False, False]


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


# ── Раздел «Отметки» (static/js/checkin-page.js) ─────────────────────────────

PAGE = ("schedule.js", "checkin.js", "checkin-page.js")


def test_tg_message_link_only_for_supergroups():
    assert eval_common("tgMessageLink(-1001234567890, 42)", PAGE) == "https://t.me/c/1234567890/42"
    assert eval_common("tgMessageLink('-1001234567890', 42)", PAGE) == "https://t.me/c/1234567890/42"
    assert eval_common("tgMessageLink(-4321, 42)", PAGE) == "", "у обычной группы ссылки на сообщение нет"
    assert eval_common("tgMessageLink(-1001234567890, 0)", PAGE) == ""


def test_month_and_day_shift():
    assert eval_common("[shiftMonth('2026-12', 1), shiftMonth('2026-01', -1), shiftMonth('2026-11', 0)]", PAGE) == \
        ["2027-01", "2025-12", "2026-11"]
    assert eval_common("[shiftDay('2026-12-31', 1), shiftDay('2026-03-01', -1)]", PAGE) == ["2027-01-01", "2026-02-28"]
    assert eval_common("monthLabel('2026-11')", PAGE) == "ноябрь 2026"


def test_state_labels_and_hours():
    assert eval_common("[ckStateLabel('missing'), ckStateLabel('review'), ckStateLabel('x')]", PAGE) == \
        ["нет отметки", "на проверке", "x"]
    assert eval_common("[ckHours(0.75), ckHours(2), ckHours(0.8333)]", PAGE) == ["0,75", "2", "0,83"]


def test_audit_summary_in_words():
    item = ("{entity: 'lesson', action: 'approve', before: {status: 'no_checkin', hours_override: null},"
            " after: {status: 'approved', hours_override: null}}")
    assert eval_common(f"auditSummary({item})", PAGE) == {
        "what": "засчитал урок", "changes": ["статус: нет отметки → засчитан"]}
    rate = "{entity: 'user', action: 'update', before: {hourly_rate: 80000}, after: {hourly_rate: 90000}}"
    summary = eval_common(f"auditSummary({rate})", PAGE)
    assert summary["what"] == "изменил сотрудника" and summary["changes"][0].startswith("ставка: 80")
    unknown = "{entity: 'x', action: 'y', before: null, after: null}"
    assert eval_common(f"auditSummary({unknown})", PAGE) == {"what": "x: y", "changes": []}
