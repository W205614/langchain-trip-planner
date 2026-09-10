"""RAG 知识库 API"""

import logging
import time
from prometheus_client import Histogram

from fastapi import APIRouter, Depends, Request, HTTPException
from threading import Lock
from datetime import datetime, timezone
from ...core.security import require_admin
from ...core.rate_limit import limiter
from ...db.database import get_db
from ...db.models import User, RagSyncJob, KnowledgeIngestJob, KnowledgeDocument
from sqlalchemy.orm import Session

from ...services.rag_service import get_rag_service
from ...services.coordination import knowledge_serialized

router = APIRouter(prefix="/rag", tags=["RAG知识库"])

logger = logging.getLogger(__name__)
_rebuild_lock = Lock()
_rebuild_seconds = Histogram("trip_index_rebuild_seconds", "Index rebuild duration", ["outcome"])


@router.get("/status", summary="RAG 知识库状态")
def rag_status():
    """查看 RAG 是否启用、知识索引情况"""
    rag = get_rag_service()
    return {
        "success": True,
        "enabled": rag.enabled,
        "embedding_model": rag._embedding.model if rag.enabled else None,
        "message": (
            "RAG 运行中"
            if rag.enabled
            else "RAG 未启用 (缺少嵌入配置, 不影响旅行规划主流程)"
        ),
    }


@router.post("/rebuild", summary="重建知识索引")
@limiter.limit("2/hour")
def rebuild_knowledge(request: Request, admin: User = Depends(require_admin)):
    """由主数据构建新集合，验证后切换，保留旧集合。"""
    if not _rebuild_lock.acquire(blocking=False):
        raise HTTPException(409, "已有重建任务运行中")
    started = time.monotonic()
    outcome = "failed"
    try:
        logger.info("Index rebuild requested actor_id=%s request_id=%s", admin.id, getattr(request.state, "request_id", ""))
        rag = get_rag_service()
        if rag._embedding is None:
            rag._init()
        result = rag.build_knowledge_index()
        outcome = "succeeded" if result["success"] else "failed"
        logger.info("Index rebuild completed actor_id=%s success=%s", admin.id, result["success"])
        return result
    finally:
        _rebuild_seconds.labels(outcome).observe(time.monotonic() - started)
        _rebuild_lock.release()


@router.post("/jobs/{kind}/{job_id}/replay")
@knowledge_serialized
def replay_job(kind: str, job_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    model = {"history": RagSyncJob, "knowledge": KnowledgeIngestJob}.get(kind)
    if model is None:
        raise HTTPException(404, "任务类型不存在")
    job = db.get(model, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status not in {"failed", "waiting", "retry"}:
        raise HTTPException(409, "只有失败或等待任务可以重放")
    if kind == "knowledge":
        document = db.get(KnowledgeDocument, job.document_id)
        if document is None or document.version != job.document_version or document.status in {"published", "rejected"}:
            raise HTTPException(409, "资料已失效或已发布")
        if document.status != "deleted":
            document.status = "queued"
    job.status, job.attempts, job.last_error = "pending", 0, ""
    job.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    logger.info("Job replay actor_id=%s kind=%s job_id=%s", admin.id, kind, job_id)
    return {"success": True}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return {kind: [{"id": job.id, "status": job.status, "attempts": job.attempts} for job in
                   db.query(model).filter(model.status.in_(("failed", "waiting", "retry"))).order_by(model.id).limit(100)]
            for kind, model in (("history", RagSyncJob), ("knowledge", KnowledgeIngestJob))}
