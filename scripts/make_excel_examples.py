"""Собирает примеры Excel в папку «примеры/»: пустой шаблон и заполненный пример.

Запуск из корня проекта:  python scripts/make_excel_examples.py
Формат берётся из backend/excel.py — тот же, что отдаёт кнопка «Скачать шаблон».
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import excel  # noqa: E402

OUT = ROOT / "примеры"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "Шаблон учеников.xlsx").write_bytes(excel.build_template(example=False))
    (OUT / "Пример заполненный.xlsx").write_bytes(excel.build_template(example=True))
    print(f"Готово: {OUT}")


if __name__ == "__main__":
    main()
