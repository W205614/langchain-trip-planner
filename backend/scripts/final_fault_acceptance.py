"""Exercise real HTTP lifecycle/authorization against guarded offline fixtures only.

Provider responses are substitutes. PostgreSQL is used only for fixture administrator
setup and independent state assertions; it never fabricates successful task results.
"""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import UUID, uuid4

import httpx


def run(args):
    report = {"mode": "isolated_final_faults", "started_at": datetime.now(timezone.utc).isoformat(),
              "passed": False, "checks": [], "not_executed": [], "provider_calls": 0,
              "boundary": "Real HTTP, database, permissions and lifecycle; offline model/map only.",
              "scope_limits": ["Does not inject live-provider 429s or assess real travel fact accuracy."]}
    clients = []

    def check(label, value):
        if not value:
            raise AssertionError(label)
        report["checks"].append({"name": label, "passed": True})

    def request(client, method, path, expected=(200,), **kwargs):
        result = client.request(method, path, **kwargs)
        if result.status_code not in expected:
            raise AssertionError(f"{method} {path}: HTTP {result.status_code}: {result.text[:500]}")
        return result.json() if result.content else {}

    def client(base):
        c = httpx.Client(base_url=base, timeout=35, trust_env=False)
        clients.append(c)
        return c

    def fixture_container(name):
        if not name.startswith("trip-validation-"):
            raise RuntimeError("Refusing container outside trip-validation")
        label = subprocess.run(["docker", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', name],
                               check=True, capture_output=True, text=True).stdout.strip()
        if label != "trip-validation":
            raise RuntimeError("Container is not owned by trip-validation")

    def sql(statement):
        return subprocess.run(["docker", "exec", args.database_container, "psql", "-v", "ON_ERROR_STOP=1", "-U", "trip", "-d", "trip", "-t", "-A", "-c", statement],
                              check=True, capture_output=True, text=True).stdout.strip()

    def poll(owner, identity, statuses, seconds=90):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                state = request(owner, "GET", f"/api/trip/tasks/{identity}")["data"]
            except (httpx.TransportError, AssertionError):
                time.sleep(.2)
                continue
            if state["status"] in statuses:
                return state
            if state["status"] in {"succeeded", "needs_attention", "failed", "cancelled"}:
                raise AssertionError(f"Unexpected task terminal: {state.get('status')} {state.get('error_code')}")
            time.sleep(.1)
        raise AssertionError(f"Task did not reach {sorted(statuses)}")

    def submit(owner, slow=False):
        body = {"city": "北京", "start_date": "2026-11-01", "end_date": "2026-11-01", "travel_days": 1,
                "transportation": "公共交通", "accommodation": "酒店", "planning_input": {},
                "free_text_input": "fixture:slow" if slow else ""}
        return request(owner, "POST", "/api/trip/tasks", (202,), json=body,
                       headers={"Idempotency-Key": str(uuid4())})["data"]["id"]

    def checkpoint(internal, execution, stage, expected=(200,)):
        return request(internal, "POST", f"/internal/v2/executions/{execution}/checkpoints", expected,
                       json={"stage": stage, "input_hash": "a" * 64, "payload": {"fixture_probe": True},
                             "compatibility": {"probe": "fault-v1"},
                             "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})

    try:
        anonymous = client(args.base_url)
        marker = request(anonymous, "GET", "/api/validation/fixture")
        check("offline fixture marker verified before mutations", marker.get("offline_fixture") is True)
        fixture_container(args.database_container)
        service_key = os.environ.get(args.service_key_env, "")
        if len(service_key.encode()) < 32:
            raise RuntimeError("Internal service key environment variable is required; value is never written to report")
        internal = client(args.business_url)
        internal.headers["X-Service-Key"] = service_key
        check("direct business endpoint also has offline fixture marker",
              request(internal, "GET", "/api/validation/fixture").get("offline_fixture") is True)
        prefix = "fault_" + uuid4().hex[:8]
        owner, admin = client(args.base_url), client(args.base_url)
        for role, c in (("owner", owner), ("admin", admin)):
            registered = request(c, "POST", "/api/auth/register", json={"username": f"{prefix}_{role}", "password": "isolated-fault123"})
            c.headers["Authorization"] = "Bearer " + registered["access_token"]
        # No production/public account elevation endpoint is added for testing.
        sql(f"UPDATE users SET is_admin=true WHERE username='{prefix}_admin'")
        me = request(admin, "GET", "/api/auth/me")
        check("administrator is real persisted role", me.get("is_admin", me.get("data", {}).get("is_admin")) is True)

        original = submit(owner, slow=True)
        running = poll(owner, original, {"running"}, 20)
        execution = running.get("execution_id")
        check("owner can identify running execution", bool(execution))
        checkpoint(internal, execution, "fault_probe")
        journal = request(internal, "GET", f"/internal/v2/executions/{execution}/checkpoints")
        check("checkpoint persisted before cancellation", any(x["stage"] == "fault_probe" for x in journal["checkpoints"]))
        request(owner, "POST", f"/api/trip/tasks/{original}/cancel")
        cancelled = poll(owner, original, {"cancelled"})
        check("cancel is durable terminal", cancelled["status"] == "cancelled")
        checkpoint(internal, execution, "late_after_cancel", (409,))
        check("cancelled execution cannot write checkpoint", True)
        time.sleep(args.slow_seconds + 3)
        persisted = sql(f"SELECT status||':'||COALESCE(record_id::text,'NULL') FROM trip_tasks WHERE id='{UUID(original)}'")
        check("late provider completion did not revive cancelled task or save result", persisted == "cancelled:NULL")

        key = str(uuid4())
        resumed = request(owner, "POST", f"/api/tasks/{original}/resume", (200, 202), headers={"Idempotency-Key": key})["data"]["id"]
        same = request(owner, "POST", f"/api/tasks/{original}/resume", (200, 202), headers={"Idempotency-Key": key})["data"]["id"]
        check("resume key creates exactly one new linked task", resumed == same and resumed != original)
        new_running = poll(owner, resumed, {"running"}, 20)
        check("new execution differs from cancelled one", new_running.get("execution_id") != execution)
        checkpoint(internal, execution, "late_after_resume", (409,))
        check("old execution cannot write after new execution starts", True)
        result = poll(owner, resumed, {"succeeded", "needs_attention"})
        check("resume succeeds while original remains terminal", request(owner, "GET", f"/api/trip/tasks/{original}")["data"]["status"] == "cancelled")
        check("new task keeps recovery ancestry", result.get("resumed_from") == original)
        trip = result["result"]["id"]

        request(admin, "GET", "/api/knowledge/admin/submissions")
        check("administrator governance permission works", True)
        for method, path, body in (("GET", f"/api/trips/{trip}/workspace", None),
                                  ("GET", f"/api/trips/{trip}/planning-input", None),
                                  ("PUT", f"/api/trips/{trip}/planning-input", {"planning_input": {}, "planning_revision": 1}),
                                  ("POST", f"/api/assistant/conversations", {"active_trip_id": trip})):
            request(admin, method, path, (403, 404), json=body, headers={"If-Match": "1"})
        check("administrator cannot bypass private trip owner checks", True)

        planning = request(owner, "GET", f"/api/trips/{trip}/planning-input")["data"]
        proposed_input = copy.deepcopy(planning["planning_input"])
        expiry = datetime.now(timezone.utc) + timedelta(seconds=12)
        proposed_input["opening_windows"] = [{"poi_id": a["poi_id"], "date": d["date"],
            "windows": [{"start": "08:00", "end": "22:00"}], "source": "offline-fixture-official",
            "observed_at": datetime.now(timezone.utc).isoformat(), "expires_at": expiry.isoformat(), "confirmed": True}
            for d in planning["plan"]["days"] for a in d["attractions"]]
        current = request(owner, "PUT", f"/api/trips/{trip}/planning-input",
                          json={"planning_input": proposed_input, "planning_revision": planning["planning_revision"]},
                          headers={"If-Match": str(planning["trip_version"])})["data"]
        # The previous slow marker belongs to the old task input, not this new trip's stored free text.
        # Remove it via guarded fixture SQL only; otherwise the fixed 20s substitute would outlive the TTL before use.
        sql(f"UPDATE trip_records SET free_text_input='' WHERE id={int(trip)}")
        check("fixture-only slow prompt marker removed without fabricating plan result", True)
        conversation = request(owner, "POST", "/api/assistant/conversations", json={"active_trip_id": trip})["id"]
        message = request(owner, "POST", f"/api/assistant/conversations/{conversation}/messages", (202,),
                          json={"content": "只调整第1天", "mode": "revise", "record_id": trip,
                                "version": current["trip_version"], "day_index": 0,
                                "planning_revision": current["planning_revision"]}, headers={"Idempotency-Key": str(uuid4())})
        proposal = poll(owner, message["task_id"], {"needs_attention"})["result"]
        proposal_id = proposal["id"]
        detail = request(owner, "GET", f"/api/assistant/conversations/{conversation}/proposals/{proposal_id}")["data"]
        dependency_count = len(detail.get("quality", {}).get("planning_dependencies", {}).get("facts", []))
        check("proposal actually bound live opening facts before expiry", dependency_count > 0 and datetime.now(timezone.utc) < expiry)
        time.sleep(max(0, (expiry-datetime.now(timezone.utc)).total_seconds()) + .5)
        rejected = request(owner, "POST", f"/api/assistant/conversations/{conversation}/proposals/{proposal_id}/confirm", (409,),
                           json={}, headers={"If-Match": str(proposal["version"])})
        check("expired fact confirmation is rejected", rejected.get("code") == "FACTS_EXPIRED")
        still = request(owner, "GET", f"/api/history/{trip}")["data"]
        check("expired proposal cannot change original version", still["version"] == current["trip_version"])

        if args.backend_container:
            fixture_container(args.backend_container)
            interrupted = submit(owner, slow=True)
            before = poll(owner, interrupted, {"running"}, 20)
            subprocess.run(["docker", "restart", "--time", "5", args.backend_container], check=True, capture_output=True, text=True)
            failed = poll(owner, interrupted, {"failed"}, 90)
            check("actual container restart marks interrupted task", failed.get("error_code") == "PROCESS_INTERRUPTED")
            checkpoint(internal, before["execution_id"], "late_after_restart", (409,))
            recovery_key = str(uuid4())
            restarted = request(owner, "POST", f"/api/tasks/{interrupted}/resume", (200, 202), headers={"Idempotency-Key": recovery_key})["data"]["id"]
            completed = poll(owner, restarted, {"succeeded", "needs_attention"})
            check("restarted task recovers with fresh execution", completed.get("resumed_from") == interrupted)
        else:
            report["not_executed"].append("Actual container restart recovery: --backend-container was not supplied")
        report["passed"] = True
        report["fully_executed"] = not report["not_executed"]
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["checks_passed"] = len(report["checks"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for c in clients:
            c.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--business-url", default="http://127.0.0.1:19000")
    parser.add_argument("--database-container", default="trip-validation-postgres-1")
    parser.add_argument("--backend-container")
    parser.add_argument("--service-key-env", default="INTERNAL_SERVICE_KEY")
    parser.add_argument("--slow-seconds", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    result = run(parser.parse_args())
    print(json.dumps({"passed": result["passed"], "fully_executed": result["fully_executed"], "checks_passed": result["checks_passed"]}))
