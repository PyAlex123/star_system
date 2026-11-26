"""«Мониторинг», выпуск 3: свои формы, конструктор, тесты с баллами."""
import io

import pytest
from openpyxl import load_workbook

from backend.main import app
from backend.monitoring import logic
from conftest import TestClient, TEACHER_LOGIN, TEACHER_PASSWORD, panel_client

QUIZ = {
    "title": "Закон Ома — проверка",
    "kind": "test",
    "settings": {"after": "full"},
    "questions": [
        {"type": "single", "text": "Единица силы тока?", "options": [{"text": "вольт"}, {"text": "ампер"}, {"text": "ом"}],
         "correct": ["b"], "points": 2},
        {"type": "multi", "text": "Что измеряют в омах?", "options": ["сопротивление", "напряжение", "реостат"],
         "correct": ["a", "c"]},
        {"type": "short", "text": "Формула закона Ома", "correct": ["I = U / R", "I=U/R"], "points": 3},
        {"type": "mood", "text": "Как тебе тест?", "required": False},
    ],
}


def new_form(client, **fields):
    r = client.post("/api/monitoring/forms", json={"school_id": 1} | fields)
    assert r.status_code == 200, r.text
    return r.json()["form"]


def save(client, form_id, body):
    return client.put(f"/api/monitoring/forms/{form_id}", json=body)


def test_create_edit_copy_delete(teacher):
    form = new_form(teacher)
    assert form["title"] == "Новая форма" and form["questions"] == []
    r = save(teacher, form["id"], QUIZ)
    assert r.status_code == 200, r.text
    saved = r.json()["form"]
    assert [q["id"] for q in saved["questions"]] == ["q1", "q2", "q3", "q4"]
    assert saved["questions"][0]["correct"] == ["b"] and saved["questions"][0]["points"] == 2
    assert saved["settings"] == {"after": "full", "show_names": False, "shuffle": False}

    copy = new_form(teacher, from_form=form["id"])
    assert copy["title"] == "Закон Ома — проверка (копия)" and len(copy["questions"]) == 4
    listing = teacher.get("/api/monitoring/forms?school_id=1").json()["forms"]
    assert {f["id"] for f in listing} == {form["id"], copy["id"]}

    assert teacher.delete(f"/api/monitoring/forms/{copy['id']}").status_code == 200
    assert teacher.get(f"/api/monitoring/forms/{copy['id']}").status_code == 404


def test_copy_of_set_keeps_standard_questions(teacher):
    form = new_form(teacher, from_set="exit")
    assert [q["std"] for q in form["questions"]] == ["С15", "С6", "С7", "С8"]
    tampered = form["questions"]
    tampered[1] = dict(tampered[1], text="Изменённый текст")                    # стандартный текст не меняется
    saved = save(teacher, form["id"], {"title": form["title"], "questions": tampered}).json()["form"]
    assert saved["questions"][1]["text"] == "Смог бы ты решить такое задание сам?"
    dup = save(teacher, form["id"], {"questions": [tampered[1], tampered[1]]})
    assert dup.status_code == 400 and "уже есть" in dup.json()["detail"]


@pytest.mark.parametrize("question, message", [
    ({"type": "single", "text": "?", "options": ["одно"]}, "два варианта"),
    ({"type": "single", "text": "", "options": ["а", "б"]}, "текст"),
    ({"type": "nope", "text": "?"}, "тип"),
    ({"type": "single", "text": "?", "options": ["а", ""]}, "нет текста"),
])
def test_validation_messages(teacher, question, message):
    form = new_form(teacher)
    r = save(teacher, form["id"], {"questions": [question]})
    assert r.status_code == 400 and message in r.json()["detail"]


def test_foreign_forms_are_hidden(teacher):
    form = new_form(teacher)
    colleague = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert colleague.get(f"/api/monitoring/forms/{form['id']}").status_code == 404
    assert save(colleague, form["id"], QUIZ).status_code == 404
    assert colleague.post("/api/monitoring/runs", json={"school_id": 1, "form_id": form["id"]}).status_code == 404
    assert colleague.get("/api/monitoring/forms?school_id=1").json()["forms"] == []


def test_quiz_scores_and_result_for_student(teacher):
    form = new_form(teacher)
    save(teacher, form["id"], QUIZ)
    run = teacher.post("/api/monitoring/runs", json={"school_id": 1, "form_id": form["id"], "class_ids": [1],
                                                     "minutes": 10}).json()["run"]
    assert run["title"] == "Закон Ома — проверка"
    code = teacher.get("/api/monitoring/link?school_id=1").json()["code"]
    kid = TestClient(app)
    me = kid.post(f"/api/m/{code}/join", json={"last_name": "Иванов", "first_name": "Артём", "class_id": 1}).json()
    assert all("correct" not in q for q in me["me"]["questions"])          # ответы теста не уходят заранее
    token = me["token"]
    for qid, value in (("q1", {"o": "b"}), ("q2", {"o": ["a"]}), ("q3", {"t": " i=u/r "})):
        assert kid.post("/api/m/answer", json={"token": token, "question_id": qid, "value": value}).status_code == 200
    done = kid.post("/api/m/finish", json={"token": token}).json()["me"]
    assert done["result"]["score"] == 5 and done["result"]["max"] == 6      # 2 + 0 (неполный) + 3
    assert done["result"]["correct"]["q2"] == ["a", "c"]

    data = teacher.get(f"/api/monitoring/runs/{run['id']}/results").json()
    assert data["is_test"] and data["max_score"] == 6 and data["rows"][0]["score"] == 5
    cells = data["rows"][0]["answers"]
    assert cells["q1"]["correct"] is True and cells["q2"]["correct"] is False
    assert {r["id"]: r.get("correct_count") for r in data["results"]}["q3"] == 1

    book = load_workbook(io.BytesIO(teacher.get(f"/api/monitoring/runs/{run['id']}/export.xlsx").content))
    head = next(book["Ответы"].iter_rows(values_only=True))
    assert "Баллы" in head
    row = next(book["Ответы"].iter_rows(min_row=2, values_only=True))
    assert row[head.index("Баллы")] == 5


def test_score_hidden_when_after_none(teacher):
    form = new_form(teacher)
    save(teacher, form["id"], QUIZ | {"settings": {"after": "none"}})
    teacher.post("/api/monitoring/runs", json={"school_id": 1, "form_id": form["id"], "class_ids": [1]})
    code = teacher.get("/api/monitoring/link?school_id=1").json()["code"]
    kid = TestClient(app)
    token = kid.post(f"/api/m/{code}/join", json={"last_name": "Петрова", "first_name": "София",
                                                  "class_id": 1}).json()["token"]
    for qid, value in (("q1", {"o": "a"}), ("q2", {"o": ["b"]}), ("q3", {"t": "не знаю"})):
        kid.post("/api/m/answer", json={"token": token, "question_id": qid, "value": value})
    assert "result" not in kid.post("/api/m/finish", json={"token": token}).json()["me"]


def test_shuffle_is_stable_per_participant(teacher):
    questions = [{"type": "single", "text": f"Вопрос {i}", "options": ["а", "б", "в", "г"]} for i in range(6)]
    form = new_form(teacher)
    save(teacher, form["id"], {"questions": questions, "settings": {"shuffle": True}})
    teacher.post("/api/monitoring/runs", json={"school_id": 1, "form_id": form["id"], "class_ids": [1]})
    code = teacher.get("/api/monitoring/link?school_id=1").json()["code"]
    kid = TestClient(app)
    token = kid.post(f"/api/m/{code}/join", json={"last_name": "Иванов", "first_name": "Артём",
                                                  "class_id": 1}).json()["token"]
    first = kid.get(f"/api/m/{code}/state?p={token}").json()["me"]["questions"]
    again = kid.get(f"/api/m/{code}/state?p={token}").json()["me"]["questions"]
    assert [q["id"] for q in first] == [q["id"] for q in again]              # порядок у ученика не прыгает
    assert sorted(q["id"] for q in first) == [f"q{i}" for i in range(1, 7)]


def test_shuffle_keeps_exclusive_last():
    q = {"type": "multi", "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"},
                                      {"id": "c", "text": "ничего", "exclusive": True}]}
    for seed in ("x", "y", "z"):
        assert logic.shuffled([q], seed)[0]["options"][-1]["id"] == "c"


def test_empty_form_cannot_launch(teacher):
    form = new_form(teacher)
    r = teacher.post("/api/monitoring/runs", json={"school_id": 1, "form_id": form["id"]})
    assert r.status_code == 400 and "нет вопросов" in r.json()["detail"]


def test_editing_form_does_not_change_past_runs(teacher):
    form = new_form(teacher)
    save(teacher, form["id"], QUIZ)
    run = teacher.post("/api/monitoring/runs", json={"school_id": 1, "form_id": form["id"], "class_ids": [1]}).json()["run"]
    save(teacher, form["id"], QUIZ | {"questions": QUIZ["questions"][:1]})
    assert len(teacher.get(f"/api/monitoring/runs/{run['id']}/results").json()["results"]) == 4
