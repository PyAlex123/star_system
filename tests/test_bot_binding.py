"""Привязка Telegram-аккаунта к ученику по коду с карточки (без живого бота)."""
from backend.bot.handlers import CODE_RE, bind_by_code
from conftest import student_record


def code_of(student_id):
    return student_record(student_id)["token"]


def test_unknown_code():
    assert bind_by_code("NOSUCH99", 1, None) == ("not_found", None)


def test_first_binding():
    status, student = bind_by_code(code_of(101), 7001, "kid")
    assert status == "ok" and student["id"] == 101
    s = student_record(101)
    assert s["tg_id"] == 7001 and s["tg_username"] == "kid" and s["bound_at"]


def test_code_case_insensitive():
    status, _ = bind_by_code(code_of(101).lower(), 7001, None)
    assert status == "ok"


def test_same_account_again():
    bind_by_code(code_of(101), 7001, "kid")
    status, _ = bind_by_code(code_of(101), 7001, "kid_renamed")
    assert status == "already_me"
    assert student_record(101)["tg_username"] == "kid_renamed"


def test_card_taken_by_other_account():
    bind_by_code(code_of(101), 7001, "kid")
    status, _ = bind_by_code(code_of(101), 7002, "stranger")
    assert status == "taken"
    assert student_record(101)["tg_id"] == 7001


def test_code_pattern_accepts_card_codes_only():
    assert CODE_RE.match("PZ8JTFRQ")
    assert not CODE_RE.match("привет")
    assert not CODE_RE.match("hi")
