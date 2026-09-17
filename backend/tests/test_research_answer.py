from types import SimpleNamespace

from app.services import research_answer


def test_research_answer_returns_direct_grounded_text_and_sources(monkeypatch):
    class Llm:
        def invoke(self, _messages):
            return SimpleNamespace(content="故宫通常需要提前预约，具体规则请以官方渠道为准。[1]")

    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: Llm())
    result = research_answer.build_research_answer(
        "故宫如何预约？",
        "北京",
        [{"content": "故宫实行预约参观。", "source": "北京旅行资料", "page": 2, "source_tier": "curated_static"}],
    )

    assert result["answer"].startswith("故宫通常需要")
    assert result["answer_status"] == "grounded_answer"
    assert result["sources"] == [
        {"index": 1, "source": "北京旅行资料", "page": 2, "source_tier": "curated_static"}
    ]


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


def test_stream_research_answer_emits_tokens_then_terminal_result(monkeypatch):
    class Llm:
        def stream(self, _messages):
            yield SimpleNamespace(content="提前预约")
            yield SimpleNamespace(content="。[1]")

    monkeypatch.setattr(research_answer, "get_llm", lambda timeout: Llm())
    events = list(research_answer.stream_research_answer(
        "如何预约？", "北京", [{"content": "实行预约。", "source": "资料"}]
    ))

    assert [event for event, _ in events] == ["token", "token", "result"]
    assert events[-1][1]["answer"] == "提前预约。[1]"
