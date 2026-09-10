"""历史行程记录 API

历史记录为登录用户的私有数据, 所有接口需携带 Bearer token (JWT)。
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Header
from sqlalchemy.orm import Session

from ...core.exceptions import BizException
from ...core.rate_limit import limiter, llm_request_gate
from ...core.security import get_current_user
from ...db.database import get_db
from ...db.models import User
from ...models.schemas import TripPlan, TripRevisionRequest
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...services.amap_service import get_amap_service
from ...services.plan_quality import evaluate_plan, repair_plan_routes, recalculate_budget
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
            "version": record.version,
            "quality": json.loads(record.quality_json),
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
    expected_version: int = Header(..., alias="If-Match", ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # 需登录
):
    """编辑保存: 用前端编辑后的完整行程计划覆盖历史记录 (仅限本人记录)"""
    record = history_service.get_trip_record(db, current_user.id, record_id)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)
    from ...services.planning_constraints import finalize_plan
    quality = finalize_plan(plan, history_service.trip_record_to_request(record), repair=False)
    quality["data_gaps"].append("user_edited_plan_not_externally_verified")
    record = history_service.update_trip_record(db, current_user.id, record_id, plan, expected_version, quality)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)
    return {"success": True, "message": "更新成功", "id": record.id, "version": record.version,
            "data": plan, "quality": quality, "saved": True, "rag_sync_pending": True}


@router.post("/{record_id}/revise-day", summary="增量改排行程中的一天")
@limiter.limit("5/minute")
def revise_history_day(
    request: Request,
    record_id: int,
    body: TripRevisionRequest,
    expected_version: int = Header(..., alias="If-Match", ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compatibility adapter; shares quota, persistence and cancellation with task API."""
    from ...services import trip_tasks
    import time
    from uuid import uuid4
    record = history_service.get_trip_record(db, current_user.id, record_id)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)
    if record.version != expected_version:
        raise BizException("行程版本冲突，请重新加载", status_code=409, code="VERSION_CONFLICT")
    # Preserve the old endpoint's immediate busy response.
    with llm_request_gate.slot():
        pass
    result = submit_revision_task(request, record_id, body, expected_version,
        request.headers.get("Idempotency-Key") or str(uuid4()), db, current_user)
    task_id = result["data"]["id"]
    while True:
        state = trip_tasks.snapshot(current_user.id, task_id)
        if state["status"] == "succeeded":
            return state["result"] | {"rag_sync_pending": True}
        if state["status"] in {"failed", "cancelled"}:
            raise BizException(state["message"], status_code=409 if state["error_code"] == "VERSION_CONFLICT" else 500,
                code=state["error_code"])
        time.sleep(0.1)


@router.post("/{record_id}/revise-task", status_code=202)
@limiter.limit("5/minute")
def submit_revision_task(request: Request, record_id: int, body: TripRevisionRequest,
    expected_version: int = Header(..., alias="If-Match", ge=1),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ...services import trip_tasks
    record = history_service.get_trip_record(db, current_user.id, record_id)
    if record is None:
        raise BizException("历史记录不存在", status_code=404)
    original_request = history_service.trip_record_to_request(record)
    if body.day_index >= original_request.travel_days:
        raise BizException("改排天数超出原行程", status_code=422)
    # Stale versions become durable failures in the worker; retries keep stable idempotency.
    revision = {"record_id": record_id, "version": expected_version, **body.model_dump()}
    db.close()
    task, cached = trip_tasks.submit(current_user.id, original_request, idempotency_key,
        request.state.request_id, revision=revision)
    trip_tasks.runner.start()
    return {"success": True, "cached": cached, "data": trip_tasks.snapshot(current_user.id, task)}


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
