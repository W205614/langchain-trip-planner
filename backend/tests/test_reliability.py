"""Failure-oriented regression tests using isolated SQL and real temporary Chroma."""
import asyncio
import json
import time
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.core.exceptions import BizException
from app.models.schemas import TripRequest
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from planning_fixtures import VALID_REQUEST, make_fake_trip_plan, make_complete_trip_plan
from test_rag_service import rag, published_document






def test_probe_timeout_keeps_existing_vectors(rag, monkeypatch):
    from langchain_core.documents import Document
    rag._knowledge_store.add_documents([Document(page_content="retained")])
    with monkeypatch.context() as probe:
        probe.setattr(rag._embedding, "embed_query", MagicMock(side_effect=TimeoutError()))
        rag._ensure_collections_consistent()
    assert rag._knowledge_store._collection.count() == 1
    # A transient probe failure must not latch an outage until process restart.
    assert rag.enabled
    recovered = rag._knowledge_store.similarity_search_by_vector(
        rag._embedding.embed_query("retained"), k=1,
    )
    assert [doc.page_content for doc in recovered] == ["retained"]


def test_rebuild_failure_keeps_active_generation(rag, monkeypatch):
    from langchain_core.documents import Document
    rag._knowledge_store.add_documents([Document(page_content="active")])
    old = rag._knowledge_store
    monkeypatch.setattr(rag, "_load_knowledge_documents", lambda: [Document(page_content="new")])
    rag._embedding.embed_documents = MagicMock(side_effect=TimeoutError())
    assert rag.build_knowledge_index()["success"] is False
    assert rag._knowledge_store is old
    assert old._collection.count() == 1












def test_slow_continuous_stream_is_cancelled_at_total_deadline():
    from langchain_core.messages import AIMessageChunk
    closed = []
    class SlowStream:
        async def astream(self, payload):
            try:
                while True:
                    await asyncio.sleep(0.01)
                    yield AIMessageChunk(content="x")
            finally:
                closed.append(True)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        MultiAgentTripPlanner._stream_day_response(SlowStream(), {}, lambda: None, timeout=0.05)
    assert time.monotonic() - started < 1
    assert closed == [True]




def test_rejected_publication_is_filtered_even_before_vector_cleanup(rag):
    from langchain_core.documents import Document
    from test_rag_service import _published
    published_document(987)
    doc = Document(page_content="private now", metadata={"source_type": "multimodal", "document_id": 987})
    assert rag._visible_documents([doc]) == [doc]
    _published[987]["status"] = "deleted"
    assert rag._visible_documents([doc]) == []








def test_live_evaluation_gate_limits_calls_and_requires_prices(monkeypatch):
    from app.services import eval_budget
    from types import SimpleNamespace
    cfg = SimpleNamespace(live_eval_enabled=True, live_eval_max_calls=1, live_eval_max_cost_usd=1,
        llm_input_price_per_million_usd=0, llm_output_price_per_million_usd=1)
    monkeypatch.setattr(eval_budget, "get_settings", lambda: cfg)
    monkeypatch.setattr(eval_budget, "_calls", 0)
    monkeypatch.setattr(eval_budget, "_reserved", 0)
    with pytest.raises(RuntimeError):
        eval_budget.reserve("hello", 100)
    cfg.llm_input_price_per_million_usd = 1
    eval_budget.reserve("hello", 100)
    with pytest.raises(RuntimeError):
        eval_budget.reserve("hello", 100)


def test_zero_budget_is_preserved_and_unknown_survives_round_trip():
    from app.models.schemas import TripPlan
    from app.services.plan_quality import recalculate_budget
    plan = make_fake_trip_plan()
    for day in plan.days:
        for attraction in day.attractions:
            attraction.ticket_price = 0
            attraction.model_fields_set.discard("ticket_price")
        for meal in day.meals:
            meal.estimated_cost = 0
        if day.hotel:
            day.hotel.estimated_cost = 0
    recalculate_budget(plan)
    assert plan.budget.total == 0
    restored = TripPlan.model_validate_json(plan.model_dump_json())
    recalculate_budget(restored)
    assert "attraction_prices" in restored.budget.unknown_items
