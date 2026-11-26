"""Excel: шаблон списка учеников, разбор заполненного файла и выгрузка журнала.

Шаблон и примеры в папке «примеры/» собираются отсюда же
(scripts/make_excel_examples.py), поэтому формат один на всё.

openpyxl при установленном defusedxml сам разбирает XML через него — это
защищает от «XML-бомб» в присланных файлах. Ещё два предохранителя: размер
файла (EXCEL_MAX_BYTES) и число строк, которые мы вообще читаем (_SCAN_ROWS).
"""
import io
from itertools import islice
from typing import Any, Dict, Iterable, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

EXCEL_MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500           # учеников за одну загрузку — как у вставки из Excel
MAX_NAME = 120
MAX_STARS = 100_000
_SCAN_ROWS = 5_000       # дальше не читаем: пустое форматирование может тянуться на миллион строк
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SHEET = "Ученики"
HEADERS = ("Класс", "Фамилия и имя", "Стартовые звёзды")

EXAMPLE_ROWS = [
    ("5-А", "Иванов Артём", 12), ("5-А", "Петрова София", 15),
    ("5-А", "Смирнова Вероника", 8), ("5-А", "Абдуллаев Руслан", 0),
    ("5-А", "Орлов Кирилл", 5), ("5-А", "Жукова Полина", 10),
    ("5-Б", "Каримов Тимур", 20), ("5-Б", "Назаров Даниил", 3),
    ("5-Б", "Белова Ксения", 7), ("5-Б", "Гусев Матвей", 0),
    ("5-Б", "Хасанова Амина", 14), ("5-Б", "Юсупов Бобур", 6),
]

_HEAD_FONT = Font(bold=True, color="FFFFFF")
_HEAD_FILL = PatternFill("solid", fgColor="1F5C45")
_NOTE_FONT = Font(color="6D7872")


class ExcelError(ValueError):
    """Файл не прочитать: текст ошибки показывается учителю как есть."""


def _to_bytes(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _style_header(sheet, widths: Iterable[int]) -> None:
    for index, width in enumerate(widths, start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font = _HEAD_FONT
        cell.fill = _HEAD_FILL
        cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions[cell.column_letter].width = width
    sheet.row_dimensions[1].height = 22
    sheet.freeze_panes = "A2"


# ── Шаблон ───────────────────────────────────────────────────────────────────

def build_template(example: bool = False) -> bytes:
    """Пустой шаблон или пример заполнения (2 класса, 12 учеников)."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    _style_header(sheet, (12, 36, 20))

    # Звёзды — целое число от 0: Excel сам не даст ввести «abc» или -5
    check = DataValidation(type="whole", operator="between", formula1="0", formula2=str(MAX_STARS),
                           allow_blank=True, showErrorMessage=True,
                           errorTitle="Звёзды", error="Целое число от 0. Можно оставить пустым.")
    sheet.add_data_validation(check)
    check.add(f"C2:C{MAX_ROWS + 1}")

    if example:
        for row in EXAMPLE_ROWS:
            sheet.append(row)

    help_sheet = book.create_sheet("Как заполнить")
    lines = [
        ("Как заполнить список учеников", True),
        ("", False),
        ("1. Лист «Ученики»: одна строка — один ученик.", False),
        ("2. «Класс» — как он называется в школе: 5-А, 7-Б, Робототехника.", False),
        ("    Класса ещё нет в журнале — он создастся сам.", False),
        ("    Пусто — ученик попадёт в класс, открытый сейчас в журнале.", False),
        ("3. «Фамилия и имя» — обязательно.", False),
        ("4. «Стартовые звёзды» — сколько звёзд у ученика уже есть. Пусто — 0.", False),
        ("", False),
        (f"За один раз — до {MAX_ROWS} учеников. Строку заголовков не удаляйте.", False),
        ("Логины, пароли и QR-коды журнал выдаст сам после загрузки.", False),
    ]
    for text, bold in lines:
        help_sheet.append([text])
        if bold:
            help_sheet.cell(row=help_sheet.max_row, column=1).font = Font(bold=True, size=13)
    help_sheet.column_dimensions["A"].width = 80
    return _to_bytes(book)


# ── Разбор загруженного файла ───────────────────────────────────────────────

def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _header_columns(row: Tuple[Any, ...]) -> Optional[Dict[str, int]]:
    """Ищет колонки по заголовкам. Нет колонки с именем — это не заголовок."""
    found: Dict[str, int] = {}
    for index, cell in enumerate(row):
        text = _text(cell).lower()
        if not text:
            continue
        if "name" not in found and ("фамил" in text or "фио" in text or text in ("имя", "ученик")):
            found["name"] = index
        elif "class" not in found and ("класс" in text or "групп" in text):
            found["class"] = index
        elif "stars" not in found and ("звёзд" in text or "звезд" in text or "баланс" in text):
            found["stars"] = index
    return found if "name" in found else None


def parse_upload(data: bytes) -> Tuple[List[Tuple[str, str, int]], List[str]]:
    """Возвращает строки (класс, ФИО, звёзды) и список пропущенных с причиной.

    Колонки ищутся по заголовкам, без заголовков — по порядку из шаблона.
    Полностью пустые строки молча пропускаются.
    """
    if len(data) > EXCEL_MAX_BYTES:
        raise ExcelError("Файл слишком большой — не больше 2 МБ")
    if not data.startswith(b"PK"):
        raise ExcelError("Это не файл Excel. Скачайте шаблон и сохраните его как .xlsx")
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ExcelError("Не удалось открыть файл. Сохраните таблицу как .xlsx и попробуйте снова")

    try:
        sheet = book[SHEET] if SHEET in book.sheetnames else book.worksheets[0]
        raw_rows = list(islice(sheet.iter_rows(values_only=True), _SCAN_ROWS))
    except Exception:
        raise ExcelError("Не удалось прочитать лист с учениками")
    finally:
        book.close()

    columns = {"class": 0, "name": 1, "stars": 2}
    start = 0
    for index, row in enumerate(raw_rows[:5]):
        header = _header_columns(row or ())
        if header:
            columns, start = header, index + 1
            break

    def cell(row: Tuple[Any, ...], key: str) -> Any:
        index = columns.get(key)
        return row[index] if index is not None and index < len(row) else None

    rows: List[Tuple[str, str, int]] = []
    skipped: List[str] = []
    for number, row in enumerate(raw_rows[start:], start=start + 1):
        row = row or ()
        if not any(_text(v) for v in row):
            continue
        name = _text(cell(row, "name"))
        class_name = _text(cell(row, "class"))
        stars_raw = _text(cell(row, "stars"))

        if not name:
            skipped.append(f"строка {number}: нет фамилии и имени")
            continue
        if len(name) > MAX_NAME or len(class_name) > MAX_NAME:
            skipped.append(f"строка {number}: слишком длинный текст")
            continue
        try:
            stars = int(float(stars_raw.replace(",", "."))) if stars_raw else 0
        except ValueError:
            skipped.append(f"строка {number}: звёзды «{stars_raw[:20]}» — не число")
            continue
        if stars < 0 or stars > MAX_STARS:
            skipped.append(f"строка {number}: звёзд должно быть от 0 до {MAX_STARS}")
            continue
        rows.append((class_name, name, stars))
        if len(rows) > MAX_ROWS:
            raise ExcelError(f"В файле больше {MAX_ROWS} учеников — разделите его на части")

    return rows, skipped


# ── Выгрузка журнала ─────────────────────────────────────────────────────────

EXPORT_HEADERS = ("Школа", "Класс", "Фамилия и имя", "Звёзды", "Логин", "Код карточки", "Telegram")


def build_export(rows: Iterable[Tuple[str, str, str, int, str, str, str]]) -> bytes:
    """Журнал для отчёта. Паролей здесь нет: файл легко переслать дальше,
    а пароли печатаются на карточках."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Журнал"
    sheet.append(EXPORT_HEADERS)
    _style_header(sheet, (22, 12, 34, 10, 22, 14, 14))
    count = 0
    for row in rows:
        sheet.append(list(row))
        count += 1
        # openpyxl считает формулой любую строку, начинающуюся с «=». Имя ученика
        # вводит человек, поэтому храним такие значения строго как текст.
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"
    sheet.auto_filter.ref = f"A1:G{count + 1}"
    return _to_bytes(book)


# ── Мониторинг: итоги опроса ─────────────────────────────────────────────────

def _as_text(sheet) -> None:
    """Ответы пишут дети: строка с «=» в начале — текст, а не формула."""
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.data_type = "s"


# ── Отчёт по отметкам преподавателей ─────────────────────────────────────────

def _append_text_safe(sheet, values) -> None:
    """Строка в лист; текст, начинающийся с «=», остаётся текстом, а не формулой."""
    sheet.append(list(values))
    _as_text(sheet)


def build_hours_report(report: Dict[str, Any], title: str) -> bytes:
    """Месячный отчёт: преподаватели (часы и суммы), школы (часы для счёта),
    уроки по одному — чтобы сверить с ручным подсчётом."""
    book = Workbook()
    teachers = book.active
    teachers.title = "Преподаватели"
    teachers.append(["Преподаватель", "Уроков по плану", "Подтверждено", "Опозданий", "Минут опоздания",
                     "Не засчитано", "Ждут решения", "Отменено", "Часы", "Ставка, сум/ч", "Сумма, сум"])
    _style_header(teachers, (30, 14, 14, 12, 14, 13, 13, 11, 10, 14, 14))
    for row in report["teachers"]:
        _append_text_safe(teachers, [row["name"], row["planned"], row["confirmed"], row["late"],
                                     row["late_minutes"], row["rejected"], row["pending"], row["cancelled"],
                                     row["hours"], row["rate"], row["amount"]])
    totals = report["totals"]
    teachers.append(["Итого", totals["lessons"], totals["confirmed"], totals["late"], None, None,
                     totals["pending"], None, totals["hours"], None, totals["amount"]])
    for cell in teachers[teachers.max_row]:
        cell.font = Font(bold=True)

    schools = book.create_sheet("Школы")
    schools.append(["Школа", "Преподаватель", "Уроков засчитано", "Часы"])
    _style_header(schools, (30, 30, 16, 10))
    for school in report["schools"]:
        _append_text_safe(schools, [school["name"], "Всего", school["lessons"], school["hours"]])
        for cell in schools[schools.max_row]:
            cell.font = Font(bold=True)
        for teacher in school["teachers"]:
            _append_text_safe(schools, ["", teacher["name"], None, teacher["hours"]])

    lessons = book.create_sheet("Уроки")
    lessons.append(["Дата", "Начало", "Конец", "Школа", "Класс", "Преподаватель", "Статус",
                    "Опоздание, мин", "Часы", "Причина проверки", "Примечание"])
    _style_header(lessons, (12, 8, 8, 26, 10, 28, 14, 14, 8, 30, 30))
    for item in report.get("lessons", []):
        _append_text_safe(lessons, [item["date"], item["start"], item["end"], item["school"], item["class"],
                                    item["teacher"], item["status_label"], item["late_minutes"], item["hours"],
                                    ", ".join(item["flag_labels"]), item["note"]])
    lessons.auto_filter.ref = f"A1:K{lessons.max_row}"
    book.properties.title = title
    return _to_bytes(book)


def build_monitoring_export(report: Dict[str, Any]) -> bytes:
    """Два листа: «Сводка» (доли по вариантам, среднее, тексты) и «Ответы» (участник × вопрос)."""
    book = Workbook()
    summary = book.active
    summary.title = "Сводка"
    summary.append(("Вопрос", "Ответ", "Сколько", "Доля", "Среднее"))
    _style_header(summary, (52, 40, 10, 10, 10))
    for item in report["results"]:
        title = f"{item['std'] + '. ' if item.get('std') else ''}{item['text']}"
        n = item["answered"]
        first = True
        for option in item.get("options", []):
            share = round(option["count"] / n, 3) if n else 0
            summary.append((title if first else "", option["label"], option["count"], share,
                            item.get("average") if first else None))
            summary.cell(row=summary.max_row, column=4).number_format = "0%"
            _as_text(summary)
            first = False
        for text in item.get("texts", []):
            summary.append((title if first else "", text["text"], None, None, None))
            _as_text(summary)
            first = False
        if first:
            summary.append((title, "нет ответов", 0, None, None))

    sheet = book.create_sheet("Ответы")
    columns = report["columns"]
    head = ["Фамилия и имя", "Класс", "Гость", "Закончил"] + (["Баллы"] if report.get("is_test") else [])
    sheet.append(head + [f"{c['std'] + '. ' if c.get('std') else ''}{c['text']}" for c in columns])
    _style_header(sheet, [30, 10, 8, 10] + ([8] if report.get("is_test") else []) + [28] * len(columns))
    for row in report["rows"]:
        values = [row["name"], row["class_name"], "да" if row["guest"] else "", "да" if row["finished"] else "нет"]
        if report.get("is_test"):
            values.append(row["score"])
        values += [row["answers"].get(c["id"], {}).get("text", "") for c in columns]
        sheet.append(values)
        _as_text(sheet)
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=1, column=len(head) + len(columns)).column_letter}{sheet.max_row}"
    return _to_bytes(book)

