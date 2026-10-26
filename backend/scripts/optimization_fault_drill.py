"""Guarded fault drill for the isolated optimization candidate; never a daily stack."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
from deployment_assets import docker, services, sql
from automated_maintenance import recover_scheduler
from optimization_http_comparison import INPUT


def snapshot(project):
    return {name: {"id": item["Id"], "running": item["State"]["Running"],
                   "mounts": [{"type": m["Type"], "source": m["Source"], "target": m["Destination"]}
                              for m in item["Mounts"]]}
            for name, item in services(project).items()}


def run(project, base_url, prometheus, output):
    if project not in {"trip-opt-candidate-20261026", "trip-opt-candidate-20261026-v2"}:
        raise ValueError("Only the explicitly named isolated candidate is eligible")
    selected = services(project)
    if any(c["Config"]["Labels"].get("com.tripplanner.validation") != "candidate" for c in selected.values()):
        raise ValueError("Every selected application container requires the candidate label")
    report = {"operation": "isolated_scheduler_fault_drill", "project": project,
              "started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "real_paid_calls": 0, "daily_before": snapshot("langchain-trip-planner"), "checks": {}}

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def wait_for(check, timeout=90):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if check():
                return
            time.sleep(2)
        raise TimeoutError("Isolated fault-drill condition did not arrive")

    try:
        save()
        with httpx.Client(base_url=base_url, timeout=20, trust_env=False) as client:
            marker = client.get("/api/validation/fixture"); marker.raise_for_status()
            if marker.json().get("offline_fixture") is not True:
                raise ValueError("Offline provider marker required")
            account = client.post("/api/auth/register", json={"username": "fault_" + uuid4().hex[:18], "password": "Isolated!Fault_2026"})
            account.raise_for_status()
            client.headers["Authorization"] = "Bearer " + account.json()["access_token"]
            request = {**INPUT, "free_text_input": "fixture:slow"}
            response = client.post("/api/trip/tasks", json=request, headers={"Idempotency-Key": str(uuid4())})
            response.raise_for_status(); task_id = response.json()["data"]["id"]
            def task():
                response = client.get(f"/api/trip/tasks/{task_id}"); response.raise_for_status()
                return response.json()["data"]
            wait_for(lambda: task()["status"] == "running")
            report["interrupted_task"] = task_id
            key = 842491570021
            lease = sql(selected["postgres"]["Id"], "trip", f"SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted AND classid={key >> 32} AND objid={key & 0xffffffff}")
            if not lease.isdecimal():
                raise RuntimeError("Expected exactly one candidate scheduler lease")
            killed = sql(selected["postgres"]["Id"], "trip", f"SELECT pg_terminate_backend({int(lease)})")
            if killed != "t":
                raise RuntimeError("Candidate lease was not terminated")
            wait_for(lambda: client.get("/readyz").status_code == 503, 20)
            rejected = client.post("/api/trip/tasks", json=INPUT, headers={"Idempotency-Key": str(uuid4())})
            report["checks"]["admission_after_lease_loss"] = {"status": rejected.status_code, "code": rejected.json().get("error_code")}
            assert rejected.status_code == 503 and rejected.json().get("error_code") == "SCHEDULER_OWNERSHIP_LOST"
            save()
            # Let the old provider execution return while the old Java process
            # remains alive. Neither a plan nor terminal metadata may be written.
            time.sleep(24)
            old = task()
            stored = sql(selected["postgres"]["Id"], "trip", f"SELECT status||':'||COALESCE(record_id::text,'none') FROM trip_tasks WHERE id='{task_id}'")
            report["checks"]["old_execution_before_restart"] = {"api_status": old["status"], "stored_status_and_record": stored}
            assert old["status"] == "running" and stored == "running:none"
            save()
            def ownership_alert():
                result = httpx.get(prometheus + "/api/v1/query", params={"query": 'ALERTS{alertname="TripSchedulerOwnershipLost",alertstate="firing"}'}, timeout=10, trust_env=False)
                result.raise_for_status()
                return bool(result.json()["data"]["result"])
            wait_for(ownership_alert, 110)
            receiver = docker("ps", "-q", "--filter", f"label=com.docker.compose.project={project}", "--filter", "label=com.docker.compose.service=alert-receiver").decode().strip()
            def delivered():
                entries = docker("exec", receiver, "python", "-c", "from pathlib import Path; p=Path('/app/runtime/alerts.jsonl'); print(p.read_text() if p.exists() else '')").decode().splitlines()
                return [json.loads(x) for x in entries if x.strip()]
            wait_for(lambda: any(x["name"] == "TripSchedulerOwnershipLost" and x["status"] == "firing" for x in delivered()), 60)
            report["checks"]["alert_delivery"] = {"prometheus_firing": True, "durable_receiver_firing": True, "destination": "isolated local durable inbox", "external_human_notification": False}
            save()
            recovery_started = time.monotonic()
            report["recovery"] = recover_scheduler(project, execute=True)
            report["recovery_seconds"] = round(time.monotonic() - recovery_started, 3)
            recovered = task()
            assert recovered["status"] == "failed" and recovered.get("error_code") == "PROCESS_INTERRUPTED"
            response = client.post("/api/trip/tasks", json=INPUT, headers={"Idempotency-Key": str(uuid4())})
            response.raise_for_status(); fresh_id = response.json()["data"]["id"]
            def fresh():
                r = client.get(f"/api/trip/tasks/{fresh_id}"); r.raise_for_status(); return r.json()["data"]
            wait_for(lambda: fresh()["status"] in {"succeeded", "needs_attention", "failed", "cancelled"})
            assert fresh()["status"] in {"succeeded", "needs_attention"}
            report["checks"]["post_recovery"] = {"readiness": client.get("/readyz").status_code, "old_task_code": recovered.get("error_code"), "new_task_status": fresh()["status"]}
            wait_for(lambda: any(x["name"] == "TripSchedulerOwnershipLost" and x["status"] == "resolved" for x in delivered()), 180)
            entries = [x for x in delivered() if x["name"] == "TripSchedulerOwnershipLost"]
            assert len({x["event_id"] for x in entries}) == len(entries)
            report["checks"]["alert_delivery"].update(resolved_received=True, duplicate_event_ids=0)
        report["passed"] = True
    except Exception as ex:
        report["failure"] = {"type": type(ex).__name__, "message": str(ex)[:200]}
        raise
    finally:
        report["daily_after"] = snapshot("langchain-trip-planner")
        report["daily_unchanged"] = report["daily_before"] == report["daily_after"]
        report["passed"] = report["passed"] and report["daily_unchanged"]
        report["completed_at"] = datetime.now(timezone.utc).isoformat(); save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="trip-opt-candidate-20261026")
    parser.add_argument("--base-url", default="http://127.0.0.1:18086")
    parser.add_argument("--prometheus", default="http://127.0.0.1:19096")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.project, args.base_url, args.prometheus, args.output)
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}))
