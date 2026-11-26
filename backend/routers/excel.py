"""Excel в панели: скачать шаблон, загрузить заполненный, выгрузить журнал."""
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from .. import db, excel
from ..security import require_teacher
from .common import bad_request, school_or_404
from .students import import_rows

router = APIRouter(prefix="/api/excel", tags=["excel"])


def _xlsx(data: bytes, filename: str, fallback: str = "students.xlsx") -> Response:
    quoted = urllib.parse.quote(filename)
    return Response(data, media_type=excel.XLSX_TYPE, headers={
        # Русское имя файла — по RFC 5987; ASCII-запасное для старых браузеров
        "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quoted}",
        "Cache-Control": "no-store",
    })


@router.get("/template", dependencies=[Depends(require_teacher)])
def download_template(example: bool = False):
    name = "Пример заполненный.xlsx" if example else "Шаблон учеников.xlsx"
    return _xlsx(excel.build_template(example=example), name)


@router.post("/import")
def upload_students(school_id: int, class_name: str = "",
                    file: UploadFile = File(...),
                    user: Dict[str, Any] = Depends(require_teacher)):
    """Ученики из заполненного шаблона — в выбранную школу.

    Пустой класс в строке — класс, открытый сейчас в журнале (class_name).
    Сначала разбираем файл целиком и только потом пишем в базу: при ошибке
    в файле не добавится половина класса.
    """
    school_or_404(db.load_db(), school_id, user)
    data = file.file.read(excel.EXCEL_MAX_BYTES + 1)
    try:
        rows, skipped = excel.parse_upload(data)
    except excel.ExcelError as exc:
        raise bad_request(str(exc))
    if not rows:
        raise bad_request("В файле не нашлось ни одного ученика" +
                          (f": {skipped[0]}" if skipped else ""))

    fallback = (class_name or "").strip()[:excel.MAX_NAME] or "Общий"
    added = import_rows([(school_id, cls or fallback, name, stars) for cls, name, stars in rows], user)
    return {"ok": True, "added": added, "skipped": skipped}


@router.get("/export")
def download_journal(school_id: Optional[int] = None,
                     user: Dict[str, Any] = Depends(require_teacher)):
    """Журнал выбранной школы (или всех своих) — для отчёта."""
    database = db.load_db()
    schools = db.allowed_school_ids(database, user)
    if school_id is not None:
        school_or_404(database, school_id, user)
        schools = {school_id}

    school_names = {s["id"]: s["name"] for s in database["schools"]}
    classes = {c["id"]: c for c in database["classes"] if c["school_id"] in schools}
    rows = []
    for student in database["students"]:
        cls = classes.get(student.get("class_id"))
        if not cls:
            continue
        rows.append((school_names.get(cls["school_id"], ""), cls["name"], student["name"],
                     student.get("total_stars", 0), student.get("login") or "",
                     student.get("token") or "", "привязан" if student.get("tg_id") else ""))
    rows.sort(key=lambda r: (r[0].lower(), r[1].lower(), r[2].lower()))
    return _xlsx(excel.build_export(rows), f"Журнал звёзд {db.today()}.xlsx")
