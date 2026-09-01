"""用户主动保存的旅行偏好接口。"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.security import get_current_user
from ...db.database import get_db
from ...db.models import User, UserTravelPreference
from ...models.schemas import TravelPreferenceRequest

router = APIRouter(prefix="/preferences", tags=["旅行偏好"])


def _serialize(item: UserTravelPreference | None) -> dict:
    if item is None:
        return {
            "saved": False,
            "preferences": [],
            "transportation": "公共交通",
            "accommodation": "经济型酒店",
        }
    return {
        "saved": True,
        "preferences": json.loads(item.preferences or "[]"),
        "transportation": item.transportation,
        "accommodation": item.accommodation,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/me", summary="读取我主动保存的旅行偏好")
def get_preferences(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = db.scalar(select(UserTravelPreference).where(UserTravelPreference.user_id == current_user.id))
    return {"success": True, "data": _serialize(item)}


@router.put("/me", summary="保存我的旅行偏好")
def save_preferences(
    body: TravelPreferenceRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = db.scalar(select(UserTravelPreference).where(UserTravelPreference.user_id == current_user.id))
    if item is None:
        item = UserTravelPreference(user_id=current_user.id)
        db.add(item)
    item.preferences = json.dumps(list(dict.fromkeys(tag.strip() for tag in body.preferences if tag.strip())), ensure_ascii=False)
    item.transportation = body.transportation.strip()
    item.accommodation = body.accommodation.strip()
    db.commit()
    db.refresh(item)
    return {"success": True, "message": "旅行偏好已保存", "data": _serialize(item)}


@router.delete("/me", summary="删除我保存的旅行偏好")
def delete_preferences(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = db.scalar(select(UserTravelPreference).where(UserTravelPreference.user_id == current_user.id))
    if item is not None:
        db.delete(item)
        db.commit()
    return {"success": True, "message": "已删除保存的旅行偏好"}
