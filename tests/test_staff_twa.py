"""Панель учителя как Telegram Web App: кнопка «Панель» в боте открывает /login?tg=1,
страница входит по подписи Telegram — без логина и пароля."""
from aiogram.methods import SendMessage, SetChatMenuButton
from aiogram.types import InlineKeyboardMarkup, MenuButtonCommands, MenuButtonWebApp
from fastapi.testclient import TestClient

from backend import db
from backend.main import app
from conftest import TEACHER_LOGIN, TEACHER_PASSWORD, load_db, panel_client, signed_init_data
from test_checkin_bot import TG, TeacherChat, prepare

STAFF = {"id": TG, "first_name": "Марина", "username": "uchitel"}
HTTPS = "https://stars.example.uz"


def bind(user_id=2, tg_id=TG):
    def _apply(database):
        db.find_user(database, user_id).update(tg_id=tg_id, tg_username="uchitel")
    db.transaction(_apply)


def twa(client, init_data):
    return client.post("/api/auth/teacher/twa", json={"init_data": init_data})


# ── Вход по подписи Telegram ─────────────────────────────────────────────────

def test_bound_staff_opens_panel_without_password():
    bind()
    client = TestClient(app)
    r = twa(client, signed_init_data(STAFF))
    assert r.status_code == 200 and r.json()["role"] == "teacher"
    state = client.get("/api/state").json()
    assert state["me"]["id"] == 2, "в панели — тот, к кому привязан Telegram"
    assert client.get("/teacher").status_code == 200


def test_unbound_telegram_is_told_to_scan_card():
    r = twa(TestClient(app), signed_init_data(STAFF))
    assert r.status_code == 403 and "карточки сотрудника" in r.json()["detail"]


def test_forged_or_foreign_signature_rejected():
    bind()
    client = TestClient(app)
    forged = signed_init_data(STAFF).replace("uchitel", "hacker")
    assert twa(client, forged).status_code == 401
    assert twa(client, signed_init_data(STAFF, bot_token="999:other")).status_code == 401
    assert twa(client, "").status_code == 401
    assert client.get("/api/state").status_code == 401, "сессии нет"


def test_student_telegram_does_not_open_panel():
    """Telegram ученика — не сотрудник: кабинет ученика да, панель нет."""
    def _apply(database):
        db.find_student(database, 101)["tg_id"] = TG
    db.transaction(_apply)
    assert twa(TestClient(app), signed_init_data(STAFF)).status_code == 403


def test_unbind_closes_panel_opened_from_telegram(teacher):
    bind()
    client = TestClient(app)
    assert twa(client, signed_init_data(STAFF)).status_code == 200
    assert teacher.delete("/api/users/2/telegram").status_code == 200
    assert client.get("/api/state").status_code == 401, "Telegram отвязали — панель из бота закрылась"
    password = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert password.get("/api/state").status_code == 200, "по паролю учитель входит, как раньше"


def test_new_code_closes_panel_too(teacher):
    bind()
    client = TestClient(app)
    twa(client, signed_init_data(STAFF))
    assert teacher.post("/api/users/2/code").status_code == 200
    assert client.get("/api/state").status_code == 401


def test_admin_unbinding_self_stays_logged_in(teacher):
    bind(user_id=1, tg_id=901)
    assert teacher.delete("/api/users/1/telegram").status_code == 200
    assert teacher.get("/api/state").status_code == 200, "себе администратор сессию не обрывает"


# ── Кнопка «Панель» в боте ───────────────────────────────────────────────────

def menu_buttons(chat):
    return [c.menu_button for c in chat.session.calls if isinstance(c, SetChatMenuButton)]


def test_menu_button_opens_panel_over_https(monkeypatch):
    monkeypatch.setenv("BASE_URL", HTTPS)
    prepare()
    chat = TeacherChat()
    chat.text("/start")
    button = menu_buttons(chat)[-1]
    assert isinstance(button, MenuButtonWebApp) and button.text == "Панель"
    assert button.web_app.url == f"{HTTPS}/login?tg=1"


def test_menu_button_is_commands_without_https():
    prepare()
    chat = TeacherChat()
    chat.text("/start")
    assert isinstance(menu_buttons(chat)[-1], MenuButtonCommands), "Telegram открывает только https"


def keys(keyboard):
    return [b.text for row in keyboard.keyboard for b in row]


def test_https_menu_has_no_second_panel_key(monkeypatch):
    """По https панель открывает кнопка «Панель» у поля ввода — клавиши «🖥 Панель» в меню нет."""
    monkeypatch.setenv("BASE_URL", HTTPS)
    prepare()
    chat = TeacherChat()
    chat.text("/start")
    texts = keys(chat.keyboard_of_last())
    assert "🖥 Панель" not in texts and "🌐 Язык" in texts


def test_without_https_menu_keeps_panel_key():
    prepare()
    chat = TeacherChat()
    chat.text("/start")
    assert "🖥 Панель" in keys(chat.keyboard_of_last()), "без https кнопки у поля ввода нет — клавиша остаётся"


def test_old_panel_key_still_sends_web_app_button(monkeypatch):
    """У кого осталась старая клавиатура — нажатие «🖥 Панель» по-прежнему открывает панель."""
    monkeypatch.setenv("BASE_URL", HTTPS)
    prepare()
    chat = TeacherChat()
    chat.text("/start")
    replies = chat.text("🖥 Панель")
    assert "Панель учителя" in replies[0]
    markup = [c for c in chat.session.calls if isinstance(c, SendMessage)][-1].reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    assert markup.inline_keyboard[0][0].web_app.url == f"{HTTPS}/login?tg=1"


def test_panel_key_without_https_gives_site_address():
    prepare()
    replies = TeacherChat().text("🖥 Панель")
    assert "/login" in replies[0]


def test_staff_without_checkins_still_gets_panel(monkeypatch):
    """Директору и завучу отметки не включены, а панель нужна — кнопка в том же сообщении."""
    monkeypatch.setenv("BASE_URL", HTTPS)
    prepare(tracked=False)
    chat = TeacherChat()
    replies = chat.text("/start")
    assert len(replies) == 1 and "не отмечаетесь" in replies[0]
    markup = [c for c in chat.session.calls if isinstance(c, SendMessage)][-1].reply_markup
    assert markup.inline_keyboard[0][0].text == "Открыть панель"


def test_language_switch_renames_menu_button(monkeypatch):
    monkeypatch.setenv("BASE_URL", HTTPS)
    prepare(consent=False)
    chat = TeacherChat()
    chat.press("lang:uz")
    assert menu_buttons(chat)[-1].text == "Panel"
    assert db.find_user(load_db(), 2)["lang"] == "uz"
