"""Запуск сервера.

Код приложения живёт в пакете backend/. Этот файл оставлен точкой входа,
чтобы привычные команды продолжали работать:

    python app.py
    uvicorn app:app --reload
"""
from backend.main import app  # noqa: F401  (имя app нужно uvicorn)

if __name__ == "__main__":
    import uvicorn

    # reload выключен намеренно: при перезапуске процесса Telegram отдаёт
    # ошибку "terminated by other getUpdates request" второму боту.
    uvicorn.run(app, host="127.0.0.1", port=8000)
