"""Защита от перебора паролей."""
from backend import ratelimit
from conftest import ADMIN_LOGIN, ADMIN_PASSWORD, STUDENT_PASSWORD


def test_student_bruteforce_blocked(teacher, client):
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    codes = [client.post("/api/auth/student",
                         json={"login": "ivanov.artem", "password": f"podbor{i}"}).status_code
             for i in range(10)]
    limit = ratelimit.LIMITS["student-login"]
    assert codes[:limit] == [401] * limit
    assert codes[-1] == 429


def test_block_applies_to_correct_password_too(teacher, client):
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    for i in range(ratelimit.MAX_FAILURES):
        client.post("/api/auth/student", json={"login": "ivanov.artem", "password": f"x{i}"})
    r = client.post("/api/auth/student", json={"login": "ivanov.artem", "password": STUDENT_PASSWORD})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_teacher_bruteforce_blocked(client):
    for i in range(ratelimit.LIMITS["teacher-login"]):
        assert client.post("/api/auth/teacher",
                           json={"login": ADMIN_LOGIN, "password": f"x{i}"}).status_code == 401
    r = client.post("/api/auth/teacher", json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD})
    assert r.status_code == 429


def test_success_resets_login_counter(teacher, client):
    teacher.post("/api/students/101/password", json={"password": STUDENT_PASSWORD})
    for i in range(ratelimit.MAX_FAILURES - 1):
        client.post("/api/auth/student", json={"login": "ivanov.artem", "password": f"x{i}"})
    assert client.post("/api/auth/student",
                       json={"login": "ivanov.artem", "password": STUDENT_PASSWORD}).status_code == 200
    assert ratelimit.retry_after("student-login", "ivanov.artem") == 0
