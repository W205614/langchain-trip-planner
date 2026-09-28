"""HTTP acceptance for the assistant proposal loop on the isolated fixture stack."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx


TERMINAL = {"succeeded", "needs_attention", "failed", "cancelled"}


def wait_task(client: httpx.Client, task_id: str) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"/api/trip/tasks/{task_id}")
        response.raise_for_status()
        state = response.json()["data"]
        if state["status"] in TERMINAL:
            return state
        time.sleep(0.2)
    raise AssertionError(f"Task {task_id} did not finish within 45 seconds")


def record(client: httpx.Client, record_id: int) -> dict:
    response = client.get(f"/api/history/{record_id}")
    response.raise_for_status()
    return response.json()["data"]


def run(base_url: str, pause_before_confirm: int = 0) -> dict:
    with httpx.Client(base_url=base_url, timeout=50) as client:
        marker = client.get("/api/validation/fixture")
        assert marker.status_code == 200 and marker.json().get("offline_fixture") is True, \
            "This acceptance case only runs against the isolated offline fixture stack"
        username = "assistant_accept_" + uuid.uuid4().hex[:12]
        registered = client.post("/api/auth/register", json={"username": username, "password": "evaluation123"})
        registered.raise_for_status()
        client.headers["Authorization"] = "Bearer " + registered.json()["access_token"]

        request = {
            "city": "北京", "start_date": "2026-09-11", "end_date": "2026-09-11", "travel_days": 1,
            "transportation": "公共交通", "accommodation": "经济型酒店", "preferences": [],
            "free_text_input": "",
        }
        submitted = client.post("/api/trip/tasks", json=request, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert submitted.status_code == 202, submitted.text
        original_task = wait_task(client, submitted.json()["data"]["id"])
        assert original_task["status"] == "succeeded", original_task
        original_id = original_task["result"]["id"]
        before = record(client, original_id)

        created = client.post("/api/assistant/conversations", json={"active_trip_id": original_id})
        created.raise_for_status()
        conversation_id = created.json()["id"]
        message = client.post(f"/api/assistant/conversations/{conversation_id}/messages",
                              json={"content": "调整第1天，少走路", "mode": "auto"},
                              headers={"Idempotency-Key": str(uuid.uuid4())})
        assert message.status_code == 202, message.text
        proposal_task = wait_task(client, message.json()["task_id"])
        assert proposal_task["status"] == "needs_attention", proposal_task
        proposal_id = proposal_task["result"]["id"]
        assert proposal_id != original_id
        proposal = record(client, proposal_id)
        quality = proposal["quality"]
        assert quality["assistant_proposal_status"] == "pending"
        assert quality["assistant_confirmation_required"] is True
        assert quality["validated_outcome"] in {"complete", "degraded", "draft"}
        assert quality["revision_parent"] == {"record_id": original_id, "version": before["version"]}
        assert all(day["attractions"] for day in proposal["plan"]["days"])
        assert record(client, original_id)["plan"] == before["plan"]
        assert record(client, original_id)["version"] == before["version"]

        if pause_before_confirm:
            print("proposal_ready_for_backend_restart", flush=True)
            time.sleep(pause_before_confirm)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    if client.get("/readyz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise AssertionError("Backend did not become ready after pause")

        url = f"/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}/confirm"
        confirmed = client.post(url, json={}, headers={"If-Match": str(proposal["version"])})
        assert confirmed.status_code == 200, confirmed.text
        after = record(client, original_id)
        assert after["version"] == before["version"] + 1
        assert after["plan"] == proposal["plan"]
        assert after["quality"]["outcome"] == quality["validated_outcome"]
        assert record(client, proposal_id)["quality"]["assistant_proposal_status"] == "confirmed"
        repeated = client.post(url, json={}, headers={"If-Match": str(proposal["version"])})
        assert repeated.status_code == 409, repeated.text
        assert record(client, original_id)["version"] == after["version"]

        return {
            "mode": "offline_fixture_http", "passed": True,
            "original_task_status": original_task["status"], "proposal_task_status": proposal_task["status"],
            "quality_outcome": quality["validated_outcome"],
            "rules_passed": quality.get("rules_passed"), "issues": quality.get("issues", []),
            "data_gaps": quality.get("data_gaps", []),
            "poi_count": sum(len(day["attractions"]) for day in proposal["plan"]["days"]),
            "model_usage": proposal_task.get("usage"),
            "user_confirmation": "fixture_confirmed", "original_version_delta": 1,
            "boundary": "Isolated fixture, not real model or map quality or real user confirmation",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause-before-confirm", type=int, default=0,
                        help="Seconds to allow an external backend restart after proposal creation")
    args = parser.parse_args()
    result = run(args.base_url, args.pause_before_confirm)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": result["mode"], "passed": result["passed"],
                      "quality_outcome": result["quality_outcome"]}))
