"""来源优先的旅行资料研究接口。"""

from fastapi import APIRouter, Depends

from ...core.exceptions import BizException
from ...core.security import get_current_user
from ...db.models import User
from ...models.schemas import TravelResearchRequest
from ...services.rag_service import get_rag_service

router = APIRouter(prefix="/research", tags=["旅行资料研究"])


@router.post("", summary="检索带来源的城市旅行资料")
def research_city(
    body: TravelResearchRequest,
    _: User = Depends(get_current_user),
):
    """返回公开资料证据卡，不将检索片段伪装为模型验证后的结论。"""
    rag = get_rag_service()
    if not rag.enabled:
        raise BizException("旅行资料研究暂不可用，请检查嵌入服务配置", status_code=503)
    evidence = rag.retrieve_research_evidence(body.query, body.city, k=5)
    return {
        "success": True,
        "message": "以下为公开资料检索结果；请以原文件和页码为准。",
        "data": {
            "city": body.city,
            "query": body.query,
            "evidence": evidence,
        },
    }
