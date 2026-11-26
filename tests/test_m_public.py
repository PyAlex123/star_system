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
