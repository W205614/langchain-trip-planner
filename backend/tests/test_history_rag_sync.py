"""历史主数据与 RAG outbox 的最终一致性测试，不调用真实向量服务。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.db import database as dbmod
from app.db import models as models_mod
from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest
from app.services import history_service
from app.services.rag_sync import RagSyncWorker


def _request() -> TripRequest:
    return TripRequest(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店",
    )


def _plan(suggestion: str = "测试") -> TripPlan:
    return TripPlan(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", overall_suggestions=suggestion,
        days=[DayPlan(
            date="2026-08-01", day_index=0, description="测试", transportation="公共交通", accommodation="酒店",
            attractions=[Attraction(
                poi_id="B000A8UIN9", name="故宫博物院", address="景山前街4号",
                location=Location(longitude=116.397, latitude=39.918), visit_duration=120, description="测试",
            )], meals=[],
        )],
    )


def _database(tmp_path):
    engine = dbmod.create_engine(
        f"sqlite:///{(tmp_path / 'history_sync.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    models_mod.Base.metadata.create_all(bind=engine)
    return dbmod.sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _drain(worker: RagSyncWorker) -> None:
    while worker.run_once():
        pass


def test_create_update_delete_enqueue_jobs_and_worker_uses_latest_record(tmp_path, monkeypatch):
    session_factory = _database(tmp_path)
    db = session_factory()
    try:
        record = history_service.create_trip_record(db, 7, _request(), _plan())
        history_service.update_trip_record(db, 7, record.id, _plan("已编辑"))
        assert [job.operation for job in db.query(models_mod.RagSyncJob).all()] == ["upsert", "upsert"]

        fake_rag = MagicMock()
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: fake_rag)
        _drain(RagSyncWorker(session_factory))
        # 两个 upsert 都读取主表的最终版本，因此不会把旧 JSON 写回 Chroma。
        assert fake_rag.add_history_plan.call_count == 2
        assert fake_rag.add_history_plan.call_args.args[3].overall_suggestions == "已编辑"

        assert history_service.delete_trip_record(db, 7, record.id) is True
        _drain(RagSyncWorker(session_factory))
        fake_rag.delete_history_plan.assert_any_call(record.id, 7)
        assert {job.status for job in db.query(models_mod.RagSyncJob).all()} == {"succeeded"}
    finally:
        db.close()


def test_failed_job_retries_and_new_worker_recovers_running_job(tmp_path, monkeypatch):
    session_factory = _database(tmp_path)
    db = session_factory()
    try:
        record = history_service.create_trip_record(db, 8, _request(), _plan())
        failing_rag = MagicMock()
        failing_rag.add_history_plan.side_effect = RuntimeError("temporary embedding error")
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: failing_rag)
        worker = RagSyncWorker(session_factory)
        assert worker.run_once() is True
        job = db.query(models_mod.RagSyncJob).one()
        db.refresh(job)
        assert job.status == "retry"
        assert job.attempts == 1

        # 模拟进程在任务标记为 running 后退出；新 worker 应能继续处理。
        job.status = "running"
        job.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
        succeeding_rag = MagicMock()
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: succeeding_rag)
        assert RagSyncWorker(session_factory).run_once() is True
        db.refresh(job)
        assert job.status == "succeeded"
        succeeding_rag.add_history_plan.assert_called_once()
        assert succeeding_rag.add_history_plan.call_args.args[:2] == (record.id, 8)
    finally:
        db.close()


def test_outbox_keeps_user_ids_isolated(tmp_path, monkeypatch):
    session_factory = _database(tmp_path)
    db = session_factory()
    try:
        first = history_service.create_trip_record(db, 11, _request(), _plan())
        second = history_service.create_trip_record(db, 12, _request(), _plan())
        fake_rag = MagicMock()
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: fake_rag)
        _drain(RagSyncWorker(session_factory))

        calls = [call.args[:2] for call in fake_rag.add_history_plan.call_args_list]
        assert (first.id, 11) in calls
        assert (second.id, 12) in calls
        assert len(calls) == 2
    finally:
        db.close()


def test_outbox_retries_when_rag_reports_unsuccessful_write(tmp_path, monkeypatch):
    session_factory = _database(tmp_path)
    db = session_factory()
    try:
        history_service.create_trip_record(db, 13, _request(), _plan())
        rag = MagicMock()
        rag.enabled = True
        rag.delete_history_plan.return_value = True
        rag.add_history_plan.return_value = False
        monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag)

        RagSyncWorker(session_factory).run_once()

        job = db.query(models_mod.RagSyncJob).one()
        db.refresh(job)
        assert job.status == "retry"
        assert "returned false" in job.last_error
    finally:
        db.close()
