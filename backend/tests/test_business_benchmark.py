"""HTTP benchmark must terminate on drafts without counting them as successes."""
import json

import httpx
import pytest

from scripts import business_benchmark


@pytest.mark.parametrize("status", ["succeeded", "needs_attention", "failed", "cancelled"])
def test_benchmark_handles_all_terminal_states(tmp_path, monkeypatch, status):
    cases = tmp_path / "evals" / "business_cases.json"
    cases.parent.mkdir()
    cases.write_text(json.dumps([{"id": "terminal", "request": {}, "expected": status}]))
    monkeypatch.setattr(business_benchmark, "__file__", str(tmp_path / "scripts" / "business_benchmark.py"))
    polls = []

    def respond(request):
        path = request.url.path
        if path == "/api/validation/fixture":
            return httpx.Response(200, json={"offline_fixture": True})
        if path == "/api/auth/register":
            return httpx.Response(201, json={"access_token": "fixture"})
        if path == "/api/trip/tasks":
            return httpx.Response(202, json={"data": {"id": "task-1"}})
        if path == "/api/trip/tasks/task-1":
            polls.append(path)
            assert len(polls) == 1, "Terminal tasks must not keep polling"
            return httpx.Response(200, json={"data": {"status": status}})
        raise AssertionError(path)

    client = httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(respond))
    monkeypatch.setattr(business_benchmark.httpx, "Client", lambda **kwargs: client)
    output = tmp_path / "report.json"
    business_benchmark.run("http://fixture", output)
    report = json.loads(output.read_text())
    assert report["contract_passed"] == 1
    assert report["summary"]["successful_generations"] == int(status == "succeeded")
    assert report["summary"]["draft_generations"] == int(status == "needs_attention")
