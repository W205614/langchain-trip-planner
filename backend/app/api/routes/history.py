"""历史行程记录 API

历史记录为登录用户的私有数据, 所有接口需携带 Bearer token (JWT)。
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ...core.exceptions import BizException
from ...core.rate_limit import limiter, llm_request_gate
from ...core.security import get_current_user
from ...db.database import get_db
from ...db.models import User
from ...models.schemas import TripPlan, TripRevisionRequest
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...services.amap_service import get_amap_service
from ...services.plan_quality import evaluate_plan, repair_plan_routes
from ...services import history_service

router = APIRouter(prefix="/history", tags=["历史记录"])

logger = logging.getLogger(__name__)


@router.get("", summary="历史记录列表")
def list_history(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=50, description="每页条数"),
    city: Optional[str] = Query(None, description="按城市筛选"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # 需登录
):
    """分页查询当前登录用户的历史行程 (按创建时间倒序)"""
    records, total = history_service.list_trip_records(db, current_user.id, page, page_size, city)
    return {
        "success": True,
        "data": [history_service.trip_record_to_summary(r) for r in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{record_id}", summary="历史记录详情")
def get_history(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # 需登录
):
    """查询当前用户单条历史记录 (含完整行程计划)"""
    record = history_service.get_trip_record(db, current_user.id, record_id)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)

    return {
        "success": True,
        "data": {
            "id": record.id,
            "city": record.city,
            "start_date": record.start_date,
            "end_date": record.end_date,
            "travel_days": record.travel_days,
            "transportation": record.transportation,
            "accommodation": record.accommodation,
            "preferences": json.loads(record.preferences or "[]"),
            "free_text_input": record.free_text_input,
            "plan": json.loads(record.plan_json),
            "created_at": record.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


@router.put("/{record_id}", summary="更新历史记录行程")
def update_history(
    record_id: int,
    plan: TripPlan,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # 需登录
):
    """编辑保存: 用前端编辑后的完整行程计划覆盖历史记录 (仅限本人记录)"""
    record = history_service.update_trip_record(db, current_user.id, record_id, plan)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)
    return {"success": True, "message": "更新成功", "id": record.id, "rag_sync_pending": True}


@router.post("/{record_id}/revise-day", summary="增量改排行程中的一天")
@limiter.limit("5/minute")
def revise_history_day(
    request: Request,
    record_id: int,
    body: TripRevisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对指定历史行程只重排一天，保留其余日期并重新进行确定性路线校验。"""
    record = history_service.get_trip_record(db, current_user.id, record_id)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)
    try:
        trip_plan = TripPlan.model_validate_json(record.plan_json)
    except ValueError as exc:
        logger.warning("历史行程 JSON 无法解析: id=%s", record_id)
        raise BizException("历史行程数据损坏，无法改排", status_code=409) from exc

    trip_request = history_service.trip_record_to_request(record)
    try:
        with llm_request_gate.slot():
            trip_plan = get_trip_planner_agent().revise_trip_day(
                trip_request, trip_plan, body.day_index, body.instruction, user_id=current_user.id
            )
    except ValueError as exc:
        raise BizException(str(exc), status_code=422) from exc

    route_quality = repair_plan_routes(trip_plan, get_amap_service(), trip_request.transportation)
    quality = evaluate_plan(trip_plan, trip_request.travel_days).to_dict() | route_quality
    updated = history_service.update_trip_record(db, current_user.id, record_id, trip_plan)
    if updated is None:  # 防御并发删除；不覆盖其它用户记录。
        raise BizException("历史记录不存在", status_code=404)
    return {
        "success": True,
        "message": f"第{body.day_index + 1}天已重新安排",
        "data": trip_plan,
        "quality": quality,
        "id": updated.id,
        "rag_sync_pending": True,
    }


@router.delete("/{record_id}", summary="删除历史记录")
def delete_history(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # 需登录
):
    """删除一条历史记录 (仅限本人记录)"""
    # 向量库仅是派生数据：删除与 outbox 入队在同一数据库事务，随后异步清除。
    ok = history_service.delete_trip_record(db, current_user.id, record_id)
    if not ok:
        raise BizException("历史记录不存在", status_code=404)
    return {"success": True, "message": "删除成功", "rag_sync_pending": True}
