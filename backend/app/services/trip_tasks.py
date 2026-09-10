"""Durable queue for one API process. Network work never owns a request Session."""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from ..config import get_settings
from ..core.exceptions import BizException
from ..core.rate_limit import llm_request_gate
from ..db.database import SessionLocal, ensure_tables
from ..db.models import TripTask, TripRecord
from ..models.schemas import TripRequest
from . import history_service
from .plan_quality import evaluate_plan, repair_plan_routes, recalculate_budget

logger = logging.getLogger(__name__)
TERMINAL = {"succeeded", "failed"}
submission_lock = Lock()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def submit(user_id, body, key=None, request_id=""):
    ensure_tables()
    key = key or str(uuid4())
    if not 1 <= len(key) <= 128:
        raise BizException("Idempotency-Key 长度必须为 1–128", status_code=422)
    payload = json.dumps(body.model_dump(), sort_keys=True, ensure_ascii=False)
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    with submission_lock, SessionLocal() as db:
        existing = db.scalar(select(TripTask).where(TripTask.user_id == user_id, TripTask.idempotency_key == key))
        if existing:
            if existing.fingerprint != fingerprint:
                raise BizException("同一幂等键不能用于不同请求", status_code=409)
            return existing.id, True
        count = db.scalar(select(func.count()).select_from(TripTask).where(TripTask.status.in_(("queued", "running"))))
        if count >= get_settings().trip_task_queue_limit:
            raise BizException("规划队列已满，请稍后重试", status_code=429, code="TASK_QUEUE_FULL")
        task = TripTask(id=str(uuid4()), user_id=user_id, idempotency_key=key, fingerprint=fingerprint,
                        request_json=payload, request_id=request_id[:64],
                        deadline_at=now() + timedelta(seconds=get_settings().trip_task_timeout_seconds))
        db.add(task)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(TripTask).where(TripTask.user_id == user_id, TripTask.idempotency_key == key))
            if existing is None or existing.fingerprint != fingerprint:
                raise BizException("同一幂等键不能用于不同请求", status_code=409)
            return existing.id, True
        return task.id, False


def snapshot(user_id, task_id):
    with SessionLocal() as db:
        task = db.scalar(select(TripTask).where(TripTask.id == task_id, TripTask.user_id == user_id))
        if task is None:
            raise BizException("任务不存在", status_code=404)
        result = {"id": task.id, "status": task.status, "stage": task.stage, "percent": task.percent,
                  "message": task.message, "error_code": task.error_code, "deadline_at": task.deadline_at.isoformat() + "Z"}
        if task.status == "succeeded":
            record = db.get(TripRecord, task.record_id)
            if record is None:
                result.update(status="failed", error_code="RESULT_DELETED", message="行程已删除")
            else:
                result["result"] = {"success": True, "message": "旅行计划已生成并保存", "saved": True,
                    "id": record.id, "version": record.version, "task_id": task.id,
                    "data": json.loads(record.plan_json), "quality": json.loads(record.quality_json)}
        return result


class TripTaskRunner:
    def __init__(self):
        self.stop_event = Event()
        self.lock = Lock()
        self.thread = None
        self.active = set()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            ensure_tables()
            with SessionLocal() as db:
                db.execute(update(TripTask).where(TripTask.status == "running").values(
                    status="failed", error_code="PROCESS_INTERRUPTED", message="服务重启，生成已中断，请重新提交"))
                db.commit()
            self.stop_event.clear()
            self.thread = Thread(target=self._loop, daemon=True, name="trip-task-scheduler")
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Task scheduler unavailable")
            self.stop_event.wait(0.25)

    def tick(self):
        with SessionLocal() as db:
            db.execute(update(TripTask).where(TripTask.status.in_(("queued", "running")), TripTask.deadline_at <= now())
                       .values(status="failed", error_code="TASK_TIMEOUT", message="规划超过总时间预算"))
            db.commit()
            task = db.scalar(select(TripTask).where(TripTask.status == "queued").order_by(TripTask.created_at).limit(1))
            if task is None or not llm_request_gate.try_acquire():
                return
            task_id = task.id
            claimed = db.execute(update(TripTask).where(TripTask.id == task_id, TripTask.status == "queued")
                                 .values(status="running", stage="starting", message="正在规划"))
            db.commit()
            if claimed.rowcount != 1:
                llm_request_gate.release()
                return
        try:
            Thread(target=self.execute, args=(task_id,), daemon=True, name="trip-generation").start()
        except Exception:
            llm_request_gate.release()
            with SessionLocal() as db:
                db.execute(update(TripTask).where(TripTask.id == task_id, TripTask.status == "running")
                           .values(status="failed", error_code="WORKER_START_FAILED", message="生成线程启动失败"))
                db.commit()
            raise

    def execute(self, task_id):
        from ..agents.trip_planner_agent import get_trip_planner_agent
        from .amap_service import get_amap_service
        from .execution import execution_deadline
        try:
            with SessionLocal() as db:
                task = db.get(TripTask, task_id)
                body = TripRequest.model_validate_json(task.request_json)
                user_id, deadline, request_id = task.user_id, task.deadline_at, task.request_id
            def progress(stage, percent, message):
                with SessionLocal() as db:
                    changed = db.execute(update(TripTask).where(TripTask.id == task_id, TripTask.status == "running",
                        TripTask.deadline_at > now()).values(stage=stage[:64], percent=percent, message=message[:255]))
                    db.commit()
                    if changed.rowcount != 1:
                        raise TimeoutError("Task expired")
            logger.info("Generation started request_id=%s task_id=%s", request_id, task_id)
            with execution_deadline(deadline) as usage:
                plan = get_trip_planner_agent().plan_trip(body, user_id=user_id, progress_callback=progress)
                routes = repair_plan_routes(plan, get_amap_service(), body.transportation)
                recalculate_budget(plan)
                quality = evaluate_plan(plan, body.travel_days).to_dict() | routes
                quality["usage"] = dict(usage)
            with SessionLocal() as db:
                # This CAS and the history/outbox insert form one transaction.
                changed = db.execute(update(TripTask).where(TripTask.id == task_id, TripTask.status == "running",
                    TripTask.deadline_at > now()).values(status="succeeded", stage="complete", percent=100,
                    message="旅行计划已生成并保存"))
                if changed.rowcount != 1:
                    db.rollback()
                    return
                record = history_service.create_trip_record(db, user_id, body, plan, quality=quality, commit=False)
                db.execute(update(TripTask).where(TripTask.id == task_id).values(record_id=record.id))
                db.commit()
            from ..core.trip_metrics import observe_trip_plan
            observe_trip_plan(quality, False)
            logger.info("Generation saved request_id=%s task_id=%s", request_id, task_id)
        except Exception as exc:
            logger.exception("Generation failed task_id=%s", task_id)
            try:
                with SessionLocal() as db:
                    db.execute(update(TripTask).where(TripTask.id == task_id, TripTask.status == "running").values(
                        status="failed", error_code="TASK_TIMEOUT" if isinstance(exc, TimeoutError) else "GENERATION_FAILED",
                        message="旅行计划未保存，请稍后重试"))
                    db.commit()
            except Exception:
                logger.exception("Cannot persist task failure; deadline/restart recovery will finalize it")
        finally:
            llm_request_gate.release()


runner = TripTaskRunner()
