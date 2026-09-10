"""Real container restart with fixture generation; fixed isolated Compose target."""
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4
import httpx


def run():
    compose = ["docker", "compose", "-p", "trip-validation", "-f", "docker-compose.validation.yml"]
    with httpx.Client(base_url="http://127.0.0.1:18080", timeout=30) as client:
        assert client.get("/api/validation/fixture").json()["offline_fixture"] is True
        account = client.post("/api/auth/register", json={"username": "restart_"+uuid4().hex[:8], "password": "restart123"})
        account.raise_for_status()
        client.headers["Authorization"] = "Bearer " + account.json()["access_token"]
        body = {"city": "北京", "start_date": "2026-09-11", "end_date": "2026-09-11", "travel_days": 1,
                "transportation": "步行", "accommodation": "酒店", "preferences": [], "free_text_input": "fixture:slow"}
        key = str(uuid4())
        created = client.post("/api/trip/tasks", json=body, headers={"Idempotency-Key": key})
        created.raise_for_status()
        task_id = created.json()["data"]["id"]
        for _ in range(100):
            if client.get(f"/api/trip/tasks/{task_id}").json()["data"]["status"] == "running":
                break
            time.sleep(.1)
        else:
            raise RuntimeError("Task never started")
        # Read one event, then disconnect; this must not cancel generation.
        with client.stream("GET", f"/api/trip/tasks/{task_id}/events") as stream:
            next(stream.iter_lines())
        assert client.get(f"/api/trip/tasks/{task_id}").json()["data"]["status"] == "running"
        subprocess.run(compose + ["restart", "backend"], check=True, capture_output=True)
        for _ in range(100):
            try:
                current = client.get(f"/api/trip/tasks/{task_id}")
                if current.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.5)
        current.raise_for_status()
        state = current.json()["data"]
        assert (state["status"], state["error_code"]) == ("failed", "PROCESS_INTERRUPTED")
        repeat = client.post("/api/trip/tasks", json=body, headers={"Idempotency-Key": key})
        assert repeat.json()["data"]["id"] == task_id
        assert repeat.json()["data"]["status"] == "failed"
        assert client.get("/api/history").json()["total"] == 0
        report = {"mode": "isolated_fixture_real_container_restart", "disconnect_kept_running": True,
                  "restart_error": state["error_code"], "same_key_did_not_regenerate": True, "history_count": 0}
        Path("docs/evidence/lifecycle-offline.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))


if __name__ == "__main__":
    run()
