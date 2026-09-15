"""Internal execution protocol. Durable business state is owned by Java."""
import asyncio
import hashlib
import hmac
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from queue import Queue, Empty
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from ..models.schemas import TripRequest, TripPlan, Location, POIInfo
from ..services.execution import execution_deadline, cancellation_var, trusted_evidence_var, rag_degradation_var
from ..core.exceptions import BizException, biz_exception_handler
from ..services.agent_paths import DATA_DIR


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
    usage: dict[str, int]


def authorize(x_service_key: str = Header(default="")):
    expected = os.environ.get("INTERNAL_SERVICE_KEY", "")
    if len(expected.encode()) < 32 or not hmac.compare_digest(expected, x_service_key):
        raise HTTPException(401, "Internal service authentication required")


_lock = threading.Lock()
# Completed registrations remain until process exit; capacity exhaustion fails closed.
_executions: dict[str, tuple[str, threading.Event]] = {}
_slots = threading.BoundedSemaphore(4)


@asynccontextmanager
async def lifespan(app):
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
                for _, cancelled in _executions.values():
                    cancelled.set()


app = FastAPI(title="Trip Agent Internal API", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_exception_handler(BizException, biz_exception_handler)


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
        if identity in _executions:
            raise HTTPException(409, "Execution already registered; do not regenerate")
        if len(_executions) >= 10000 or not _slots.acquire(blocking=False):
            raise HTTPException(429, "Agent capacity exhausted")
        _executions[identity] = (fingerprint, cancelled)
    events = Queue(maxsize=128)

    def emit(event, payload):
        if cancelled.is_set():
            raise TimeoutError("Execution cancelled")
        events.put((event, payload), timeout=2)

    def work():
        token = cancellation_var.set(cancelled)
        try:
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
                emit("result", {"protocol_version":1,"plan": plan.model_dump(), "trusted_candidates": list(evidence.values()), "usage": dict(stats), "execution_id": identity})
        except Exception as exc:
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
            _slots.release()

    worker = threading.Thread(target=work, daemon=True, name=f"agent-{identity}")
    try:
        worker.start()
    except Exception:
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
            item[1].set()
    return {"success": True}


class RouteCoordinates(BaseModel):
    left: Location
    right: Location
    route_type: Literal["walking", "driving", "transit"]
    city: str


@app.post("/internal/v1/map/route-coordinates", dependencies=[Depends(authorize)])
def route(body: RouteCoordinates):
    from ..services.amap_service import get_amap_service
    return get_amap_service().plan_route_by_locations(body.left, body.right, route_type=body.route_type, city=body.city)


@app.post("/internal/v1/capabilities/{kind}", dependencies=[Depends(authorize)])
def capability(kind: str, body: dict):
    from ..services.amap_service import get_amap_service
    from ..services.rag_service import get_rag_service
    from ..models.schemas import RouteRequest, TravelResearchRequest, POISearchRequest
    if kind == "validation":
        if os.environ.get("APP_ENV") != "validation" or os.environ.get("VALIDATION_ALLOW_FIXTURES") != "yes":
            raise HTTPException(404, "Not available")
        from ..services.amap_service import get_amap_service
        return {"offline_fixture": get_amap_service().transport == "fixture"}
    elif kind == "poi":
        request = POISearchRequest.model_validate(body)
        result = get_amap_service().search_poi(request.keywords, request.city, request.citylimit)
    elif kind == "photo":
        from ..api.routes.poi import _resolve_attraction_photo
        result = {"name": body["name"], "photo_url": _resolve_attraction_photo(body["name"])}
    elif kind == "photo-image":
        import base64
        from ..api.routes.poi import render_attraction_photo
        image = render_attraction_photo(body["name"], body.get("poi_id", ""), body.get("city", ""))
        return {"content_type": image.media_type, "content": base64.b64encode(image.body).decode()}
    elif kind == "eval-policy":
        from ..services.eval_budget import policy
        return policy()
    elif kind == "detail":
        result = get_amap_service().get_poi_detail(body["poi_id"])
    elif kind == "weather":
        result = get_amap_service().get_weather(body["city"])
    elif kind == "route":
        request = RouteRequest.model_validate(body)
        result = get_amap_service().plan_route(**request.model_dump())
    elif kind == "research":
        request = TravelResearchRequest.model_validate(body)
        rag = get_rag_service()
        if not rag.enabled:
            raise BizException("旅行资料研究未启用或索引需要修复", status_code=503, code="RAG_DISABLED")
        evidence = rag.retrieve_research_evidence(request.query, request.city, k=5)
        result = {"city": request.city, "query": request.query, "evidence": evidence,
                  "status": "matched" if evidence else "no_match"}
    elif kind == "rag-status":
        rag = get_rag_service()
        return {"success": True, "enabled": rag.enabled, "embedding_model": rag._embedding.model if rag.enabled else None}
    elif kind == "map-health":
        service = get_amap_service()
        return {"status": "healthy", "service": "map-service", "transport": service.transport,
                "connectivity_checked": False, "amap_api_key_configured": bool(service.api_key)}
    else:
        raise HTTPException(404, "Unknown capability")
    return {"success": True, "message": "查询完成", "data": result}


@app.post("/internal/v1/index/{kind}", dependencies=[Depends(authorize)])
def index(kind: Literal["history", "document", "rebuild"], body: dict):
    if kind == "rebuild":
        from .rebuild import rebuild
        return rebuild()
    from . import indexing
    from .contracts import HistoryIndexRequest, DocumentIndexRequest
    contract = HistoryIndexRequest if kind == "history" else DocumentIndexRequest
    return getattr(indexing, kind)(contract.model_validate(body).model_dump())


@app.post("/internal/v1/documents/extract", dependencies=[Depends(authorize)])
def extract_document(body: dict):
    from .extraction import extract
    from .contracts import DocumentExtractRequest, DocumentExtractResult
    return DocumentExtractResult.model_validate(extract(DocumentExtractRequest.model_validate(body).model_dump())).model_dump()
