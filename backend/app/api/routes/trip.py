"""旅行规划API路由"""

import hashlib
import json
import logging
from queue import Empty, Queue
from threading import Lock, Thread
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...models.schemas import TripRequest, TripPlanResponse
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...core.exceptions import BizException
from ...core.rate_limit import limiter
from ...core.security import get_current_user
from ...core.trip_metrics import observe_trip_plan, observe_trip_stream
from ...db.database import get_db
from ...db.models import User
from ...services import history_service
from ...services.idempotency import idempotency_store
from ...services.amap_service import get_amap_service
from ...services.plan_quality import evaluate_plan, repair_plan_routes

router = APIRouter(prefix="/trip", tags=["旅行规划"])

logger = logging.getLogger(__name__)


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划 (需登录, 限流防刷)",
)
@limiter.limit("5/minute")  # AI 生成耗 token, 限制单IP每分钟最多5次, 防滥用
def plan_trip(
    request: Request,  # slowapi 要求第一个参数是 starlette Request
    body: TripRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # 需登录
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """
    生成旅行计划

    注意: 本接口不声明 async。内部是同步的 LLM+高德调用(可能耗时30秒+),
    由 FastAPI 自动放到线程池执行, 避免阻塞事件循环拖慢其他接口。

    生成成功后自动:
    1. 保存历史记录到 SQLite (归属当前登录用户, 供 /api/history 查询)
    2. 写入 RAG 向量库 (供下次规划时检索参考)
    (以上两步失败不影响行程返回)

    Args:
        request: FastAPI Request (slowapi 限流注入)
        body: 旅行请求参数
        db: 数据库会话
        current_user: 当前登录用户 (由 JWT 解析)

    Returns:
        旅行计划响应
    """
    logger.info(
        f"收到旅行规划请求: 用户={current_user.username}, 城市={body.city}, "
        f"日期={body.start_date}~{body.end_date}, 天数={body.travel_days}"
    )

    # Idempotency-Key 保护浏览器重试/重复点击：同一用户 + 同一请求在短 TTL 内只创建一份历史。
    # 本地默认使用进程内实现；多实例生产环境可替换为 Redis 等共享存储。
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    def _generate_and_persist() -> TripPlanResponse:
        agent = get_trip_planner_agent()
        trip_plan = agent.plan_trip(body, user_id=current_user.id)
        route_quality = repair_plan_routes(trip_plan, get_amap_service(), body.transportation)
        quality = evaluate_plan(trip_plan, body.travel_days).to_dict() | route_quality
        try:
            history_service.create_trip_record(db, current_user.id, body, trip_plan)
        except Exception as e:
            logger.warning(f"⚠️ 历史记录/RAG 保存失败(不影响行程): {e}")
        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan,
            quality=quality,
        )

    try:
        response, cached = idempotency_store.execute(
            current_user.id, idempotency_key, fingerprint, _generate_and_persist
        )
    except ValueError as exc:
        raise BizException(str(exc), status_code=409) from exc
    if response.quality:
        observe_trip_plan(response.quality, cached)
    if cached:
        return response.model_copy(update={"cached": True, "message": "已复用相同请求的旅行计划"})
    return response


@router.post(
    "/plan/stream",
    summary="流式生成旅行计划",
    description="以 Server-Sent Events 返回真实 LangGraph 阶段进度及最终行程（需登录）。",
)
def plan_trip_stream(
    body: TripRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """流式包装同步 Agent，前端不再伪造定时进度条。"""
    events: Queue[tuple[str, dict]] = Queue()
    stream_started_at = perf_counter()
    first_event_seconds: float | None = None
    first_event_lock = Lock()
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    def _emit(stage: str, percent: int, message: str) -> None:
        nonlocal first_event_seconds
        with first_event_lock:
            if first_event_seconds is None:
                # 当前 Agent 以 invoke 获取完整日计划，不能把这个 UX 指标冒充模型 TTFT。
                first_event_seconds = perf_counter() - stream_started_at
        events.put(("progress", {"stage": stage, "percent": percent, "message": message}))

    def _run() -> None:
        outcome = "error"
        try:
            def _generate_and_persist() -> TripPlanResponse:
                agent = get_trip_planner_agent()
                trip_plan = agent.plan_trip(body, user_id=current_user.id, progress_callback=_emit)
                route_quality = repair_plan_routes(trip_plan, get_amap_service(), body.transportation)
                quality = evaluate_plan(trip_plan, body.travel_days).to_dict() | route_quality
                try:
                    history_service.create_trip_record(db, current_user.id, body, trip_plan)
                except Exception as exc:
                    logger.warning("历史记录/RAG 保存失败(不影响流式返回): %s", exc)
                return TripPlanResponse(success=True, message="旅行计划生成成功", data=trip_plan, quality=quality)

            response, cached = idempotency_store.execute(
                current_user.id, idempotency_key, fingerprint, _generate_and_persist
            )
            if response.quality:
                observe_trip_plan(response.quality, cached)
            payload = response.model_copy(update={"cached": cached}).model_dump(mode="json")
            events.put(("complete", payload))
            outcome = "success"
        except Exception:
            logger.exception("流式旅行规划失败")
            events.put(("error", {"message": "旅行计划生成失败，请稍后重试"}))
        finally:
            observe_trip_stream(first_event_seconds, perf_counter() - stream_started_at, outcome)

    Thread(target=_run, name="trip-plan-stream", daemon=True).start()

    def _event_stream():
        while True:
            try:
                event, payload = events.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if event in {"complete", "error"}:
                return

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常",
)
async def health_check():
    """健康检查"""
    try:
        agent = get_trip_planner_agent()
        info = agent.get_agent_info()

        return {
            "status": "healthy",
            "service": "trip-planner",
            "agent_name": info["name"],
            "framework": info["framework"],
            "nodes_count": len(info["nodes"]),
        }
    except Exception as e:
        raise BizException(f"服务不可用: {str(e)}", status_code=503)
