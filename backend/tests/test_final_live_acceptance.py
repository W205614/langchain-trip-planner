"""Offline crash-window checks for the bounded live sampler; no Docker or HTTP I/O."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def sampler(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("live_sampler_under_test", scripts / "final_live_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path / "repository")
    def inspect(name):
        if name.endswith("backend-1"):
            return {"Config": {"Env": ["FINAL_ACCEPTANCE_RUN_ID=test-run", "JDBC_DATABASE_URL=jdbc:postgresql://postgres:5432/trip_business"]}}
        return {"Id": "offline-db", "Config": {"Env": ["POSTGRES_DB=bootstrap", "POSTGRES_USER=trip"]}}
    monkeypatch.setattr(module, "inspect", inspect)
    def sql(container, database, query, user):
        assert container == "offline-db" and database == "trip_business"
        assert "acceptance:test-run" in query
        return "text:0\nembedding:0\namap:0"
    monkeypatch.setattr(module, "sql", sql)
    return module


def record(identity=7, version=1):
    return {"id": identity, "version": version, "quality": {"outcome": "degraded"}, "plan": {"days": [
        {"city": "北京", "attractions": [{"poi_id": f"poi-{i}", "activity_id": f"a-{i}", "name": "景点"}],
         "timeline": [{"activity_id": f"a-{i}", "kind": "attraction"}], "feasibility": {"status": "unverified"}}
        for i in range(2)]}}


class Response:
    def __init__(self, status, data):
        self.status_code, self.data = status, data
        self.content, self.text = b"{}", "offline response"
    def json(self):
        return deepcopy(self.data)
    def raise_for_status(self):
        assert self.status_code < 400


def setup(monkeypatch, sampler, tmp_path, stage, state, handler):
    private = tmp_path / "private"
    private.mkdir()
    state = {"run_id": "test-run", "username": "offline-user", "password": "offline-only", **state}
    (private / "state.json").write_text(json.dumps(state), encoding="utf-8")
    calls = []
    class Client:
        def __init__(self, **kwargs):
            self.headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def request(self, method, path, **kwargs):
            calls.append((method, path, deepcopy(kwargs)))
            if path == "/api/validation/fixture":
                return Response(404, {})
            if path == "/api/auth/login":
                return Response(200, {"access_token": "offline-token"})
            return handler(method, path, kwargs)
        def get(self, path, **kwargs):
            return self.request("GET", path, **kwargs)
        def post(self, path, **kwargs):
            return self.request("POST", path, **kwargs)
    monkeypatch.setattr(sampler.httpx, "Client", Client)
    args = SimpleNamespace(private_dir=private, base_url="http://127.0.0.1:8380", run_id="test-run",
                           output=tmp_path / "report.json", stage=stage)
    return args, calls


def test_generation_replays_saved_request_and_key_after_lost_submit_response(monkeypatch, sampler, tmp_path):
    saved = {"city": "北京", "start_date": "2020-01-01", "end_date": "2020-01-02", "travel_days": 2}
    def handler(method, path, kwargs):
        if path == "/api/trip/tasks":
            assert method == "POST" and kwargs["json"] == saved
            assert kwargs["headers"]["Idempotency-Key"] == "original-key"
            return Response(202, {"data": {"id": "existing-task"}})
        if path == "/api/trip/tasks/existing-task":
            return Response(200, {"data": {"status": "succeeded", "result": {"id": 7}}})
        if path == "/api/history/7":
            return Response(200, {"data": record()})
        raise AssertionError(f"Unexpected call: {method} {path}")
    args, calls = setup(monkeypatch, sampler, tmp_path, "plan", {"plan_request": saved, "plan_key": "original-key"}, handler)
    assert sampler.run(args)["passed"]
    persisted = json.loads((args.private_dir / "state.json").read_text(encoding="utf-8"))
    assert persisted["plan_request"] == saved and persisted["plan_task"] == "existing-task"
    assert sum(path == "/api/trip/tasks" for _, path, _ in calls) == 1


def test_unknown_research_response_is_not_implicitly_sent_again(monkeypatch, sampler, tmp_path):
    def handler(method, path, kwargs):
        raise AssertionError(f"Unexpected paid call: {method} {path}")
    args, calls = setup(monkeypatch, sampler, tmp_path, "research", {"research_attempted": True}, handler)
    with pytest.raises(RuntimeError, match="Research response is unknown"):
        sampler.run(args)
    assert not any(path == "/api/research" for _, path, _ in calls)
    assert json.loads(args.output.read_text(encoding="utf-8"))["passed"] is False


def test_confirmation_recovers_server_success_before_local_flag_saved(monkeypatch, sampler, tmp_path):
    before = record()
    preview = deepcopy(before["plan"])
    preview["days"][1]["description"] = "已确认的新安排"
    proposed = {"id": 8, "version": 2, "plan": preview,
                "quality": {"validated_outcome": "degraded", "assistant_proposal_status": "confirmed", "applied_to": 7}}
    def handler(method, path, kwargs):
        if path == "/api/trip/tasks/proposal-task":
            return Response(200, {"data": {"status": "needs_attention", "result": {"id": 8}}})
        if path == "/api/history/8":
            return Response(200, {"data": proposed})
        if path == "/api/history/7":
            return Response(200, {"data": {"id": 7, "version": 2, "plan": preview}})
        if path.endswith("/proposals/8/confirm"):
            return Response(409, {"success": False, "code": "VERSION_CONFLICT"})
        raise AssertionError(f"Unexpected setup or paid call: {method} {path}")
    state = {"plan_trip": 7, "proposal_task": "proposal-task", "proposal_before": before,
             "proposal_preview": preview, "proposal_version": 1, "conversation": "conversation"}
    args, calls = setup(monkeypatch, sampler, tmp_path, "proposal", state, handler)
    assert sampler.run(args)["passed"]
    persisted = json.loads((args.private_dir / "state.json").read_text(encoding="utf-8"))
    assert persisted["confirmed"] is True
    assert len([path for method, path, _ in calls if method == "POST"]) == 2  # login and negative duplicate confirm
