"""The internal execution boundary must never expose business endpoints."""
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from app.agent_api import main
from app.services.execution import cancellation_var, remaining
import threading


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "internal-test-key-01234567890123456789")
    monkeypatch.setenv("BUSINESS_URL", "http://business-test:9000")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    with TestClient(main.app) as client:
        yield client


def test_service_auth_and_public_routes(client):
    assert client.post("/internal/v1/executions", json={}).status_code == 401
    assert client.get("/api/history").status_code == 404
    response = client.get("/readyz", headers={"X-Request-ID": "trace-test"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-test"


def test_lifespan_initializes_application_logging(monkeypatch, tmp_path):
    calls = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "internal-test-key-01234567890123456789")
    monkeypatch.setenv("BUSINESS_URL", "http://business-test:9000")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "setup_logging", lambda: calls.append("configured"))

    with TestClient(main.app) as test_client:
        assert test_client.get("/readyz").status_code == 200

    assert calls == ["configured"]


def test_cancel_unknown_is_idempotent(client):
    headers={"X-Service-Key": "internal-test-key-01234567890123456789"}
    for _ in range(2):
        assert client.post(f"/internal/v1/executions/{uuid.uuid4()}/cancel", headers=headers).status_code == 200


def test_only_agent_owned_mcp_poi_capabilities_are_exposed(client, monkeypatch):
    from types import SimpleNamespace
    from app.services import amap_service
    poi = {"id":"B0001","name":"故宫博物院","type":"博物馆","address":"景山前街4号",
           "city":"北京","location":{"longitude":116.4,"latitude":39.9},"photos":["https://store.is.autonavi.com/a.jpg"],"opening_hours":"08:30-17:00"}
    fake = SimpleNamespace(
        search_poi=lambda *args: [SimpleNamespace(model_dump=lambda: poi)],
        get_poi_detail=lambda poi_id: {**poi, "id": poi_id, "cityname":"北京", "photos":[{"url":poi["photos"][0]}]},
    )
    monkeypatch.setattr(amap_service, "get_amap_service", lambda: fake)
    headers={"X-Service-Key": "internal-test-key-01234567890123456789"}
    search = client.post("/internal/v1/capabilities/poi-search", json={"keywords":"故宫","city":"北京"}, headers=headers)
    assert search.status_code == 200 and search.json()["data"][0]["id"] == "B0001"
    detail = client.post("/internal/v1/capabilities/poi-detail", json={"poi_id":"B0001"}, headers=headers)
    assert detail.status_code == 200 and detail.json()["data"]["photos"] == poi["photos"]
    assert client.post("/internal/v1/capabilities/photo-image", json={"name": "故宫"}, headers=headers).status_code == 404
    assert client.post("/internal/v1/capabilities/route", json={}, headers=headers).status_code == 404
    assert client.post("/internal/v1/capabilities/weather", json={}, headers=headers).status_code == 404


def test_research_disabled_preserves_business_error_code(client, monkeypatch):
    from types import SimpleNamespace
    from app.services import rag_service
    monkeypatch.setattr(rag_service,"get_rag_service",lambda:SimpleNamespace(enabled=False))
    response=client.post("/internal/v1/capabilities/research",json={"city":"北京","query":"故宫资料"},
        headers={"X-Service-Key":"internal-test-key-01234567890123456789"})
    assert response.status_code==503 and response.json()["code"]=="RAG_DISABLED"


def test_research_stream_emits_progress_tokens_and_result(client, monkeypatch):
    from types import SimpleNamespace
    from app.services import rag_service, research_answer

    fake_rag = SimpleNamespace(
        enabled=True,
        retrieve_research_evidence=lambda *args, **kwargs: [{"content": "实行预约。", "source": "资料"}],
    )
    monkeypatch.setattr(rag_service, "get_rag_service", lambda: fake_rag)
    monkeypatch.setattr(
        research_answer,
        "stream_research_answer",
        lambda *args: iter([
            ("token", {"delta": "提前预约"}),
            ("result", {"answer": "提前预约", "sources": []}),
        ]),
    )
    response = client.post(
        "/internal/v1/capabilities/research/stream",
        json={"city": "北京", "query": "故宫如何预约？"},
        headers={"X-Service-Key": "internal-test-key-01234567890123456789"},
    )

    assert response.status_code == 200
    assert "event: progress" in response.text
    assert "event: token" in response.text
    assert "event: result" in response.text


def test_cancellation_propagates_without_deadline():
    event=threading.Event();event.set();token=cancellation_var.set(event)
    try:
        with pytest.raises(TimeoutError, match="cancelled"):
            remaining()
    finally:
        cancellation_var.reset(token)


def test_expired_execution_never_registers(client):
    identity=str(uuid.uuid4())
    result=client.post("/internal/v1/executions",headers={"X-Service-Key":"internal-test-key-01234567890123456789"},json={
        "task_id":str(uuid.uuid4()),"execution_id":identity,"request_id":"test","user_id":1,
        "deadline_at":(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
        "request":{"city":"北京","start_date":"2026-10-01","end_date":"2026-10-01","travel_days":1,"transportation":"步行","accommodation":"经济"}})
    assert result.status_code == 409
    assert identity not in main._executions


def test_duplicate_execution_never_calls_planner_twice(client, monkeypatch):
    from types import SimpleNamespace
    from app.agents import trip_planner_agent
    from app.services import planning_constraints
    calls=[]
    def plan(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(enrichment_notices=[], model_dump=lambda: {"days": []})
    route_planner = object()
    validation_routes = []
    monkeypatch.setattr(
        trip_planner_agent,
        "get_trip_planner_agent",
        lambda: SimpleNamespace(plan_trip=plan, amap_service=route_planner),
    )
    def finalize(*args, **kwargs):
        validation_routes.append(args[2])
        return {"outcome":"complete","issues":[]}
    monkeypatch.setattr(planning_constraints,"finalize_plan",finalize)
    body={"protocol_version":1,"task_id":str(uuid.uuid4()),"execution_id":str(uuid.uuid4()),"request_id":"duplicate",
        "user_id":1,"deadline_at":(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat(),
        "request":{"city":"北京","start_date":"2026-10-01","end_date":"2026-10-01","travel_days":1,"transportation":"步行","accommodation":"经济"}}
    headers={"X-Service-Key":"internal-test-key-01234567890123456789"}
    response=client.post("/internal/v1/executions",json=body,headers=headers)
    assert response.status_code==200 and "event: result" in response.text
    assert '"outcome": "complete"' in response.text
    assert '"agent_route_and_constraints_ms"' in response.text
    assert client.post("/internal/v1/executions",json=body,headers=headers).status_code==409
    assert calls==[1]
    assert validation_routes == [route_planner]
