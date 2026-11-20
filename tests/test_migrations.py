"""Старые базы доезжают до текущей схемы без потерь."""
import json

from backend import config, db


def write_raw(data):
    config.DB_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


OLD_V2 = {
    "version": 2,
    "classes": ["5-А", "6-Б"],
    "prizes": [{"id": 1, "name": "Брелок", "cost": 12}],
    "students": [
        {"id": 101, "class_name": "5-А", "name": "Иванов Артём", "total_stars": 40,
         "history": [{"date": "2026-10-01", "delta": 40, "reason": "Перенос"}],
         "token": "AAAA2222", "tg_id": 900, "tg_username": "artem",
         "avatar": {"type": "preset", "value": "owl"}},
        {"id": 102, "class_name": "7-В", "name": "Петрова София", "total_stars": 10},
    ],
    "lessons": [{"class_name": "5-А", "date": "2026-10-01", "total_stars": 4}],
}


def test_v2_flat_classes_become_schools():
    write_raw(OLD_V2)
    data = db.load_db()

    assert data["version"] == db.DB_VERSION
    assert len(data["schools"]) == 1
    assert data["schools"][0]["name"] == "Тестовая школа", "имя школы из .env"
    names = {c["name"] for c in data["classes"]}
    assert names == {"5-А", "6-Б", "7-В"}, "неизвестный класс ученика тоже создан"
    assert all(c["school_id"] == data["schools"][0]["id"] for c in data["classes"])


def test_v2_students_keep_everything():
    write_raw(OLD_V2)
    data = db.load_db()
    artem = db.find_student(data, 101)
    assert db.class_name(data, artem["class_id"]) == "5-А"
    assert "class_name" not in artem
    assert artem["total_stars"] == 40
    assert artem["history"][0]["reason"] == "Перенос"
    assert artem["token"] == "AAAA2222", "код карточки не меняется — напечатанные карточки работают"
    assert artem["tg_id"] == 900, "привязка Telegram сохраняется"
    assert artem["avatar"]["value"] == "owl"


def test_v2_students_get_missing_fields():
    write_raw(OLD_V2)
    sofia = db.find_student(db.load_db(), 102)
    assert len(sofia["token"]) == 8
    assert sofia["login"] == "petrova.sofiya"
    assert sofia["password_hash"].startswith("pbkdf2_sha256$")
    assert sofia["password_shown"]
    assert sofia["avatar"]["type"] == "preset"
    assert sofia["history"] == []


def test_v2_lessons_get_class_id():
    write_raw(OLD_V2)
    data = db.load_db()
    assert data["lessons"][0]["class_id"] == db.find_class_by_name(data, 1, "5-А")["id"]


def test_v2_prizes_get_defaults():
    write_raw(OLD_V2)
    prize = db.load_db()["prizes"][0]
    assert prize["active"] is True and prize["category"] == "small" and "image" in prize


def test_migration_is_idempotent():
    write_raw(OLD_V2)
    first = db.load_db()
    mtime = config.DB_FILE.stat().st_mtime_ns
    second = db.load_db()
    assert first == second
    assert config.DB_FILE.stat().st_mtime_ns == mtime, "повторная загрузка не перезаписывает файл"


def test_fresh_database_created_from_defaults():
    assert not config.DB_FILE.exists()
    data = db.load_db()
    assert config.DB_FILE.exists()
    assert {s["id"] for s in data["students"]} == {101, 102, 103, 104}


def test_seed_file_loads():
    """deploy/seed_db.json — то, что кладётся на сервер при первом запуске."""
    seed = (config.BASE_DIR / "deploy" / "seed_db.json").read_text(encoding="utf-8")
    config.DB_FILE.write_text(seed, encoding="utf-8")
    data = db.load_db()
    assert len(data["students"]) == 12
    assert all(s.get("token") and s.get("login") for s in data["students"])
    assert len({s["token"] for s in data["students"]}) == 12


def test_atomic_write_leaves_no_temp_files():
    db.load_db()
    db.transaction(lambda d: None)
    leftovers = [p for p in config.DB_FILE.parent.iterdir() if p.name.startswith(".stars_db_")]
    assert leftovers == []
