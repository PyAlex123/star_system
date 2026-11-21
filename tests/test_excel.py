"""Excel: шаблон, загрузка заполненного файла, выгрузка журнала."""
import io
from pathlib import Path

from openpyxl import Workbook, load_workbook

from backend import excel
from conftest import load_db

EXAMPLES = Path(__file__).resolve().parent.parent / "примеры"


def xlsx(rows, headers=excel.HEADERS, sheet_title=excel.SHEET) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title
    if headers:
        sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def upload(client, data: bytes, school_id=1, class_name="5-А", filename="list.xlsx"):
    return client.post(f"/api/excel/import?school_id={school_id}&class_name={class_name}",
                       files={"file": (filename, data, excel.XLSX_TYPE)})


# ── Шаблон ───────────────────────────────────────────────────────────────────

def test_template_download(teacher):
    r = teacher.get("/api/excel/template")
    assert r.status_code == 200
    assert r.headers["content-type"] == excel.XLSX_TYPE
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    book = load_workbook(io.BytesIO(r.content))
    assert [c.value for c in book[excel.SHEET][1]] == list(excel.HEADERS)
    assert book[excel.SHEET].max_row == 1, "шаблон пустой"
    assert "Как заполнить" in book.sheetnames


def test_template_needs_login(client):
    assert client.get("/api/excel/template").status_code == 401


def test_example_files_in_repo_match_generator():
    """Примеры в репозитории собраны из того же кода и читаются им же."""
    for name, rows in (("Шаблон учеников.xlsx", 0), ("Пример заполненный.xlsx", 12)):
        data = (EXAMPLES / name).read_bytes()
        parsed, skipped = excel.parse_upload(data)
        assert len(parsed) == rows and skipped == []


# ── Загрузка ─────────────────────────────────────────────────────────────────

def test_filled_example_imports_whole(teacher):
    r = upload(teacher, (EXAMPLES / "Пример заполненный.xlsx").read_bytes())
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "added": 12, "skipped": []}
    data = load_db()
    new = [s for s in data["students"] if s["name"] == "Юсупов Бобур"]
    assert len(new) == 1 and new[0]["total_stars"] == 6 and new[0]["login"]
    cls = next(c for c in data["classes"] if c["id"] == new[0]["class_id"])
    assert cls["name"] == "5-Б" and cls["school_id"] == 1


def test_bad_rows_listed_good_rows_added(teacher):
    data = xlsx([
        ("5-А", "Новиков Илья", 3),
        (None, None, None),                 # пустая строка — молча
        ("5-А", None, 4),                   # без имени
        ("5-А", "Лебедев Глеб", "много"),   # звёзды не число
        ("5-А", "Соколова Ева", -2),
        (None, "Морозов Лев", None),        # без класса — в открытый класс, 0 звёзд
    ])
    r = upload(teacher, data, class_name="6-А")
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 2
    skipped = r.json()["skipped"]
    assert any("строка 4" in s and "фамилии" in s for s in skipped)
    assert any("строка 5" in s and "не число" in s for s in skipped)
    assert any("строка 6" in s for s in skipped)
    lev = next(s for s in load_db()["students"] if s["name"] == "Морозов Лев")
    assert lev["class_id"] == 3 and lev["total_stars"] == 0


def test_columns_found_by_header_in_any_order(teacher):
    data = xlsx([(7, "Зайцев Ян", "5-Б")], headers=("Звёзды", "ФИО", "Класс"))
    assert upload(teacher, data).json()["added"] == 1
    yan = next(s for s in load_db()["students"] if s["name"] == "Зайцев Ян")
    assert yan["total_stars"] == 7 and yan["class_id"] == 2


def test_not_excel_rejected(teacher):
    r = upload(teacher, "Класс;ФИО\n5-А;Иванов".encode("utf-8"), filename="list.csv")
    assert r.status_code == 400
    assert "не файл Excel" in r.json()["detail"]


def test_broken_zip_rejected(teacher):
    assert upload(teacher, b"PK\x03\x04 broken").status_code == 400


def test_too_many_rows(teacher):
    data = xlsx([("5-А", f"Ученик {i}", 0) for i in range(excel.MAX_ROWS + 1)])
    r = upload(teacher, data)
    assert r.status_code == 400
    assert str(excel.MAX_ROWS) in r.json()["detail"]
    assert len(load_db()["students"]) == 4, "ничего не добавлено"


def test_too_big_file(teacher):
    r = upload(teacher, b"PK" + b"0" * (excel.EXCEL_MAX_BYTES + 10))
    assert r.status_code == 400


def test_empty_file_rejected(teacher):
    r = upload(teacher, xlsx([]))
    assert r.status_code == 400
    assert len(load_db()["students"]) == 4


# ── Выгрузка ─────────────────────────────────────────────────────────────────

def test_export_has_logins_but_no_passwords(teacher):
    teacher.post("/api/students/101/password", json={"password": "sekretik-1"})
    r = teacher.get("/api/excel/export?school_id=1")
    assert r.status_code == 200
    book = load_workbook(io.BytesIO(r.content))
    rows = list(book.active.iter_rows(values_only=True))
    assert rows[0] == excel.EXPORT_HEADERS
    assert len(rows) == 5
    flat = str(rows)
    assert "ivanov.artem" in flat
    assert "sekretik-1" not in flat


def test_export_formula_names_stay_text(teacher):
    teacher.post("/api/students", json={"class_id": 1, "name": "=HYPERLINK(\"http://x\")"})
    book = load_workbook(io.BytesIO(teacher.get("/api/excel/export").content))
    cell = next(c for row in book.active.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=HYPER"))
    assert cell.data_type == "s"
