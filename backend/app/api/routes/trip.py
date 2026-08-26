"""旅行规划API路由"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...models.schemas import TripRequest, TripPlanResponse
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...core.exceptions import BizException
from ...core.rate_limit import limiter
from ...core.security import get_current_user
from ...db.database import get_db
from ...db.models import User
from ...services import history_service
from ...services.rag_service import get_rag_service

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

    # 获取Agent实例并生成旅行计划 (异常由全局异常处理器统一兜底)
    agent = get_trip_planner_agent()
    trip_plan = agent.plan_trip(body)

    # 生成成功 → 保存历史(归属当前用户) + RAG 入库 (失败仅告警, 不影响主流程)
    try:
        record = history_service.create_trip_record(db, current_user.id, body, trip_plan)
        get_rag_service().add_history_plan(record.id, body, trip_plan)
    except Exception as e:
        logger.warning(f"⚠️ 历史记录/RAG 保存失败(不影响行程): {e}")

    return TripPlanResponse(
        success=True,
        message="旅行计划生成成功",
        data=trip_plan,
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
