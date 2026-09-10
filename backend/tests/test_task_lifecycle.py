import json
from unittest.mock import MagicMock
from uuid import uuid4
import pytest
from app.core.exceptions import BizException
from app.db.models import TripTask, TripRecord
from app.models.schemas import TripRequest
from app.services import trip_tasks, history_service
from test_reliability import sql
from test_trip_route import VALID_REQUEST, make_fake_trip_plan


def test_cancelled_task_never_commits_late_result(sql, monkeypatch):
    task, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
    with sql() as db:
        db.get(TripTask, task).status = "running"
        db.commit()
    def late(*args, **kwargs):
        trip_tasks.cancel(1, task)
        return make_fake_trip_plan()
    monkeypatch.setattr("app.agents.trip_planner_agent.get_trip_planner_agent", lambda: MagicMock(plan_trip=late))
    monkeypatch.setattr("app.services.amap_service.get_amap_service", lambda: MagicMock())
    assert trip_tasks.llm_request_gate.try_acquire()
    trip_tasks.TripTaskRunner().execute(task)
    assert trip_tasks.snapshot(1, task)["status"] == "cancelled"
    with sql() as db:
        assert db.query(TripRecord).count() == 0


def test_retry_has_new_identity_and_stable_idempotency(sql):
    task, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST))
    trip_tasks.cancel(1, task)
    retried, cached = trip_tasks.retry(1, task, "retry-1")
    assert retried != task and not cached
    assert trip_tasks.retry(1, task, "retry-1") == (retried, True)
    assert trip_tasks.list_tasks(2)["data"] == []
    with pytest.raises(BizException):
        trip_tasks.cancel(2, task)


def test_daily_quota_survives_scheduler_recreation(sql, monkeypatch):
    monkeypatch.setattr(trip_tasks.get_settings(), "trip_user_daily_limit", 1)
    task, _ = trip_tasks.submit(1, TripRequest(**VALID_REQUEST), "stable")
    trip_tasks.cancel(1, task)
    assert trip_tasks.submit(1, TripRequest(**VALID_REQUEST), "stable") == (task, True)
    with pytest.raises(BizException, match="额度"):
        trip_tasks.submit(1, TripRequest(**VALID_REQUEST))


@pytest.mark.parametrize("conflict", [False, True])
def test_revision_task_updates_original_atomically(sql, monkeypatch, conflict):
    body = TripRequest(**VALID_REQUEST)
    with sql() as db:
        record = history_service.create_trip_record(db, 1, body, make_fake_trip_plan())
        record_id = record.id
    task, _ = trip_tasks.submit(1, body, revision={"record_id": record_id, "version": 1, "day_index": 0, "instruction": "少走路"})
    with sql() as db:
        db.get(TripTask, task).status = "running"
        db.commit()
    def revised(request, plan, *args, **kwargs):
        if conflict:
            with sql() as db:
                history_service.update_trip_record(db, 1, record_id, make_fake_trip_plan(), 1)
        plan.overall_suggestions = "revised"
        return plan
    monkeypatch.setattr("app.agents.trip_planner_agent.get_trip_planner_agent", lambda: MagicMock(revise_trip_day=revised))
    monkeypatch.setattr("app.services.amap_service.get_amap_service", lambda: MagicMock())
    assert trip_tasks.llm_request_gate.try_acquire()
    trip_tasks.TripTaskRunner().execute(task)
    assert trip_tasks.snapshot(1, task)["status"] == ("failed" if conflict else "succeeded")
    with sql() as db:
        assert db.query(TripRecord).count() == 1
        assert (json.loads(db.get(TripRecord, record_id).plan_json)["overall_suggestions"] == "revised") is (not conflict)


def test_logout_revokes_existing_token_and_allows_new_login(client):
    account = {"username": "revoke_" + uuid4().hex[:12], "password": "test123456"}
    token = client.post('/api/auth/register', json=account).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.post('/api/auth/logout', headers=headers).status_code == 200
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    fresh = client.post('/api/auth/login', json=account).json()["access_token"]
    assert client.get('/api/auth/me', headers={"Authorization": "Bearer " + fresh}).status_code == 200
