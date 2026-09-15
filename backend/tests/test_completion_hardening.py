"""Fault injection against completion, publication and RAG entity boundaries."""
import json
from datetime import timedelta
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from app.core.exceptions import BizException
from app.models.schemas import TripRequest, Location
from app.services.knowledge_chunks import entity_excerpt, complete_excerpt, sections
from app.services.attraction_names import valid_attraction, resolve_name
from app.services.planning_constraints import finalize_plan
from app.services.rag_runtime import index_lock, deadline, slots, operation
from test_rag_service import rag
from planning_fixtures import VALID_REQUEST, make_fake_trip_plan, make_complete_trip_plan








def test_rule_fallback_can_finish_when_constraints_hold():
    plan = make_complete_trip_plan()
    plan.days[0].generation_mode = "fallback"
    report = finalize_plan(plan, TripRequest(**VALID_REQUEST))
    assert report["outcome"] == "degraded"
    assert any(i["code"] == "RULE_FALLBACK" for i in report["issues"])
    assert not any(i["blocking"] for i in report["issues"])


def test_fixed_product_boundaries_are_not_counted_as_runtime_outage():
    report = finalize_plan(make_complete_trip_plan(), TripRequest(**VALID_REQUEST))
    assert report["outcome"] == "complete"
    assert not report["facts_complete"]
    assert "reservation_unverified" in report["data_gaps"]


def test_missing_route_cannot_pass_user_walking_limit():
    body = TripRequest(**(VALID_REQUEST | {"constraints": {"max_inter_stop_walking_km": 1}}))
    plan = make_complete_trip_plan()
    plan.days[0].attractions.append(plan.days[1].attractions.pop())
    report = finalize_plan(plan, body, MagicMock(plan_route_by_locations=MagicMock(side_effect=TimeoutError())))
    assert report["outcome"] == "draft"
    assert {i["code"] for i in report["issues"]} >= {"WALKING_LIMIT_UNVERIFIED", "TIME_LIMIT_UNVERIFIED"}
    assert report["day_checks"][0]["route_minutes"] is None




@pytest.mark.parametrize("body", ["### 天坛\n门票60元", "故宫附近的天坛门票60元", "### 故宫售票处\n无需预约"])
def test_same_city_wrong_entity_never_enriches_attraction(body):
    assert entity_excerpt(Document(page_content=body, metadata={}), "故宫", "北京", 320) == ""


def test_sections_keep_ownership_and_poi_id_precedence():
    text = "## 北京\n### 故宫\n需要预约\n### 天坛\n开放时间未知"
    doc = Document(page_content=text, metadata={})
    assert "天坛" not in entity_excerpt(doc, "故宫博物院", "北京", 320)
    assert "需要预约" in entity_excerpt(doc, "故宫博物院", "北京", 320)
    doc.metadata = {"poi_id": "wrong"}
    assert entity_excerpt(doc, "故宫", "北京", 320, "expected") == ""


def test_excerpt_does_not_cut_off_negation_or_table_conditions():
    text = "| 景点 | 预约 |\n| 故宫 | 无预约不得进入，请核对日期 |"
    assert complete_excerpt(text, 20) == ""
    assert complete_excerpt(text, 100) == text


@pytest.mark.parametrize("name,lon,lat", [("故宫售票处", 116,39), ("故宫",0,0), ("故宫",181,39), ("故宫",float('nan'),39)])
def test_candidate_gate_rejects_bad_identity_or_coordinates(name, lon, lat):
    p = SimpleNamespace(id="poi", name=name, type="", location=Location(longitude=lon, latitude=lat))
    assert not valid_attraction(p)


def test_ambiguous_alias_is_rejected():
    candidates = [SimpleNamespace(id=str(i), name=n) for i,n in enumerate(["博物馆(东馆)", "博物馆(西馆)"])]
    assert resolve_name("博物馆", candidates, "北京") is None


def test_cross_city_candidate_is_rejected_when_upstream_reports_city():
    p = SimpleNamespace(id="poi", name="博物馆", type="", city="上海市", location=Location(longitude=121, latitude=31))
    assert not valid_attraction(p, "北京")
    assert valid_attraction(p, "上海")






def test_slow_embedding_does_not_hold_index_lock(rag):
    entered, release, finished = Event(), Event(), Event()
    original = rag._embedding.embed_documents
    def slow(texts):
        entered.set()
        assert release.wait(3)
        return original(texts)
    rag._embedding.embed_documents = slow
    errors = []
    def write():
        try:
            rag._write_documents("_knowledge_store", [Document(page_content="facts",metadata={"city":"北京"})])
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    thread = Thread(target=write)
    thread.start()
    try:
        assert entered.wait(2)
        with index_lock():
            assert not finished.is_set()
    finally:
        release.set(); thread.join(5)
    assert not errors and finished.is_set()


def test_embedding_failure_preserves_existing_document(rag):
    rag._write_documents("_knowledge_store", [Document(page_content="old",metadata={"document_id":99})], ids=["old"])
    rag._embedding.embed_documents = MagicMock(side_effect=TimeoutError())
    assert not rag.replace_public_knowledge_document(99, "北京", "test", ["new"])
    assert rag._knowledge_store.get(ids=["old"])["documents"] == ["old"]


def test_rag_capacity_is_bounded():
    assert slots.acquire(False) and slots.acquire(False)
    try:
        with pytest.raises(TimeoutError):
            with operation():
                pytest.fail("unbounded optional work")
    finally:
        slots.release(); slots.release()


def test_dripping_embedding_response_has_total_deadline(monkeypatch):
    import asyncio
    import httpx
    from app.services import rag_service
    closed = Event()
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "
        async def aclose(self):
            closed.set()
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, stream=Drip())
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=Transport(), **kw))
    monkeypatch.setattr(rag_service, "budget", lambda: 0.08)
    embedding = rag_service._OpenAICompatEmbeddings("test", "https://example.invalid/v1", "test")
    with pytest.raises(TimeoutError):
        embedding.embed_query("测试")
    assert closed.is_set()
