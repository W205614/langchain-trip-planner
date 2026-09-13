"""Failure-oriented regression tests using isolated SQL and real temporary Chroma."""
import asyncio
import json
import time
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base, TripTask, TripRecord, RagSyncJob, KnowledgeDocument, KnowledgeIngestJob, User
from app.core.exceptions import BizException
from app.models.schemas import TripRequest
from app.services import trip_tasks, history_service
from app.services.rag_sync import RagSyncWorker
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from test_trip_route import VALID_REQUEST, make_fake_trip_plan, make_complete_trip_plan
from test_rag_service import rag, published_document


@pytest.fixture
def sql(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "isolated.db"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(trip_tasks, "SessionLocal", factory)
    yield factory
    engine.dispose()


def test_rebuild_requires_admin(client, monkeypatch):
    from app.api.main import app
    from app.core.security import get_current_user
    assert client.post("/api/rag/rebuild").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: User(id=44, username="ordinary", is_admin=False)
    try:
        assert client.post("/api/rag/rebuild").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_probe_timeout_keeps_existing_vectors(rag):
    from langchain_core.documents import Document
    rag._knowledge_store.add_documents([Document(page_content="retained")])
    rag._embedding.embed_query = MagicMock(side_effect=TimeoutError())
    rag._ensure_collections_consistent()
    assert rag._knowledge_store._collection.count() == 1
    assert not rag.enabled


def test_rebuild_failure_keeps_active_generation(rag, monkeypatch):
    from langchain_core.documents import Document
    rag._knowledge_store.add_documents([Document(page_content="active")])
    old = rag._knowledge_store
    monkeypatch.setattr(rag, "_load_knowledge_documents", lambda: [Document(page_content="new")])
    rag._embedding.embed_documents = MagicMock(side_effect=TimeoutError())
    assert rag.build_knowledge_index()["success"] is False
    assert rag._knowledge_store is old
    assert old._collection.count() == 1


def test_disabled_rag_waits_then_recovers(sql, monkeypatch):
    with sql() as db:
        record = history_service.create_trip_record(db, 1, TripRequest(**VALID_REQUEST), make_fake_trip_plan())
    worker = RagSyncWorker(sql)
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: MagicMock(enabled=False))
    worker.run_once()
    with sql() as db:
        job = db.query(RagSyncJob).one()
        assert (job.status, job.attempts) == ("waiting", 0)
        job.next_retry_at = trip_tasks.now() - timedelta(seconds=1)
        db.commit()
    healthy = MagicMock(enabled=True)
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: healthy)
    worker.run_once()
    with sql() as db:
        assert db.query(RagSyncJob).one().status == "succeeded"


def test_durable_idempotency_and_owner_isolation(sql):
    body = TripRequest(**VALID_REQUEST)
    key = str(uuid4())
    task_id, cached = trip_tasks.submit(1, body, key)
    assert not cached
    assert trip_tasks.submit(1, body, key) == (task_id, True)
    with pytest.raises(BizException):
        trip_tasks.submit(1, body.model_copy(update={"city": "上海"}), key)
    with pytest.raises(BizException):
        trip_tasks.snapshot(2, task_id)


def test_restart_marks_running_interrupted_and_keeps_queue(sql, monkeypatch):
    first, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
    queued, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
    with sql() as db:
        db.get(TripTask, first).status = "running"
        db.commit()
    runner = trip_tasks.TripTaskRunner()
    monkeypatch.setattr(runner, "_loop", lambda: None)
    runner.start()
    runner.stop()
    assert trip_tasks.snapshot(1, first)["error_code"] == "PROCESS_INTERRUPTED"
    assert trip_tasks.snapshot(1, queued)["status"] == "queued"


def test_queue_deadline_is_terminal(sql):
    task_id, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
    with sql() as db:
        db.get(TripTask, task_id).deadline_at = trip_tasks.now() - timedelta(seconds=1)
        db.commit()
    trip_tasks.TripTaskRunner().tick()
    assert trip_tasks.snapshot(1, task_id)["error_code"] == "TASK_TIMEOUT"


@pytest.mark.parametrize("fail_save", [False, True])
def test_history_outbox_success_are_atomic(sql, monkeypatch, fail_save):
    task_id, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
    with sql() as db:
        db.get(TripTask, task_id).status = "running"
        db.commit()
    monkeypatch.setattr("app.agents.trip_planner_agent.get_trip_planner_agent",
                        lambda: MagicMock(plan_trip=MagicMock(return_value=make_complete_trip_plan())))
    monkeypatch.setattr("app.services.amap_service.get_amap_service", lambda: MagicMock())
    if fail_save:
        original = history_service.create_trip_record
        def failing(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("commit unavailable")
        monkeypatch.setattr(history_service, "create_trip_record", failing)
    assert trip_tasks.llm_request_gate.try_acquire()
    trip_tasks.TripTaskRunner().execute(task_id)
    state = trip_tasks.snapshot(1, task_id)
    with sql() as db:
        assert db.query(TripRecord).count() == (0 if fail_save else 1)
        assert db.query(RagSyncJob).count() == (0 if fail_save else 1)
    assert state["status"] == ("failed" if fail_save else "succeeded")


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


def test_edit_compare_and_swap_prevents_lost_update(sql):
    with sql() as db:
        record = history_service.create_trip_record(db, 1, TripRequest(**VALID_REQUEST), make_fake_trip_plan())
        history_service.update_trip_record(db, 1, record.id, make_fake_trip_plan(), expected_version=1)
        with pytest.raises(BizException):
            history_service.update_trip_record(db, 1, record.id, make_fake_trip_plan(), expected_version=1)
        assert db.get(TripRecord, record.id).version == 2


def test_rejected_publication_is_filtered_even_before_vector_cleanup(rag):
    from langchain_core.documents import Document
    from app.db.database import SessionLocal
    published_document(987)
    doc = Document(page_content="private now", metadata={"source_type": "multimodal", "document_id": 987})
    assert rag._visible_documents([doc]) == [doc]
    with SessionLocal() as db:
        db.get(KnowledgeDocument, 987).status = "deleted"
        db.commit()
    assert rag._visible_documents([doc]) == []


def test_reject_during_extraction_cannot_publish(sql, monkeypatch, tmp_path):
    from app.services import knowledge_ingest as ingest
    with sql() as db:
        record = KnowledgeDocument(submitted_by=1, city="北京", title="test", original_filename="x.jpg",
            stored_path="x.jpg", sha256="a"*64, media_type="image/jpeg", status="queued")
        db.add(record)
        db.commit()
        document_id = record.id
        monkeypatch.setattr(ingest, "_render_pages", lambda document: [(1, b"image", "image/jpeg")])
        def extraction(*args):
            with sql() as other:
                item = other.get(KnowledgeDocument, document_id)
                item.status, item.version = "rejected", item.version + 1
                other.commit()
            return ingest.VisionExtraction(summary="facts", facts=["提前预约"])
        rag_mock = MagicMock(enabled=True)
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag_mock)
        with pytest.raises(ingest.PublicationCancelled):
            ingest.process_document(db, record, MagicMock(extract=extraction))
        rag_mock.replace_public_knowledge_document.assert_not_called()


def test_queue_capacity_is_atomic_under_concurrent_submission(sql, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(trip_tasks.get_settings(), "trip_task_queue_limit", 1)
    def send(_):
        try:
            trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
            return 202
        except BizException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(send, range(4))) == [202, 429, 429, 429]


def test_knowledge_dependency_wait_does_not_spend_retry_or_call_vision(sql, monkeypatch):
    from app.services import knowledge_ingest as ingest
    with sql() as db:
        document = KnowledgeDocument(submitted_by=1, city="北京", title="test", original_filename="x.jpg",
            stored_path="x.jpg", sha256="a"*64, media_type="image/jpeg", status="queued")
        db.add(document)
        db.flush()
        db.add(KnowledgeIngestJob(document_id=document.id, document_version=1))
        db.commit()
    vision = MagicMock(side_effect=AssertionError("must not call paid vision"))
    monkeypatch.setattr(ingest, "VisionExtractor", vision)
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: MagicMock(enabled=False))
    assert ingest.KnowledgeIngestWorker(sql).run_once()
    with sql() as db:
        job = db.query(KnowledgeIngestJob).one()
        assert (job.status, job.attempts) == ("waiting", 0)
    vision.assert_not_called()


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
