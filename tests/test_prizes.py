"""Витрина призов и фотографии."""
from backend import config
from conftest import jpeg_bytes, load_db


def create(teacher, **extra):
    payload = {"name": "Проверочный приз", "cost": 15, **extra}
    r = teacher.post("/api/prizes", json=payload)
    assert r.status_code == 200
    return r.json()["prize"]["id"]


def upload(teacher, prize_id, data=None, name="photo.jpg"):
    return teacher.post(f"/api/prizes/{prize_id}/photo",
                        files={"file": (name, data if data is not None else jpeg_bytes(), "image/jpeg")})


def test_default_prizes_have_photos(teacher):
    prizes = teacher.get("/api/state").json()["prizes"]
    assert len(prizes) == 8
    assert all(p["image"] for p in prizes)


def test_create_update_toggle(teacher):
    pid = create(teacher)
    assert teacher.patch(f"/api/prizes/{pid}", json={"cost": 25, "active": False}).status_code == 200
    prize = next(p for p in load_db()["prizes"] if p["id"] == pid)
    assert prize["cost"] == 25 and prize["active"] is False


def test_cost_must_be_positive(teacher):
    assert teacher.post("/api/prizes", json={"name": "X", "cost": 0}).status_code == 422


def test_photo_upload_served_and_resized(teacher):
    pid = create(teacher)
    r = upload(teacher, pid, jpeg_bytes((1600, 900)))
    assert r.status_code == 200
    url = r.json()["image"]
    served = teacher.get(url)
    assert served.status_code == 200
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(served.content)).size == (800, 600)


def test_photo_removed(teacher):
    pid = create(teacher)
    upload(teacher, pid)
    assert teacher.delete(f"/api/prizes/{pid}/photo").status_code == 200
    assert next(p for p in load_db()["prizes"] if p["id"] == pid)["image"] == ""
    assert not list(config.PRIZES_UPLOAD_DIR.iterdir())


def test_not_an_image_rejected(teacher):
    pid = create(teacher)
    assert upload(teacher, pid, b"MZ\x90\x00 not an image", "x.exe").status_code == 400


def test_too_large_rejected(teacher):
    pid = create(teacher)
    assert upload(teacher, pid, b"\xff" * (config.MAX_UPLOAD_BYTES + 1)).status_code == 413


def test_delete_prize_removes_file(teacher):
    pid = create(teacher)
    upload(teacher, pid)
    assert list(config.PRIZES_UPLOAD_DIR.iterdir())
    assert teacher.delete(f"/api/prizes/{pid}").status_code == 200
    assert not list(config.PRIZES_UPLOAD_DIR.iterdir())


def test_photo_for_missing_prize(teacher):
    assert upload(teacher, 999).status_code == 404
