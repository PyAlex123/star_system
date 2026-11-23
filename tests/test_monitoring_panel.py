"""«Мониторинг» в панели: наборы, запуск, живой экран, итоги, права."""
import pytest

from backend.main import app
from backend.monitoring import library, logic, store
from conftest import TestClient, TEACHER_LOGIN, TEACHER_PASSWORD, panel_client


def launch(client, **fields):
    return client.post("/api/monitoring/runs", json={"school_id": 1, "set_key": "pulse",
                                                     "class_ids": [1], "minutes": 5} | fields)


def test_library_has_15_questions_and_4_sets(teacher):
    data = teacher.get("/api/monitoring/library").json()
    assert [q["std"] for q in data["questions"]] == [f"С{i}" for i in range(1, 16)]
    sets = {s["key"]: s for s in data["sets"]}
    assert list(sets) == ["pulse", "start", "exit", "big"]
    assert sets["pulse"]["uses_topic"] and sets["exit"]["uses_goals"]


def test_launch_pulse_in_one_request(teacher):
    r = launch(teacher)
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["title"] == "Пульс за 30 секунд" and run["open"]
    assert [c["name"] for c in run["classes"]] == ["5-А"]
    assert 0 < run["seconds_left"] <= 300
    assert run["code"] is None


def test_topic_optional_goals_required():
    pulse = logic.prepare_questions(library.set_questions("pulse"), "", [])
    assert pulse[1]["text"] == "Насколько понятна тема урока?"
    with pytest.raises(ValueError):
        logic.prepare_questions(library.set_questions("exit"), "", [])
    exit_ticket = logic.prepare_questions(library.set_questions("exit"), "", ["считать ток", "читать схему"])
    assert [q["id"] for q in exit_ticket[:2]] == ["c15-1", "c15-2"]
    assert exit_ticket[1]["text"] == "Я могу: читать схему"


def test_exit_ticket_without_goals_is_400(teacher):
    r = launch(teacher, set_key="exit")
    assert r.status_code == 400 and "цель" in r.json()["detail"]


def test_class_cannot_be_in_two_open_runs(teacher):
    assert launch(teacher).status_code == 200
    r = launch(teacher, set_key="start", class_ids=[1, 2])
    assert r.status_code == 400 and "5-А" in r.json()["detail"]
    assert launch(teacher, set_key="start", class_ids=[2]).status_code == 200   # другой класс — можно


def test_all_classes_when_none_chosen(teacher):
    run = launch(teacher, class_ids=[]).json()["run"]
    assert run["all_classes"] and len(run["classes"]) == 3


def test_extend_and_close(teacher, monkeypatch):
    run = launch(teacher).json()["run"]
    r = teacher.post(f"/api/monitoring/runs/{run['id']}/extend", json={"minutes": 5})
    assert 540 < r.json()["seconds_left"] <= 600

    later = store.now() + 11 * 60                                          # окно истекло
    monkeypatch.setattr(store, "now", lambda: later)
    assert teacher.get(f"/api/monitoring/runs/{run['id']}").json()["open"] is False
    r = teacher.post(f"/api/monitoring/runs/{run['id']}/extend", json={"minutes": 5})
    assert r.json()["seconds_left"] == 300                                 # снова открыт на 5 минут

    teacher.post(f"/api/monitoring/runs/{run['id']}/close")
    listing = teacher.get("/api/monitoring/runs?school_id=1").json()
    assert listing["open"] == [] and listing["recent"][0]["id"] == run["id"]
    assert teacher.post(f"/api/monitoring/runs/{run['id']}/extend", json={}).status_code == 400


def test_until_manual_close(teacher, monkeypatch):
    run = launch(teacher, minutes=None).json()["run"]
    assert run["seconds_left"] is None
    later = store.now() + 3 * 3600
    monkeypatch.setattr(store, "now", lambda: later)
    assert teacher.get(f"/api/monitoring/runs/{run['id']}").json()["open"] is True


def test_other_teacher_sees_404_admin_sees_all(teacher):
    run = launch(teacher).json()["run"]
    colleague = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert colleague.get(f"/api/monitoring/runs/{run['id']}").status_code == 404
    assert colleague.post(f"/api/monitoring/runs/{run['id']}/close").status_code == 404
    assert colleague.get("/api/monitoring/runs?school_id=1").json() == {"open": [], "recent": []}

    own = launch(colleague, class_ids=[2]).json()["run"]
    assert teacher.get(f"/api/monitoring/runs/{own['id']}").status_code == 200     # администратор видит
    assert {r["id"] for r in teacher.get("/api/monitoring/runs?school_id=1").json()["open"]} == {run["id"], own["id"]}


def test_teacher_of_other_school_cannot_launch(teacher):
    other = teacher.post("/api/schools", json={"name": "Чужая"}).json()["school"]["id"]
    colleague = panel_client(TEACHER_LOGIN, TEACHER_PASSWORD)
    assert colleague.post("/api/monitoring/runs", json={"school_id": other, "set_key": "pulse"}).status_code == 404
    assert colleague.get(f"/api/monitoring/link?school_id={other}").status_code == 404
    assert launch(colleague, class_ids=[99]).status_code == 404


def test_school_admin_has_no_access(teacher):
    teacher.post("/api/users", json={"login": "direktor", "name": "Директор", "school_ids": [1],
                                     "password": "direktor-2026", "role": "school_admin"})
    sa = panel_client("direktor", "direktor-2026")
    run = launch(teacher).json()["run"]
    for path in ("/api/monitoring/library", "/api/monitoring/link?school_id=1",
                 "/api/monitoring/runs?school_id=1", f"/api/monitoring/runs/{run['id']}",
                 f"/api/monitoring/runs/{run['id']}/results"):
        assert sa.get(path).status_code == 403, path


def test_link_is_permanent_with_qr(teacher):
    first = teacher.get("/api/monitoring/link?school_id=1").json()
    again = teacher.get("/api/monitoring/link?school_id=1").json()
    assert first == again and first["path"] == f"/m/{first['code']}"
    assert first["url"].endswith(first["path"])
    qr = teacher.get("/api/monitoring/link/qr.png?school_id=1")
    assert qr.status_code == 200 and qr.content[:4] == b"\x89PNG"


def test_deleting_school_drops_its_runs(teacher):
    other = teacher.post("/api/schools", json={"name": "Вторая"}).json()["school"]["id"]
    cls = teacher.post("/api/classes", json={"school_id": other, "name": "7-В"}).json()["class"]["id"]
    run = teacher.post("/api/monitoring/runs", json={"school_id": other, "set_key": "start",
                                                     "class_ids": [cls]}).json()["run"]
    code = teacher.get(f"/api/monitoring/link?school_id={other}").json()["code"]
    assert teacher.delete(f"/api/schools/{other}").status_code == 200
    assert teacher.get(f"/api/monitoring/runs/{run['id']}").status_code == 404
    assert TestClient(app).get(f"/api/m/{code}/state").status_code == 404


def test_results_summary_counts(teacher):
    run = launch(teacher, class_ids=[1, 2]).json()["run"]
    code = teacher.get("/api/monitoring/link?school_id=1").json()["code"]
    for last, mood, light in (("Иванов", 5, "green"), ("Петрова", 3, "green"), ("Гость", 4, "red")):
        kid = TestClient(app)
        token = kid.post(f"/api/m/{code}/join", json={"last_name": last, "first_name": "Имя",
                                                      "class_id": 1}).json()["token"]
        kid.post("/api/m/answer", json={"token": token, "question_id": "c2", "value": {"v": mood}})
        kid.post("/api/m/answer", json={"token": token, "question_id": "c5", "value": {"v": light}})
    data = teacher.get(f"/api/monitoring/runs/{run['id']}/results").json()
    mood, light, pace = data["results"]
    assert mood["answered"] == 3 and mood["average"] == 4
    assert {o["key"]: o["count"] for o in light["options"]} == {"green": 2, "yellow": 0, "red": 1}
    assert pace["answered"] == 0
    assert data["joined"] == 3 and data["finished"] == 0


def test_backup_snapshot_is_readable(teacher, tmp_path):
    """deploy/backup.sh делает снимок SQLite изнутри контейнера — копия открывается и полна."""
    import sqlite3
    launch(teacher)
    target = tmp_path / "copy.sqlite3"
    store.backup_to(str(target))
    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    conn.close()
