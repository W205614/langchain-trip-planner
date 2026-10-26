from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.research_evidence import evidence_contract, check_answer_support
from app.services import research_answer


def test_structural_and_semantic_support_are_different():
    evidence, _ = evidence_contract([{"source": "规则", "content": "每周一闭馆。", "source_tier": "official"}])
    assert check_answer_support("每周一闭馆。[1]", evidence)["passed"]
    assert "UNSUPPORTED_CLAIM" in check_answer_support("每周一开放。[1]", evidence)["issues"]
    assert "INVALID_CITATION" in check_answer_support("每周一闭馆。[9]", evidence)["issues"]
    assert "UNCITED_CLAIM" in check_answer_support("每周一闭馆。", evidence)["issues"]
    assert not check_answer_support("每周一闭馆。[1]价格为10元。[1]", evidence)["passed"]


def test_support_check_cannot_drop_negation_or_rewrite_number_punctuation():
    evidence, _ = evidence_contract([{"source": "规则", "content": "景区周一不开放。票价1.0元。费用调整-10元。"}])
    assert not check_answer_support("开放。[1]", evidence)["passed"]
    assert not check_answer_support("票价10元。[1]", evidence)["passed"]
    assert not check_answer_support("费用调整10元。[1]", evidence)["passed"]
    assert check_answer_support("景区周一不开放。[1]", evidence)["passed"]


def test_source_page_tier_and_time_legality():
    accepted, rejected = evidence_contract([
        {"source": "规则", "content": "开放", "page": 0},
        {"source": "规则", "content": "开放", "source_tier": "super_official"},
        {"source": "规则", "content": "开放", "expires_at": "not-a-date"},
        {"source": "规则", "content": "开放", "observed_at": "2025-01-01T00:00:00Z"},
    ], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(rejected) == 2
    assert accepted[0]["freshness"] == "invalid_timestamp"
    assert not accepted[0]["eligible_for_answer"]
    assert accepted[1]["freshness"] == "dated"


def test_pure_retrieval_route_cannot_generate_or_rebuild(monkeypatch):
    from app.agent_api import research
    calls = Mock()
    def retrieve(query, city, k, local_only=False):
        calls.retrieval(local_only)
        assert local_only is True
        return [{"content": "演示数据：门票10元", "source": "资料", "source_tier": "curated_static"}]
    monkeypatch.setattr(research, "get_rag_service", lambda: SimpleNamespace(enabled=True, retrieve_research_evidence=retrieve))
    monkeypatch.setattr(research_answer, "get_llm", lambda **kw: calls.generation())
    result = research.run("research-retrieve", {"city": "北京", "query": "门票价格"})
    assert result["generation_calls"] == 0
    assert result["answer"] is None
    assert result["answer_status"] == "not_generated"
    assert result["evidence"][0]["data_classification"] == "demonstration"
    calls.generation.assert_not_called()


def test_demo_disclaimer_is_inherited_by_non_header_chunks(rag):
    from app.services import rag_service
    rag_service.KNOWLEDGE_DIR.mkdir(parents=True)
    (rag_service.KNOWLEDGE_DIR / "beijing.md").write_text("# 北京\n演示数据\n\n## 参观\n预约参观。\n\n### 景点\n门票示例。", encoding="utf-8")
    documents = rag._load_knowledge_documents()
    beijing = [doc for doc in documents if doc.metadata.get("source") == "beijing.md"]
    assert len(beijing) > 2
    assert all(doc.metadata["data_classification"] == "demonstration" for doc in beijing)
    assert rag_service.RagService._research_file_classification({"source_type": "markdown", "source": "beijing.md"}) == {"data_classification": "demonstration"}


def test_local_only_retrieval_does_not_call_embedding_or_city_index(rag, monkeypatch):
    from langchain_core.documents import Document
    rag.ensure_city_index = Mock(side_effect=AssertionError("city build forbidden"))
    rag._embedding = Mock()
    rag._embedding.embed_query.side_effect = AssertionError("embedding forbidden")
    monkeypatch.setattr(rag, "_refresh_store", lambda *_: None)
    monkeypatch.setattr(rag, "_hybrid_public_search", lambda query, city, k, embedding: [Document(page_content="预约信息", metadata={"source": "规则"})])
    monkeypatch.setattr(rag, "_visible_documents", lambda documents: documents)
    assert rag.retrieve_research_evidence("预约信息", "北京", local_only=True)[0]["content"] == "预约信息"
    rag.ensure_city_index.assert_not_called()
    rag._embedding.embed_query.assert_not_called()
