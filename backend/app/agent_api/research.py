"""Separate no-generation retrieval from explicitly requested answer synthesis."""
from ..models.schemas import TravelResearchRequest
from ..services.rag_service import get_rag_service
from ..services.research_evidence import retrieval_result
from ..core.exceptions import BizException


def run(kind, body):
    request = TravelResearchRequest.model_validate(body)
    rag = get_rag_service()
    if not rag.enabled:
        raise BizException("旅行资料研究未启用或索引需要修复", status_code=503, code="RAG_DISABLED")
    if kind == "research-retrieve":
        evidence = rag.retrieve_research_evidence(request.query, request.city, k=5, local_only=True)
        return retrieval_result(request.query, request.city, evidence)
    from ..services.research_answer import build_research_answer
    evidence = rag.retrieve_research_evidence(request.query, request.city, k=5)
    return build_research_answer(request.query, request.city, evidence, request.trip_context)
