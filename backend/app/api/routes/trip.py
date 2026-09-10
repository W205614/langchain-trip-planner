"""Task API and compatibility adapters sharing one durable execution path."""
import asyncio
import json
import time
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from ...core.exceptions import BizException
from ...core.rate_limit import limiter
from ...core.security import get_current_user, require_admin
from ...db.models import User
from ...models.schemas import TripRequest, TripPlanResponse
from ...services import trip_tasks
from ...agents.trip_planner_agent import get_trip_planner_agent

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.get("/eval-policy")
def evaluation_policy(_: User = Depends(require_admin)):
    from ...services.eval_budget import policy
    return policy()


def _submit(request, body, user, key):
    from ...config import get_settings
    if not get_settings().trip_tasks_enabled:
        raise BizException("规划任务暂不可用", status_code=503)
    trip_tasks.runner.start()
    return trip_tasks.submit(user.id, body, key, getattr(request.state, "request_id", ""))


@router.post("/tasks", status_code=202)
@limiter.limit("5/minute")
def create_task(request: Request, body: TripRequest, user: User = Depends(get_current_user),
                idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    task_id, cached = _submit(request, body, user, idempotency_key)
    return {"success": True, "cached": cached, "data": trip_tasks.snapshot(user.id, task_id)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: User = Depends(get_current_user)):
    return {"success": True, "data": trip_tasks.snapshot(user.id, task_id)}


async def _events(request, user_id, task_id, cached=False):
    previous = None
    while not await request.is_disconnected():
        state = await asyncio.to_thread(trip_tasks.snapshot, user_id, task_id)
        if state["status"] == "succeeded":
            payload = state["result"] | {"cached": cached}
            yield "event: complete\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            return
        if state["status"] == "failed":
            yield "event: error\ndata: " + json.dumps(state, ensure_ascii=False) + "\n\n"
            return
        payload = json.dumps(state, ensure_ascii=False)
        if payload != previous:
            yield "event: progress\ndata: " + payload + "\n\n"
            previous = payload
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(1)


def _stream(request, user_id, task_id, cached=False):
    return StreamingResponse(_events(request, user_id, task_id, cached), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/tasks/{task_id}/events")
def task_events(request: Request, task_id: str, user: User = Depends(get_current_user)):
    trip_tasks.snapshot(user.id, task_id)
    return _stream(request, user.id, task_id)


@router.post("/plan", response_model=TripPlanResponse)
@limiter.limit("5/minute")
def plan_trip(request: Request, body: TripRequest, user: User = Depends(get_current_user),
              idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    task_id, cached = _submit(request, body, user, idempotency_key)
    while True:
        state = trip_tasks.snapshot(user.id, task_id)
        if state["status"] == "succeeded":
            return state["result"] | {"cached": cached}
        if state["status"] == "failed":
            raise BizException(state["message"], status_code=504 if state["error_code"] == "TASK_TIMEOUT" else 500,
                               code=state["error_code"])
        time.sleep(0.1)


@router.post("/plan/stream")
@limiter.limit("5/minute")
def plan_trip_stream(request: Request, body: TripRequest, user: User = Depends(get_current_user),
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    task_id, cached = _submit(request, body, user, idempotency_key)
    return _stream(request, user.id, task_id, cached)


@router.get("/health")
def health_check():
    info = get_trip_planner_agent().get_agent_info()
    return {"status": "healthy", "framework": info["framework"], "agent_name": info["name"]}
