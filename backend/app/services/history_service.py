"""历史行程记录服务: 保存/查询/删除用户的历史旅行计划"""

import json
import logging
from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..db.models import RagSyncJob, TripRecord
from ..models.schemas import TripPlan, TripRequest

logger = logging.getLogger(__name__)


def _enqueue_rag_sync(db: Session, record_id: int, user_id: int, operation: str) -> RagSyncJob:
    """在同一事务中写入派生向量同步任务。"""
    job = RagSyncJob(record_id=record_id, user_id=user_id, operation=operation)
    db.add(job)
    return job


def create_trip_record(
    db: Session, user_id: int, request: TripRequest, trip_plan: TripPlan
) -> TripRecord:
    """保存一条旅行计划历史记录 (归属指定用户)"""
    record = TripRecord(
        user_id=user_id,
        city=request.city,
        start_date=request.start_date,
        end_date=request.end_date,
        travel_days=request.travel_days,
        transportation=request.transportation,
        accommodation=request.accommodation,
        preferences=json.dumps(request.preferences, ensure_ascii=False),
        free_text_input=request.free_text_input or "",
        plan_json=trip_plan.model_dump_json(),
    )
    db.add(record)
    db.flush()  # 在 commit 前取得主记录 ID，使主表与 outbox 原子提交。
    _enqueue_rag_sync(db, record.id, user_id, "upsert")
    db.commit()
    db.refresh(record)
    logger.info(f"💾 历史记录已保存: id={record.id}, 用户={user_id}, 城市={record.city}")
    return record


def list_trip_records(
    db: Session,
    user_id: int,
    page: int = 1,
    page_size: int = 10,
    city: Optional[str] = None,
):
    """分页查询指定用户的历史记录 (按创建时间倒序, 只返回该用户的)"""
    query = select(TripRecord).where(TripRecord.user_id == user_id)
    if city:
        query = query.where(TripRecord.city.contains(city))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    records = db.scalars(
        query.order_by(desc(TripRecord.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(records), total


def get_trip_record(db: Session, user_id: int, record_id: int) -> Optional[TripRecord]:
    """按 id 查询历史记录 (仅限该用户的记录, 防止越权访问他人数据)"""
    return db.scalar(
        select(TripRecord).where(
            TripRecord.id == record_id,
            TripRecord.user_id == user_id,
        )
    )


def update_trip_record(
    db: Session, user_id: int, record_id: int, trip_plan: TripPlan
) -> Optional[TripRecord]:
    """更新历史记录的行程计划 (仅限该用户的记录)"""
    record = get_trip_record(db, user_id, record_id)
    if record is None:
        return None
    record.plan_json = trip_plan.model_dump_json()
    _enqueue_rag_sync(db, record.id, user_id, "upsert")
    db.commit()
    db.refresh(record)
    logger.info(f"✏️  历史记录已更新: id={record_id}")
    return record


def trip_record_to_request(record: TripRecord) -> TripRequest:
    """从持久化历史还原原始请求，供派生 RAG 向量重新索引。"""
    return TripRequest(
        city=record.city,
        start_date=record.start_date,
        end_date=record.end_date,
        travel_days=record.travel_days,
        transportation=record.transportation,
        accommodation=record.accommodation,
        preferences=json.loads(record.preferences or "[]"),
        free_text_input=record.free_text_input or "",
    )


def delete_trip_record(db: Session, user_id: int, record_id: int) -> bool:
    """删除历史记录 (仅限该用户的记录)"""
    record = get_trip_record(db, user_id, record_id)
    if record is None:
        return False
    _enqueue_rag_sync(db, record.id, user_id, "delete")
    db.delete(record)
    db.commit()
    logger.info(f"🗑️  历史记录已删除: id={record_id}")
    return True


def trip_record_to_summary(record: TripRecord) -> dict:
    """转列表摘要 (不含完整行程, 减少传输量)"""
    try:
        plan = json.loads(record.plan_json)
    except json.JSONDecodeError:
        plan = {}

    return {
        "id": record.id,
        "city": record.city,
        "start_date": record.start_date,
        "end_date": record.end_date,
        "travel_days": record.travel_days,
        "transportation": record.transportation,
        "accommodation": record.accommodation,
        "preferences": json.loads(record.preferences or "[]"),
        "created_at": record.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "attraction_count": sum(
            len(day.get("attractions", [])) for day in plan.get("days", [])
        ),
        "budget_total": (plan.get("budget") or {}).get("total", 0),
    }
