"""Витрина призов: редактирование и загрузка фотографий.

Общие призы (school_id нет) видят все школы — их меняет только администратор системы.
Администрация школы добавляет свои призы: school_id — её школа, такой приз видят
только дети этой школы, с пометкой «от администрации школы». Чужие и общие призы
администрация школы не меняет. Учитель видит призы в /api/state и выдаёт их
через списание звёзд."""
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from .. import config, db, images, schemas
from ..security import require_prize_editor

router = APIRouter(prefix="/api/prizes", tags=["prizes"], dependencies=[Depends(require_prize_editor)])


def _find(database: Dict[str, Any], prize_id: int, user: Dict[str, Any] = None):
    """Приз, который этот пользователь может менять. Приз чужой школы — как несуществующий."""
    prize = next((p for p in database["prizes"] if p["id"] == prize_id), None)
    if user is not None and prize is not None and not db.is_superadmin(user):
        mine = db.manage_school_ids(user) & db.allowed_school_ids(database, user)
        if not db.prize_for_school(prize, mine):
            prize = None
        elif prize.get("school_id") is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Этот приз добавил администратор системы — изменить его нельзя")
    if not prize:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Приз не найден")
    return prize


def _owner_school(database: Dict[str, Any], item: schemas.PrizeCreate, user: Dict[str, Any]):
    """Чей приз: администрация школы — только своей школы; администратор системы —
    общий или, если указал школу, этой школы."""
    if item.school_id is not None and item.school_id not in {s["id"] for s in database["schools"]}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
    if db.is_superadmin(user):
        return item.school_id
    mine = db.manage_school_ids(user) & db.allowed_school_ids(database, user)
    if item.school_id is None:
        if len(mine) != 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Выберите школу, для которой этот приз")
        return next(iter(mine))
    if item.school_id not in mine:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
    return item.school_id


def _photo_path(prize_id: int):
    return config.PRIZES_UPLOAD_DIR / f"prize-{prize_id}.webp"


def _drop_photo_file(prize_id: int) -> None:
    """Удаляем загруженный файл, чтобы в папке не копились картинки удалённых призов."""
    _photo_path(prize_id).unlink(missing_ok=True)


@router.post("")
def create_prize(item: schemas.PrizeCreate, user: Dict[str, Any] = Depends(require_prize_editor)):
    def _apply(database: Dict[str, Any]):
        school_id = _owner_school(database, item, user)
        new_id = max((p["id"] for p in database["prizes"]), default=0) + 1
        prize = {
            "id": new_id,
            "name": item.name.strip() or f"Приз №{new_id}",
            "cost": item.cost,
            "desc": item.desc.strip(),
            "category": item.category,
            "image": "",
            "active": item.active,
            "school_id": school_id,          # None — общий приз
            "created_by": user["id"],
        }
        database["prizes"].append(prize)
        return prize

    return {"ok": True, "prize": db.transaction(_apply)}


@router.patch("/{prize_id}")
def update_prize(prize_id: int, item: schemas.PrizeUpdate, user: Dict[str, Any] = Depends(require_prize_editor)):
    def _apply(database: Dict[str, Any]):
        prize = _find(database, prize_id, user)
        for field, value in item.model_dump(exclude_none=True).items():
            prize[field] = value.strip() if isinstance(value, str) else value
        return prize

    return {"ok": True, "prize": db.transaction(_apply)}


@router.delete("/{prize_id}")
def delete_prize(prize_id: int, user: Dict[str, Any] = Depends(require_prize_editor)):
    def _apply(database: Dict[str, Any]):
        if not db.is_superadmin(user):
            _find(database, prize_id, user)
        before = len(database["prizes"])
        database["prizes"] = [p for p in database["prizes"] if p["id"] != prize_id]
        return db.NO_SAVE if len(database["prizes"]) == before else None

    db.transaction(_apply)
    _drop_photo_file(prize_id)
    return {"ok": True}


@router.post("/{prize_id}/photo")
async def upload_photo(prize_id: int, file: UploadFile = File(...),
                       user: Dict[str, Any] = Depends(require_prize_editor)):
    """Заменить картинку приза своим снимком — чтобы дети видели реальный приз."""
    _find(db.load_db(), prize_id, user)
    data = await images.read_upload(file)

    filename = f"prize-{prize_id}.webp"
    images.save_wide(data, config.PRIZES_UPLOAD_DIR / filename)
    url = f"/static/uploads/prizes/{filename}?v={int(time.time())}"

    def _apply(database: Dict[str, Any]):
        prize = _find(database, prize_id, user)
        prize["image"] = url
        return url

    db.transaction(_apply)
    return {"ok": True, "image": url}


@router.delete("/{prize_id}/photo")
def delete_photo(prize_id: int, user: Dict[str, Any] = Depends(require_prize_editor)):
    """Убрать картинку приза — в витрине останется аккуратная заглушка."""
    def _apply(database: Dict[str, Any]):
        prize = _find(database, prize_id, user)
        if not prize.get("image"):
            return db.NO_SAVE
        prize["image"] = ""
        return None

    db.transaction(_apply)
    _drop_photo_file(prize_id)
    return {"ok": True}
