"""Новый пароль пользователю панели — если забыли пароль администратора.

На сервере:
    docker compose exec school-stars python scripts/reset_user_password.py admin
Локально:
    python scripts/reset_user_password.py admin [новый-пароль]

Без второго аргумента пароль придумывается и печатается. Все входы этого
пользователя завершаются.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import db, passwords  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    login = passwords.normalize_login(sys.argv[1])
    new = sys.argv[2] if len(sys.argv) > 2 else passwords.new_password()
    try:
        new = passwords.validate_new(new)
    except ValueError as exc:
        print(f"Ошибка: {exc}")
        return 1

    def _apply(database):
        user = db.find_user_by_login(database, login)
        if not user:
            return None
        user["password_hash"] = passwords.hash_password(new)
        user["session_key"] = db.new_session_key()
        return user

    user = db.transaction(_apply)
    if not user:
        known = ", ".join(u["login"] for u in db.load_db().get("users", []))
        print(f"Пользователя «{login}» нет. Есть: {known}")
        return 1
    print(f"Новый пароль для {login}: {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
