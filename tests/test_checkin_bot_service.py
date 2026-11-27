"""Бот преподавателей, слой сервиса: привязка по коду сотрудника, язык, согласие, блокировка,
«мои уроки сегодня» и «мои часы за месяц» — без Telegram."""
from datetime import date

from backend import db
from backend.checkin import clock, locales, service, store
from conftest import load_db

MON = date(2026, 11, 23)


def staff_code(user_id=2, code="TCHR2345"):
    """Код для бота у сотрудника — как на карточке."""
    def _apply(database):
        db.find_user(database, user_id).update(bot_code=code, checkin_tracked=True)
    db.transaction(_apply)
    return code


def test_code_binds_first_account_and_stays():
    code = staff_code()
    status, user = service.bind_code(code, 900, "uchitel")
    assert status == "ok" and user["id"] == 2
    saved = db.find_user(load_db(), 2)
    assert (saved["tg_id"], saved["tg_username"], saved["bot_code"]) == (900, "uchitel", code), "код остаётся"
    assert saved["tg_bound_at"]
    assert service.bind_code(code, 900, "uchitel")[0] == "already_me", "свой код ещё раз — свой"
    assert service.bind_code(code, 901, "chuzhoy") == ("taken", None), "второй аккаунт код не заберёт"
    with store.tx() as conn:
        audit = store.row(conn.execute("SELECT * FROM audit_log WHERE action = 'telegram_bound'").fetchone())
    assert store.loads(audit["after"])["tg_id"] == 900 and audit["user_name"] == "Учитель"


def test_code_is_read_as_typed():
    code = staff_code()
    assert service.bind_code("tchr 2345", 900, None)[0] == "ok"
    assert service.bind_code("TCHR-2345", 900, None)[0] == "already_me"
    assert service.user_by_code(" tchr2345 ")["id"] == 2
    assert service.bind_code("nope", 901, None) == ("not_found", None)
    assert service.user_by_code("") is None


def test_one_telegram_one_employee():
    def _bound(database):
        db.find_user(database, 1)["tg_id"] = 900
    db.transaction(_bound)
    code = staff_code()
    assert service.bind_code(code, 900, None) == ("other_user", None)
    assert db.find_user(load_db(), 2)["tg_id"] is None, "чужая попытка не привязывает"


def test_lang_consent_and_block():
    code = staff_code()
    service.bind_code(code, 900, None)
    assert service.set_lang(2, "uz")["lang"] == "uz"
    assert service.set_lang(2, "fr")["lang"] == "ru", "неизвестный язык — русский"
    assert service.give_consent(2)["consent_at"]
    service.set_blocked(900, True)
    assert db.find_user(load_db(), 2)["tg_blocked_at"]
    service.set_blocked(900, False)
    assert db.find_user(load_db(), 2)["tg_blocked_at"] is None
    service.set_blocked(12345, True)            # незнакомый Telegram — ничего не ломает


def _schedule():
    def _apply(database):
        db.find_user(database, 2).update(checkin_tracked=True, tg_id=900, consent_at="2026-11-01T00:00:00+00:00")
        database["schools"][0].update(lat=41.311, lon=69.279)
        database["schedule"] = [
            {"id": 1, "class_id": 1, "weekday": 1, "start": "09:00", "end": "09:45", "room": "", "user_id": 2,
             "created_at": "2026-01-01", "valid_from": None, "valid_to": None},
            {"id": 2, "class_id": 2, "weekday": 1, "start": "11:00", "end": "11:45", "room": "", "user_id": 2,
             "created_at": "2026-01-01", "valid_from": None, "valid_to": None},
        ]
    db.transaction(_apply)


def test_today_and_month_for_teacher():
    _schedule()
    clock.freeze(clock.at(MON, "07:00"))
    service.tick()
    clock.freeze(clock.at(MON, "08:55"))
    service.start_checkin(2, 41.311, 69.279, clock.now_utc())
    service.finish_checkin(2, "video_note", False, "f", "u", clock.now_utc())
    today = service.today_for(2)
    assert today["date"] == MON
    assert [(l["start"], l["status"]) for l in today["lessons"]] == [("09:00", "checked_in"), ("11:00", "planned")]
    month = service.month_for(2)
    assert (month["year"], month["month"], month["confirmed"], month["minutes"]) == (2026, 11, 1, 45)
    assert "amount" not in month, "сумму преподаватель в боте не видит"
    assert service.month_for(1)["planned"] == 0


def test_locales_have_same_keys_and_placeholders():
    import string
    ru, uz = locales.LANGS["ru"], locales.LANGS["uz"]
    assert set(ru) == set(uz)
    for key in ru:
        fields = lambda text: {f for _, f, _, _ in string.Formatter().parse(text) if f}
        assert fields(ru[key]) == fields(uz[key]), key
    assert locales.minutes("ru", 2) == "2 минуты" and locales.minutes("ru", 5) == "5 минут"
    assert locales.minutes("ru", 21) == "21 минуту" and locales.minutes("ru", 12) == "12 минут"
    assert locales.minutes("uz", 2) == "2 daqiqa"
    assert locales.t("fr", "menu_here") == "📍 Я на месте"
    assert locales.month_title("uz", 2026, 11) == "2026-yil noyabr"
    assert locales.day_title("ru", MON) == "23 ноября"
