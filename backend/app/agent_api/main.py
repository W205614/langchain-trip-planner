"""Internal execution protocol. Durable business state is owned by Java."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Queue, Empty
from typing import Literal
from uuid import UUID
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from prometheus_client import Counter, Gauge, Histogram

from ..models.schemas import TripRequest, TripPlan, Location, POIInfo
from ..services.execution import execution_deadline, cancellation_var, trusted_evidence_var, rag_degradation_var
from ..core.exceptions import BizException, biz_exception_handler
from ..core.logging import request_id_context, setup_logging
from ..services.agent_paths import DATA_DIR


logger = logging.getLogger(__name__)


def _bounded_env(name: str, default: int, maximum: int = 256) -> int:
    try:
        return max(1, min(maximum, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


class Execution(BaseModel):
    protocol_version: Literal[1] = 1
    task_id: UUID
    execution_id: UUID
    request_id: str = Field(max_length=64)
    user_id: int = Field(gt=0)
    deadline_at: datetime
    request: TripRequest
    original_plan: TripPlan | None = None
    original_version: int | None = Field(default=None, ge=1)
    day_index: int | None = Field(default=None, ge=0, le=29)
    instruction: str = Field(default="", max_length=500)


class ExecutionResult(BaseModel):
    protocol_version: Literal[1] = 1
    execution_id: UUID
    plan: TripPlan
    trusted_candidates: list[POIInfo] = Field(max_length=5000)
    quality: dict
    usage: dict[str, int]


def authorize(x_service_key: str = Header(default="")):
    expected = os.environ.get("INTERNAL_SERVICE_KEY", "")
    if len(expected.encode()) < 32 or not hmac.compare_digest(expected, x_service_key):
        raise HTTPException(401, "Internal service authentication required")


@dataclass
class ExecutionRegistration:
    fingerprint: str
    cancelled: threading.Event
    completed_at: float | None = None


_lock = threading.Lock()
_executions: dict[str, ExecutionRegistration] = {}
_slots = threading.BoundedSemaphore(_bounded_env("AGENT_EXECUTION_MAX_CONCURRENCY", 4, 32))
_slow_slots = threading.BoundedSemaphore(_bounded_env("AGENT_SLOW_CAPABILITY_MAX_CONCURRENCY", 4, 32))
_tool_slots = threading.BoundedSemaphore(_bounded_env("AGENT_TOOL_MAX_CONCURRENCY", 8, 64))
_execution_retention_seconds = _bounded_env("AGENT_EXECUTION_RETENTION_SECONDS", 900, 86400)
_execution_registry_max = _bounded_env("AGENT_EXECUTION_REGISTRY_MAX", 10000, 100000)

HTTP_REQUESTS = Counter(
    "trip_agent_http_requests_total", "Agent HTTP requests", ("method", "path", "status")
)
HTTP_SECONDS = Histogram(
    "trip_agent_http_request_seconds", "Agent HTTP latency", ("method", "path")
)
HTTP_IN_FLIGHT = Gauge("trip_agent_http_in_flight", "Agent HTTP requests currently running")
CAPACITY_REJECTED = Counter(
    "trip_agent_capacity_rejected_total", "Agent work rejected before execution", ("kind",)
)
EXECUTION_ACTIVE = Gauge("trip_agent_execution_active", "Agent itinerary executions currently active")
EXECUTION_REGISTRY = Gauge("trip_agent_execution_registry_size", "Retained execution identities")


def _refresh_execution_gauges() -> None:
    EXECUTION_REGISTRY.set(len(_executions))
    EXECUTION_ACTIVE.set(sum(item.completed_at is None for item in _executions.values()))


def _purge_executions(now: float) -> None:
    stale = [
        identity
        for identity, item in _executions.items()
        if item.completed_at is not None and now - item.completed_at >= _execution_retention_seconds
    ]
    for identity in stale:
        _executions.pop(identity, None)
    if len(_executions) >= _execution_registry_max:
        completed = sorted(
            (item.completed_at, identity)
            for identity, item in _executions.items()
            if item.completed_at is not None
        )
        for _, identity in completed[: max(0, len(_executions) - _execution_registry_max + 1)]:
            _executions.pop(identity, None)
    _refresh_execution_gauges()


@contextmanager
def capacity(kind: str, slots: threading.BoundedSemaphore):
    if not slots.acquire(blocking=False):
        CAPACITY_REJECTED.labels(kind).inc()
        logger.warning("agent_capacity_rejected kind=%s", kind)
        raise HTTPException(429, f"Agent {kind} capacity exhausted")
    try:
        yield
    finally:
        slots.release()


@asynccontextmanager
async def lifespan(app):
    setup_logging()
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError("Agent must not receive business database credentials")
    if len(os.environ.get("INTERNAL_SERVICE_KEY", "").encode()) < 32:
        raise RuntimeError("INTERNAL_SERVICE_KEY requires at least 32 bytes")
    from filelock import FileLock
    from urllib.parse import urlparse
    url = urlparse(os.environ.get("BUSINESS_URL", ""))
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise RuntimeError("BUSINESS_URL requires a fixed service HTTP address")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    instance = FileLock(str(DATA_DIR / "agent-instance.lock"), timeout=0)
    with instance:
        try:
            yield
        finally:
            with _lock:
                for registration in _executions.values():
                    registration.cancelled.set()


app = FastAPI(title="Trip Agent Internal API", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_exception_handler(BizException, biz_exception_handler)


@app.middleware("http")
async def correlate_request(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if incoming and len(incoming) <= 64 and incoming.replace("-", "").replace("_", "").isalnum() else str(uuid4())
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    status = 500
    HTTP_IN_FLIGHT.inc()
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f'agent;dur={(time.perf_counter() - started) * 1000:.1f}'
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - started
        HTTP_IN_FLIGHT.dec()
        HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
        HTTP_SECONDS.labels(request.method, path).observe(elapsed)
        if status >= 400 or elapsed >= 1:
            logger.warning(
                "agent_request_complete method=%s path=%s status=%s elapsed_ms=%.1f",
                request.method,
                path,
                status,
                elapsed * 1000,
            )
        request_id_context.reset(token)


@app.exception_handler(ValidationError)
async def invalid_capability_input(request, exception):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail":"Invalid internal capability request"})


@app.get("/metrics")
def metrics():
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthy():
    return {"status": "healthy", "service": "trip-agent"}


@app.get("/readyz")
def ready():
    import tempfile
    try:
        with tempfile.TemporaryFile(dir=DATA_DIR) as check:
            check.write(b"ready")
            check.flush()
    except OSError:
        raise HTTPException(503, "Agent local storage unavailable") from None
    # Readiness never invokes embeddings or an external model.
    from ..services import rag_service
    rag = getattr(rag_service, "_rag_service", None)
    return {"status": "ready", "service": "trip-agent", "rag": "available" if rag and rag.enabled else "not_initialized_or_degraded"}


@app.post("/internal/v1/executions", dependencies=[Depends(authorize)])
def execute(body: Execution, request: Request):
    deadline = body.deadline_at
    if deadline.tzinfo is None:
        raise HTTPException(422, "deadline_at requires timezone")
    deadline = deadline.astimezone(timezone.utc).replace(tzinfo=None)
    if deadline <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(409, "Execution expired")
    if body.original_plan is not None and (body.day_index is None or not body.instruction):
        raise HTTPException(422, "Revision requires day_index and instruction")
    identity = str(body.execution_id)
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    cancelled = threading.Event()
    with _lock:
        _purge_executions(time.monotonic())
        if identity in _executions:
            raise HTTPException(409, "Execution already registered; do not regenerate")
        if len(_executions) >= _execution_registry_max or not _slots.acquire(blocking=False):
            CAPACITY_REJECTED.labels("execution").inc()
            logger.warning("agent_capacity_rejected kind=execution registry=%s", len(_executions))
            raise HTTPException(429, "Agent capacity exhausted")
        _executions[identity] = ExecutionRegistration(fingerprint, cancelled)
        _refresh_execution_gauges()
    events = Queue(maxsize=128)

    def emit(event, payload):
        if cancelled.is_set():
            raise TimeoutError("Execution cancelled")
        events.put((event, payload), timeout=2)

    def work():
        work_started = time.perf_counter()
        request_token = request_id_context.set(body.request_id)
        token = cancellation_var.set(cancelled)
        try:
            logger.info("agent_execution_started execution_id=%s task_id=%s", identity, body.task_id)
            from ..agents.trip_planner_agent import get_trip_planner_agent
            planner = get_trip_planner_agent()
            def usage(stats, before_call):
                emit("usage", stats)
            evidence = {}
            evidence_token = trusted_evidence_var.set(evidence)
            notices = []
            notices_token = rag_degradation_var.set(notices)
            with execution_deadline(deadline, usage) as stats:
                if body.original_plan is None:
                    plan = planner.plan_trip(body.request, user_id=body.user_id,
                        progress_callback=lambda stage, percent, message: emit("progress", dict(stage=stage, percent=percent, message=message)))
                else:
                    plan = planner.revise_trip_day(body.request, body.original_plan, body.day_index,
                        body.instruction, user_id=body.user_id)
                plan.enrichment_notices = list(dict.fromkeys([*plan.enrichment_notices, *notices]))
                # The Agent owns travel generation end to end: trusted POI selection,
                # route lookup, bounded repair and quality classification. Java validates
                # the returned protocol and owns task lifecycle and persistence only.
                from ..services.planning_constraints import finalize_plan
                precheck_started = time.perf_counter()
                emit("progress", {"stage":"agent_precheck", "percent":90,
                    "message":"Agent 草稿已生成，正在核验路线与行程约束"})
                quality = finalize_plan(plan, body.request, planner.amap_service, repair=True)
                quality.setdefault("timings", {})["agent_route_and_constraints_ms"] = round(
                    (time.perf_counter() - precheck_started) * 1000
                )
                from ..core.trip_metrics import observe_agent_stage
                observe_agent_stage("agent_precheck", time.perf_counter() - precheck_started)
                emit("result", {"protocol_version":1,"plan": plan.model_dump(),
                    "trusted_candidates": list(evidence.values()), "quality": quality,
                    "usage": dict(stats), "execution_id": identity})
        except Exception as exc:
            logger.warning(
                "agent_execution_failed execution_id=%s type=%s elapsed_ms=%.1f",
                identity,
                type(exc).__name__,
                (time.perf_counter() - work_started) * 1000,
            )
            if not cancelled.is_set():
                try:
                    emit("error", {"code": "TASK_TIMEOUT" if isinstance(exc, TimeoutError) else getattr(exc, "code", "GENERATION_FAILED")})
                except Exception:
                    cancelled.set()
        finally:
            if "evidence_token" in locals():
                trusted_evidence_var.reset(evidence_token)
            if "notices_token" in locals():
                rag_degradation_var.reset(notices_token)
            cancellation_var.reset(token)
            request_id_context.reset(request_token)
            with _lock:
                registration = _executions.get(identity)
                if registration is not None:
                    registration.completed_at = time.monotonic()
                _refresh_execution_gauges()
            _slots.release()
            logger.info(
                "agent_execution_finished execution_id=%s elapsed_ms=%.1f",
                identity,
                (time.perf_counter() - work_started) * 1000,
            )

    worker = threading.Thread(target=work, daemon=True, name=f"agent-{identity}")
    try:
        worker.start()
    except Exception:
        with _lock:
            _executions.pop(identity, None)
            _refresh_execution_gauges()
        _slots.release()
        raise

    async def stream():
        try:
            while True:
                if await request.is_disconnected() or cancelled.is_set():
                    return
                try:
                    event, payload = await asyncio.to_thread(events.get, True, 1)
                except Empty:
                    if not worker.is_alive():
                        return
                    yield ": heartbeat\n\n"
                    continue
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if event in {"result", "error"}:
                    return
        finally:
            cancelled.set()
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/internal/v1/executions/{execution_id}/cancel", dependencies=[Depends(authorize)])
def cancel(execution_id: UUID):
    with _lock:
        item = _executions.get(str(execution_id))
        if item:
            item.cancelled.set()
    return {"success": True}


@app.post("/internal/v1/capabilities/{kind}", dependencies=[Depends(authorize)])
def capability(kind: str, body: dict):
    from ..services.rag_service import get_rag_service
    from ..models.schemas import TravelResearchRequest, POISearchRequest, POIDetailRequest
    if kind == "validation":
        if os.environ.get("APP_ENV") != "validation" or os.environ.get("VALIDATION_ALLOW_FIXTURES") != "yes":
            raise HTTPException(404, "Not available")
        return {"offline_fixture": os.environ.get("VALIDATION_ALLOW_FIXTURES") == "yes"}
    elif kind == "eval-policy":
        from ..services.eval_budget import policy
        return policy()
    elif kind == "research":
        with capacity("research", _slow_slots):
            request = TravelResearchRequest.model_validate(body)
            rag = get_rag_service()
            if not rag.enabled:
                raise BizException("旅行资料研究未启用或索引需要修复", status_code=503, code="RAG_DISABLED")
            evidence = rag.retrieve_research_evidence(request.query, request.city, k=5)
            from ..services.research_answer import build_research_answer
            result = build_research_answer(request.query, request.city, evidence, request.trip_context)
    elif kind == "assistant-intent":
        from ..services.assistant_intent import classify_assistant_intent
        content = str(body.get("content") or "").strip()
        travel_days = body.get("travel_days", 0)
        if len(content) < 2 or len(content) > 500 or not isinstance(travel_days, int) or not 1 <= travel_days <= 30:
            raise HTTPException(422, "Invalid assistant intent request")
        result = classify_assistant_intent(content, travel_days)
    elif kind == "poi-search":
        with capacity("tool", _tool_slots):
            request = POISearchRequest.model_validate(body)
            from ..services.amap_service import get_amap_service
            result = [poi.model_dump() for poi in get_amap_service().search_poi(
                request.keywords, request.city, request.citylimit
            )]
    elif kind == "poi-detail":
        with capacity("tool", _tool_slots):
            request = POIDetailRequest.model_validate(body)
            from ..services.amap_service import get_amap_service
            raw = get_amap_service().get_poi_detail(request.poi_id)
            if not raw or raw.get("id") != request.poi_id:
                raise HTTPException(404, "POI not found")
            photos = raw.get("photos") or []
            result = {
                "id": str(raw.get("id") or ""),
                "name": str(raw.get("name") or ""),
                "type": raw.get("type") if isinstance(raw.get("type"), str) else "",
                "address": raw.get("address") if isinstance(raw.get("address"), str) else "",
                "city": raw.get("cityname") if isinstance(raw.get("cityname"), str) else "",
                "photos": [p.get("url") for p in photos if isinstance(p, dict) and p.get("url")],
            }
    elif kind == "rag-status":
        rag = get_rag_service()
        return {"success": True, "enabled": rag.enabled, "embedding_model": rag._embedding.model if rag.enabled else None}
    elif kind == "status":
        from ..config import get_settings
        cfg = get_settings()
        rag = get_rag_service()
        return {"success": True, "rag": "available" if rag.enabled else "disabled",
                "map": "mcp" if cfg.amap_transport == "mcp" and bool(cfg.amap_api_key) else "not_configured",
                "vision": "available" if cfg.vision_model and (cfg.vision_api_key or cfg.llm_api_key) else "not_configured"}
    else:
        raise HTTPException(404, "Unknown capability")
    return {"success": True, "message": "查询完成", "data": result}


@app.post("/internal/v1/capabilities/research/stream", dependencies=[Depends(authorize)])
def research_stream(body: dict, http_request: Request):
    """检索后直接转发模型分片；用终态 result 覆盖可能存在的半截输出。"""
    from ..models.schemas import TravelResearchRequest
    from ..services.rag_service import get_rag_service

    request = TravelResearchRequest.model_validate(body)
    rag = get_rag_service()
    if not rag.enabled:
        raise BizException("旅行资料研究未启用或索引需要修复", status_code=503, code="RAG_DISABLED")
    request_id = getattr(http_request.state, "request_id", "-")

    def events():
        from ..core.trip_metrics import observe_agent_stage

        def with_request_context(callback):
            token = request_id_context.set(request_id)
            try:
                return callback()
            finally:
                request_id_context.reset(token)

        started_at = time.perf_counter()
        try:
            with capacity("research", _slow_slots):
                yield "event: progress\ndata: " + json.dumps(
                    {"stage": "research_retrieval", "message": "正在检索当前旅行资料"}, ensure_ascii=False
                ) + "\n\n"
                evidence = with_request_context(
                    lambda: rag.retrieve_research_evidence(request.query, request.city, k=5)
                )
                observe_agent_stage("research_retrieval", time.perf_counter() - started_at)
                from ..services.research_answer import stream_research_answer
                stream = with_request_context(
                    lambda: iter(
                        stream_research_answer(
                            request.query, request.city, evidence, request.trip_context
                        )
                    )
                )
                while True:
                    try:
                        event, payload = with_request_context(lambda: next(stream))
                    except StopIteration:
                        break
                    yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            observe_agent_stage("research_retrieval", time.perf_counter() - started_at, "error")
            payload = {
                "code": getattr(exc, "code", "RESEARCH_UNAVAILABLE"),
                "message": "攻略问答暂不可用",
            }
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/internal/v1/index/{kind}", dependencies=[Depends(authorize)])
def index(kind: Literal["history", "document", "rebuild"], body: dict):
    with capacity("index", _slow_slots):
        if kind == "rebuild":
            from .rebuild import rebuild
            return rebuild()
        from . import indexing
        from .contracts import HistoryIndexRequest, DocumentIndexRequest
        contract = HistoryIndexRequest if kind == "history" else DocumentIndexRequest
        return getattr(indexing, kind)(contract.model_validate(body).model_dump())


@app.post("/internal/v1/documents/extract", dependencies=[Depends(authorize)])
def extract_document(body: dict):
    with capacity("document", _slow_slots):
        from .extraction import extract
        from .contracts import DocumentExtractRequest, DocumentExtractResult
        return DocumentExtractResult.model_validate(extract(DocumentExtractRequest.model_validate(body).model_dump())).model_dump()
