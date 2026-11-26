"""«Мониторинг», выпуск 2: таблица ответов, Excel, удаление запуска, история по классу, лист с QR."""
import io

from openpyxl import load_workbook

from backend.main import app
from backend.monitoring import logic, store
from conftest import TestClient, TEACHER_LOGIN, TEACHER_PASSWORD, panel_client


def launch(client, **fields):
    r = client.post("/api/monitoring/runs", json={"school_id": 1, "set_key": "pulse",
                                                  "class_ids": [1], "minutes": 5} | fields)
    assert r.status_code == 200, r.text
    return r.json()["run"]


def answer_as(client_code, last, answers, class_id=1, finish=True, first="Имя"):
    kid = TestClient(app)
    token = kid.post(f"/api/m/{client_code}/join", json={"last_name": last, "first_name": first,
                                                        "class_id": class_id}).json()["token"]
    for qid, value in answers:
        assert kid.post("/api/m/answer", json={"token": token, "question_id": qid, "value": value}).status_code == 200
    if finish:
        assert kid.post("/api/m/finish", json={"token": token}).status_code == 200


def code(teacher):
    return teacher.get("/api/monitoring/link?school_id=1").json()["code"]


def test_table_hides_mood_but_shows_other_answers(teacher):
    run = launch(teacher, topic="Закон Ома")
    answer_as(code(teacher), "Иванов", [("c2", {"v": 5}), ("c5", {"v": "yellow"}), ("c9", {"o": "c"})], first="Артём")
    data = teacher.get(f"/api/monitoring/runs/{run['id']}/results").json()
    assert [c["id"] for c in data["columns"]] == ["c2", "c5", "c9"]
    row = data["rows"][0]
    assert row["name"] == "Иванов Артём" and row["finished"] and not row["guest"]
    assert row["answers"]["c2"]["text"] == logic.HIDDEN             # настроение без имён
    assert row["answers"]["c5"]["text"] == "понял, но есть вопросы"
    assert row["answers"]["c9"]["text"] == "Быстро"
    assert data["results"][0]["average"] == 5                       # в сводке настроение видно


def test_answer_labels():
    multi = {"type": "multi", "other": True, "options": [{"id": "a", "text": "шумно"}, {"id": "b", "text": "скучно"}]}
    assert logic.answer_label(multi, {"o": ["a", "_other"], "t": "сосед"}) == "шумно, Другое: сосед"
    assert logic.answer_label({"type": "scale", "scale": {"min": 1, "max": 5}}, {"v": 4}) == "4"
    assert logic.answer_label({"type": "mood"}, {"v": 2}) == "Не очень"
    assert logic.answer_label({"type": "long"}, None) == ""


def test_excel_has_summary_and_answers(teacher):
    run = launch(teacher, class_ids=[1, 2])
    answer_as(code(teacher), "Иванов", [("c2", {"v": 4}), ("c5", {"v": "green"}), ("c9", {"o": "b"})])
    answer_as(code(teacher), "=HYPERLINK(1)", [("c2", {"v": 2})], class_id=2, finish=False)
    r = teacher.get(f"/api/monitoring/runs/{run['id']}/export.xlsx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    book = load_workbook(io.BytesIO(r.content))
    assert book.sheetnames == ["Сводка", "Ответы"]
    summary = [row for row in book["Сводка"].iter_rows(values_only=True)]
    assert summary[0] == ("Вопрос", "Ответ", "Сколько", "Доля", "Среднее")
    assert any(row[1] == "могу объяснить другу" and row[2] == 1 for row in summary)
    answers = list(book["Ответы"].iter_rows(values_only=True))
    assert answers[0][:4] == ("Фамилия и имя", "Класс", "Гость", "Закончил")
    names = {row[0]: row for row in answers[1:]}
    assert names["Иванов Имя"][3] == "да" and names["Иванов Имя"][-1] == "В самый раз"
    assert book["Ответы"]["A3"].data_type == "s"                    # «=» — текст, не формула


def test_delete_run_with_answers(teacher):
    run = launch(teacher)
    answer_as(code(teacher), "Иванов", [("c2", {"v": 4})], finish=False)
    colleague = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert colleague.delete(f"/api/monitoring/runs/{run['id']}").status_code == 404
    assert teacher.delete(f"/api/monitoring/runs/{run['id']}").status_code == 200
    assert teacher.get(f"/api/monitoring/runs/{run['id']}").status_code == 404
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 0


def test_history_by_class_and_limit(teacher):
    first = launch(teacher)
    teacher.post(f"/api/monitoring/runs/{first['id']}/close")
    second = launch(teacher, class_ids=[2], set_key="start")
    teacher.post(f"/api/monitoring/runs/{second['id']}/close")
    only_b = teacher.get("/api/monitoring/runs?school_id=1&class_id=2").json()["recent"]
    assert [r["id"] for r in only_b] == [second["id"]]
    assert len(teacher.get("/api/monitoring/runs?school_id=1&limit=1").json()["recent"]) == 1


def test_poster_page(teacher, client):
    page = teacher.get("/monitoring/poster/1")
    link = teacher.get("/api/monitoring/link?school_id=1").json()["path"]
    assert page.status_code == 200 and "data:image/png;base64," in page.text and link in page.text
    assert client.get("/monitoring/poster/1", follow_redirects=False).status_code == 307   # без входа — на вход
