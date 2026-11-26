"""Страница ученика /m/<код>: ожидание, вход, ответы, закрытие — без пароля и без лишних данных."""
import pytest

from backend.main import app
from backend.monitoring import logic, store
from conftest import TestClient, panel_client, TEACHER_LOGIN, TEACHER_PASSWORD


@pytest.fixture
def link(teacher):
    return teacher.get("/api/monitoring/link?school_id=1").json()["code"]


def start(teacher, **fields):
    body = {"school_id": 1, "set_key": "pulse", "class_ids": [1], "minutes": 5} | fields
    r = teacher.post("/api/monitoring/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()["run"]


def join(client, link_code, last="Иванов", first="Артём", class_id=1, **extra):
    return client.post(f"/api/m/{link_code}/join", json={"last_name": last, "first_name": first,
                                                          "class_id": class_id} | extra)


def test_waiting_then_run_appears(teacher, link):
    kid = TestClient(app)
    state = kid.get(f"/api/m/{link}/state").json()
    assert state["school"] == "Наша школа"
    assert state["classes"] == [] and state["me"] is None          # ждём учителя

    start(teacher, class_ids=[1, 2])
    state = kid.get(f"/api/m/{link}/state").json()
    assert [c["name"] for c in state["classes"]] == ["5-А", "5-Б"]
    assert not any(c["code"] for c in state["classes"])


def test_unknown_link_404(client):
    assert client.get("/api/m/zzzzz/state").status_code == 404


def test_known_student_linked_and_guest(teacher, link):
    start(teacher)
    kid = TestClient(app)
    r = join(kid, link, last="  ИВАНОВ ", first="артем")                 # регистр, пробелы, ё/е
    assert r.status_code == 200, r.text
    me = r.json()["me"]
    assert me["status"] == "active" and len(me["questions"]) == 3
    assert me["last_name"] == "Иванов"                                       # капс выровнен

    join(kid, link, last="Новенький", first="Гость")
    run_id = teacher.get("/api/monitoring/runs?school_id=1").json()["open"][0]["id"]
    people = {p["name"]: p for p in teacher.get(f"/api/monitoring/runs/{run_id}").json()["participants"]}
    assert people["Иванов Артем"]["guest"] is False
    assert people["Новенький Гость"]["guest"] is True


def test_namesakes_stay_guests():
    roster = [(1, "Иванов Артём"), (2, "Иванов Артем Сергеевич")]
    assert logic.match_student(roster, "Иванов", "Артём") is None
    assert logic.match_student([(1, "Иванов Артём")], "Артём", "Иванов") == 1      # перепутал поля
    assert logic.match_student([(1, "Иванов Артём")], "Иванов", "Пётр") is None


def test_duplicate_names_flagged_not_blocked(teacher, link):
    run = start(teacher)
    assert join(TestClient(app), link).status_code == 200
    assert join(TestClient(app), link).status_code == 200
    people = teacher.get(f"/api/monitoring/runs/{run['id']}").json()["participants"]
    assert len(people) == 2 and all(p["duplicate"] for p in people)


def test_answers_and_finish(teacher, link):
    run = start(teacher, topic="Закон Ома")
    kid = TestClient(app)
    me = join(kid, link).json()
    token, questions = me["token"], me["me"]["questions"]
    assert questions[1]["text"] == "Насколько понятна тема «Закон Ома»?"

    def answer(qid, value):
        return kid.post("/api/m/answer", json={"token": token, "question_id": qid, "value": value})

    assert answer("c2", {"v": 7}).status_code == 400                       # такого лица нет
    assert kid.post("/api/m/finish", json={"token": token}).status_code == 400    # не всё отвечено
    assert answer("c2", {"v": 4}).status_code == 200
    assert answer("c2", {"v": 5}).status_code == 200                       # передумал — перезапись
    assert answer("c5", {"v": "yellow"}).status_code == 200
    assert answer("c9", {"o": "b"}).status_code == 200
    r = kid.post("/api/m/finish", json={"token": token})
    assert r.status_code == 200 and r.json()["me"]["status"] == "done"
    assert answer("c2", {"v": 1}).status_code == 409                       # уже отправлено

    results = teacher.get(f"/api/monitoring/runs/{run['id']}/results").json()["results"]
    mood = results[0]
    assert mood["answered"] == 1 and mood["average"] == 5
    assert {o["label"]: o["count"] for o in mood["options"]}["Отлично"] == 1


def test_closed_window_rejects_answers(teacher, link, monkeypatch):
    began = store.now()
    monkeypatch.setattr(store, "now", lambda: began)                       # часы стоят: секунда на запуск не сдвинет итог
    start(teacher)
    kid = TestClient(app)
    token = join(kid, link).json()["token"]
    monkeypatch.setattr(store, "now", lambda: began + 6 * 60)              # окно в 5 минут прошло
    r = kid.post("/api/m/answer", json={"token": token, "question_id": "c2", "value": {"v": 4}})
    assert r.status_code == 409
    state = kid.get(f"/api/m/{link}/state?p={token}").json()
    assert state["me"]["status"] == "closed" and state["me"]["ended_ago"] == 60
    assert state["classes"] == []
    assert join(TestClient(app), link).status_code == 409                  # и войти нельзя


def test_lesson_code(teacher, link):
    run = start(teacher, use_code=True)
    assert run["code"] and len(run["code"]) == 4 and run["code"].isdigit()
    state = TestClient(app).get(f"/api/m/{link}/state").json()
    assert state["classes"][0]["code"] is True
    assert join(TestClient(app), link, code="0000" if run["code"] != "0000" else "1111").status_code == 400
    assert join(TestClient(app), link, code=run["code"]).status_code == 200


def test_code_guessing_is_slowed(teacher, link):
    run = start(teacher, use_code=True)
    wrong = "0000" if run["code"] != "0000" else "1111"
    kid = TestClient(app)
    codes = [join(kid, link, code=wrong).status_code for _ in range(45)]
    assert codes[0] == 400 and codes[-1] == 429


def test_public_api_leaks_nothing(teacher, link):
    start(teacher)
    kid = TestClient(app)
    join(TestClient(app), link, last="Петрова", first="София")
    text = kid.get(f"/api/m/{link}/state").text
    for secret in ("Петрова", "Иванов", "token", "student_id", "owner"):
        assert secret not in text, secret
    me = join(kid, link).json()
    assert "Петрова" not in str(me) and "correct" not in str(me["me"]["questions"])


def test_removed_participant_must_rejoin(teacher, link):
    run = start(teacher)
    kid = TestClient(app)
    token = join(kid, link).json()["token"]
    pid = teacher.get(f"/api/monitoring/runs/{run['id']}").json()["participants"][0]["id"]
    assert teacher.delete(f"/api/monitoring/runs/{run['id']}/participants/{pid}").status_code == 200
    r = kid.post("/api/m/answer", json={"token": token, "question_id": "c2", "value": {"v": 4}})
    assert r.status_code == 404
    assert kid.get(f"/api/m/{link}/state?p={token}").json()["me"] is None


def test_participant_limit(teacher, link, monkeypatch):
    monkeypatch.setattr(store, "MAX_PARTICIPANTS", 2)
    start(teacher)
    assert join(TestClient(app), link).status_code == 200
    assert join(TestClient(app), link, last="Петрова").status_code == 200
    assert join(TestClient(app), link, last="Сидоров").status_code == 400


def test_empty_name_rejected(teacher, link):
    start(teacher)
    assert join(TestClient(app), link, last="  ").status_code == 400
    assert join(TestClient(app), link, first="123").status_code == 400


def test_short_entry_redirects_when_school_is_single(client):
    r = client.get("/m", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("/m/")
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and 'data-theme="notebook"' in page.text


def test_short_entry_asks_for_link_when_many_schools(teacher, client):
    teacher.post("/api/schools", json={"name": "Вторая"})
    assert client.get("/m", follow_redirects=False).status_code == 200


def test_page_takes_school_theme(teacher, link, client):
    teacher.put("/api/schools/1/theme", json={"theme": "sky"})
    assert 'data-theme="sky"' in client.get(f"/m/{link}").text


def test_validate_answer_types():
    q = {"type": "multi", "required": True, "other": True,
         "options": [{"id": "a", "text": "шумно"}, {"id": "e", "text": "ничего", "exclusive": True}]}
    assert logic.validate_answer(q, {"o": ["a", "_other"], "t": " соседи "}) == {"o": ["a", "_other"], "t": "соседи"}
    for bad in ({"o": ["a", "e"]}, {"o": ["z"]}, {"o": ["a", "a"]}, {"o": ["_other"]}, {"o": []}):
        with pytest.raises(logic.AnswerError):
            logic.validate_answer(q, bad)
    scale = {"type": "scale", "scale": {"min": 1, "max": 5}}
    with pytest.raises(logic.AnswerError):
        logic.validate_answer(scale, {"v": True})
    assert logic.validate_answer({"type": "long", "required": False}, {"t": "  "}) is None


# ── Опрос из кабинета ученика и звёзды за опрос ──────────────────────────────

def _finish(kid, token):
    for qid, value in (("c2", {"v": 4}), ("c5", {"v": "yellow"}), ("c9", {"o": "b"})):
        assert kid.post("/api/m/answer", json={"token": token, "question_id": qid, "value": value}).status_code == 200
    r = kid.post("/api/m/finish", json={"token": token})
    assert r.status_code == 200, r.text
    return r.json()["me"]


def test_cabinet_sees_class_poll_and_gets_stars(teacher, link, student_headers):
    from conftest import student_record
    h = student_headers(101)                                  # Иванов Артём, 5-А
    kid = TestClient(app)
    assert kid.get("/api/me/poll", headers=h).json()["poll"] is None, "опроса нет — в кабинете пусто"

    start(teacher, stars=3)
    poll = kid.get("/api/me/poll", headers=h).json()["poll"]
    assert poll["title"] == "Пульс за 30 секунд" and poll["stars"] == 3 and not poll["done"] and not poll["started"]
    assert kid.get("/api/me/poll", headers=student_headers(103)).json()["poll"] is None, "у 5-Б опроса нет"

    r = kid.post("/api/me/poll/join", json={}, headers=h)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith(f"/m/{link}?p=")
    token = url.split("?p=")[1]
    me = kid.get(f"/api/m/{link}/state?p={token}").json()["me"]
    assert me["last_name"] == "Иванов" and me["first_name"] == "Артём" and me["verified"] and me["stars"] == 3
    again = kid.post("/api/me/poll/join", json={}, headers=h).json()["url"]
    assert again == url, "повторный вход — то же участие"

    before = student_record(101)["total_stars"]
    done = _finish(kid, token)
    assert done["stars_given"] == 3
    student = student_record(101)
    assert student["total_stars"] == before + 3
    assert student["history"][0]["reason"] == "Опрос «Пульс за 30 секунд»" and student["history"][0]["delta"] == 3
    poll = kid.get("/api/me/poll", headers=h).json()["poll"]
    assert poll["done"] and poll["stars_given"] == 3

    from backend.routers import m_public
    with store.read() as conn:
        participant_id = store.participant_by_token(conn, token)["id"]
    assert m_public.award_stars(participant_id) == 0, "второй раз звёзды не начисляются"
    assert student_record(101)["total_stars"] == before + 3

    run_id = teacher.get("/api/monitoring/runs?school_id=1").json()["open"][0]["id"]
    live = teacher.get(f"/api/monitoring/runs/{run_id}").json()
    assert live["stars"] == 3
    person = next(p for p in live["participants"] if p["name"] == "Иванов Артём")
    assert person["verified"] and person["stars"] == 3 and person["guest"] is False


def test_link_guest_gets_no_stars(teacher, link):
    from conftest import student_record
    start(teacher, stars=2)
    kid = TestClient(app)
    token = join(kid, link).json()["token"]                    # по ссылке, с именем — без проверки личности
    before = student_record(101)["total_stars"]
    done = _finish(kid, token)
    assert done["stars"] == 0 and done["stars_given"] is None
    assert student_record(101)["total_stars"] == before


def test_poll_without_stars(teacher, student_headers):
    from conftest import student_record
    start(teacher)
    kid = TestClient(app)
    h = student_headers(101)
    assert kid.get("/api/me/poll", headers=h).json()["poll"]["stars"] == 0
    token = kid.post("/api/me/poll/join", json={}, headers=h).json()["url"].split("?p=")[1]
    before = student_record(101)["total_stars"]
    assert _finish(kid, token)["stars_given"] is None
    assert student_record(101)["total_stars"] == before


def test_cabinet_join_needs_lesson_code(teacher, student_headers):
    start(teacher, use_code=True, stars=1)
    kid = TestClient(app)
    h = student_headers(101)
    assert kid.get("/api/me/poll", headers=h).json()["poll"]["code"] is True
    r = kid.post("/api/me/poll/join", json={}, headers=h)
    assert r.status_code == 400 and "код урока" in r.json()["detail"]
    assert kid.post("/api/me/poll/join", json={"code": "0000x"}, headers=h).status_code in (400, 422)
    run = teacher.get("/api/monitoring/runs?school_id=1").json()["open"][0]
    assert kid.post("/api/me/poll/join", json={"code": run["code"]}, headers=h).status_code == 200


def test_cabinet_join_when_no_poll(student_headers):
    kid = TestClient(app)
    r = kid.post("/api/me/poll/join", json={}, headers=student_headers(101))
    assert r.status_code == 409


def test_teacher_preview_cannot_take_poll(teacher):
    start(teacher)
    token = teacher.post("/api/auth/preview?student_id=101").json()["token"]
    r = TestClient(app).post("/api/me/poll/join", json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_old_monitoring_base_gets_new_columns():
    """База опросов версии 1 (до звёзд за опрос): столбцы дописываются, данные остаются."""
    import sqlite3
    path = store.db_file()
    store.forget()
    path.unlink(missing_ok=True)
    old = sqlite3.connect(str(path))
    old.executescript(store.SCHEMA.replace(
        "    score       REAL,\n    verified    INTEGER NOT NULL DEFAULT 0,   -- вошёл из своего кабинета: кто это — точно известно\n"
        "    stars_given INTEGER                       -- звёзды за опрос уже начислены (сколько)\n",
        "    score       REAL\n"))
    assert "verified" not in {r[1] for r in old.execute("PRAGMA table_info(participants)")}, "собрали старую схему"
    old.execute("INSERT INTO runs (school_id, school_name, owner_id, owner_name, title, questions, lesson_date, started_at) "
                "VALUES (1, 'Школа', 1, 'Учитель', 'Старый', '[]', '2026-11-20', 1)")
    old.execute("INSERT INTO participants (run_id, token, last_name, first_name, class_id, class_name, name_key, joined_at) "
                "VALUES (1, 'tok', 'Иванов', 'Артём', 1, '5-А', 'k', 1)")
    old.execute("PRAGMA user_version = 1")
    old.commit()
    old.close()
    with store.read() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(participants)")}
        assert {"verified", "stars_given"} <= cols
        row = store.participant_by_token(conn, "tok")
        assert row["verified"] == 0 and row["stars_given"] is None
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
