"""历史 RAG 派生索引的本地 outbox worker。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.database import SessionLocal
from ..db.models import RagSyncJob, TripRecord
from ..models.schemas import TripPlan
from .history_service import trip_record_to_request

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def _utcnow() -> datetime:
    """返回与现有无时区数据库列兼容的 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RagSyncWorker:
    """单进程串行消费 outbox；失败任务可在下一次启动时继续处理。"""

    def __init__(self, session_factory: Callable[[], Session] = SessionLocal, poll_seconds: float = 1.0):
        self._session_factory = session_factory
        self._poll_seconds = poll_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run, name="rag-sync-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            handled = self.run_once()
            if not handled:
                self._stop_event.wait(self._poll_seconds)

    def run_once(self) -> bool:
        """同步一个到期任务；供离线测试和启动恢复复用。"""
        db = self._session_factory()
        try:
            now = _utcnow()
            job = db.scalar(
                select(RagSyncJob)
                .where(
                    RagSyncJob.status.in_(("pending", "retry", "running")),
                    RagSyncJob.next_retry_at <= now,
                )
                .order_by(RagSyncJob.id)
                .limit(1)
            )
            if job is None:
                return False

            # 先持久化 running/attempts；进程中断后下一实例会重新捡起该任务。
            job.status = "running"
            job.attempts += 1
            db.commit()

            try:
                self._synchronize_job(db, job)
            except Exception as exc:
                self._schedule_retry(job, exc)
                db.commit()
            else:
                job.status = "succeeded"
                job.last_error = ""
                db.commit()
            return True
        except Exception:
            db.rollback()
            logger.exception("RAG outbox worker failed before a job could be synchronized")
            return False
        finally:
            db.close()

    @staticmethod
    def _synchronize_job(db: Session, job: RagSyncJob) -> None:
        from .rag_service import get_rag_service

        rag = get_rag_service()
        if job.operation == "delete":
            rag.delete_history_plan(job.record_id, job.user_id)
            return

        record = db.get(TripRecord, job.record_id)
        # 若 upsert 任务之后已删除主记录，删除旧向量即可，不能重新创建幻影记录。
        if record is None:
            rag.delete_history_plan(job.record_id, job.user_id)
            return
        plan = TripPlan.model_validate_json(record.plan_json)
        rag.delete_history_plan(record.id, record.user_id)
        rag.add_history_plan(record.id, record.user_id, trip_record_to_request(record), plan)

    @staticmethod
    def _schedule_retry(job: RagSyncJob, exc: Exception) -> None:
        summary = " ".join(str(exc).split())
        summary = re.sub(r"(?i)(api[_-]?key|token|password)=\S+", r"\1=[redacted]", summary)
        summary = re.sub(r"://([^:/]+):[^@]+@", r"://\1:[redacted]@", summary)
        summary = summary[:480]
        job.last_error = summary or exc.__class__.__name__
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            logger.warning("RAG sync permanently failed: job=%s attempts=%s", job.id, job.attempts)
        else:
            delay_seconds = min(300, 2 ** max(0, job.attempts - 1))
            job.status = "retry"
            job.next_retry_at = _utcnow() + timedelta(seconds=delay_seconds)
            logger.warning("RAG sync retry scheduled: job=%s delay=%ss", job.id, delay_seconds)
