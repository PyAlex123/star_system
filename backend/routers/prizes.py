"""Витрина призов: редактирование и загрузка фотографий (только учитель)."""
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from .. import config, db, images, schemas
from ..security import require_teacher

router = APIRouter(prefix="/api/prizes", tags=["prizes"], dependencies=[Depends(require_teacher)])


def _find(database: Dict[str, Any], prize_id: int):
    prize = next((p for p in database["prizes"] if p["id"] == prize_id), None)
    if not prize:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Приз не найден")
    return prize


def _photo_path(prize_id: int):
    return config.PRIZES_UPLOAD_DIR / f"prize-{prize_id}.webp"


def _drop_photo_file(prize_id: int) -> None:
    """Удаляем загруженный файл, чтобы в папке не копились картинки удалённых призов."""
    _photo_path(prize_id).unlink(missing_ok=True)


@router.post("")
def create_prize(item: schemas.PrizeCreate):
    def _apply(database: Dict[str, Any]):
        new_id = max((p["id"] for p in database["prizes"]), default=0) + 1
        prize = {
            "id": new_id,
            "name": item.name.strip() or f"Приз №{new_id}",
            "cost": item.cost,
            "desc": item.desc.strip(),
            "category": item.category,
            "image": "",
            "active": item.active,
        }
        database["prizes"].append(prize)
        return prize

    return {"ok": True, "prize": db.transaction(_apply)}


@router.patch("/{prize_id}")
def update_prize(prize_id: int, item: schemas.PrizeUpdate):
    def _apply(database: Dict[str, Any]):
        prize = _find(database, prize_id)
        for field, value in item.model_dump(exclude_none=True).items():
            prize[field] = value.strip() if isinstance(value, str) else value
        return prize

    return {"ok": True, "prize": db.transaction(_apply)}


@router.delete("/{prize_id}")
def delete_prize(prize_id: int):
    def _apply(database: Dict[str, Any]):
        before = len(database["prizes"])
        database["prizes"] = [p for p in database["prizes"] if p["id"] != prize_id]
        return db.NO_SAVE if len(database["prizes"]) == before else None

    db.transaction(_apply)
    _drop_photo_file(prize_id)
    return {"ok": True}


@router.post("/{prize_id}/photo")
async def upload_photo(prize_id: int, file: UploadFile = File(...)):
    """Заменить картинку приза своим снимком — чтобы дети видели реальный приз."""
    _find(db.load_db(), prize_id)
    data = await images.read_upload(file)

    filename = f"prize-{prize_id}.webp"
    images.save_wide(data, config.PRIZES_UPLOAD_DIR / filename)
    url = f"/static/uploads/prizes/{filename}?v={int(time.time())}"

    def _apply(database: Dict[str, Any]):
        prize = _find(database, prize_id)
        prize["image"] = url
        return url

    db.transaction(_apply)
    return {"ok": True, "image": url}


@router.delete("/{prize_id}/photo")
def delete_photo(prize_id: int):
    """Убрать картинку приза — в витрине останется аккуратная заглушка."""
    def _apply(database: Dict[str, Any]):
        prize = _find(database, prize_id)
        if not prize.get("image"):
            return db.NO_SAVE
        prize["image"] = ""
        return None

    db.transaction(_apply)
    _drop_photo_file(prize_id)
    return {"ok": True}
