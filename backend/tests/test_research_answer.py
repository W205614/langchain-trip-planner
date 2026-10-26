from types import SimpleNamespace

from app.services import research_answer


def test_research_answer_returns_direct_grounded_text_and_sources(monkeypatch):
    class Llm:
        def invoke(self, _messages):
            return SimpleNamespace(content="故宫实行预约参观。[1]")

    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: Llm())
    result = research_answer.build_research_answer(
        "故宫如何预约？",
        "北京",
        [{"content": "故宫实行预约参观。", "source": "北京旅行资料", "page": 2, "source_tier": "curated_static"}],
    )

    assert result["answer"] == "故宫实行预约参观。[1]"
    assert result["answer_status"] == "supported_excerpt_answer"
    assert result["sources"][0]["source"] == "北京旅行资料"
    assert result["sources"][0]["freshness"] == "unknown"
    assert result["support_check"]["independent_semantic_evaluation"] is False


def test_research_answer_without_evidence_explains_the_gap_without_model_call(monkeypatch):
    monkeypatch.setattr(
        research_answer,
        "get_llm",
        lambda timeout: (_ for _ in ()).throw(AssertionError("model must not be called")),
    )
    result = research_answer.build_research_answer("冷门地点开放吗？", "北京", [])

    assert "没有找到足以回答" in result["answer"]
    assert result["answer_status"] == "insufficient_evidence"
    assert result["sources"] == []


def test_research_answer_degrades_to_readable_evidence_when_model_fails(monkeypatch):
    monkeypatch.setattr(
        research_answer,
        "get_llm",
        lambda timeout: (_ for _ in ()).throw(TimeoutError("fixture")),
    )
    result = research_answer.build_research_answer(
        "怎么安排？", "北京", [{"content": "建议预留半天参观。", "source": "资料"}]
    )

    assert "建议预留半天参观" in result["answer"]
    assert result["answer_status"] == "extractive_fallback"


def test_stream_research_answer_never_exposes_unchecked_provider_tokens(monkeypatch):
    class Llm:
        def invoke(self, _messages):
            return SimpleNamespace(content="无需预约而且永久免费。[1]")

    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: Llm())
    events = list(research_answer.stream_research_answer(
        "如何预约？", "北京", [{"content": "实行预约。", "source": "资料"}]
    ))

    assert [event for event, _ in events] == ["progress", "result"]
    assert events[-1][1]["answer_status"] == "extractive_fallback"
    assert "永久免费" not in events[-1][1]["answer"]


def test_valid_citation_does_not_establish_support(monkeypatch):
    class Llm:
        def invoke(self, _messages):
            return SimpleNamespace(content="景区免预约、免门票。[1]")
    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: Llm())
    result = research_answer.build_research_answer("预约吗", "北京", [{"source": "官方声明", "source_tier": "official", "content": "景区实行实名预约。"}])
    assert result["answer_status"] == "extractive_fallback"
    assert "UNSUPPORTED_CLAIM" in result["support_check"]["issues"]
    assert "免门票" not in result["answer"]
    assert result["semantic_truth_status"] == "not_independently_evaluated"


def test_demo_and_expired_evidence_are_not_promoted_to_generated_facts(monkeypatch):
    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: (_ for _ in ()).throw(AssertionError("no generation")))
    result = research_answer.build_research_answer("门票", "北京", [
        {"source": "演示资料", "content": "门票十元。", "is_demo": True},
        {"source": "旧规则", "content": "无需预约。", "expires_at": "2020-01-01T00:00:00Z"}])
    assert result["answer_status"] == "insufficient_evidence"
    assert result["generation_calls"] == 0
    assert result["data_classification"] == "mixed"
    assert result["evidence"][0]["data_classification"] == "demonstration"
    assert result["evidence"][1]["freshness"] == "expired"


def test_answer_citation_numbers_match_visible_eligible_evidence(monkeypatch):
    class Llm:
        def invoke(self, _messages):
            return SimpleNamespace(content="实行实名预约。[1]")
    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: Llm())
    result = research_answer.build_research_answer("如何预约", "北京", [
        {"source": "旧规则", "content": "无需预约。", "expires_at": "2020-01-01T00:00:00Z"},
        {"source": "当前资料", "content": "实行实名预约。"}])
    assert result["sources"][0]["source"] == result["evidence"][0]["source"] == "当前资料"
    assert result["evidence"][0]["citation_index"] == 1
    assert result["evidence"][1]["citation_index"] is None
