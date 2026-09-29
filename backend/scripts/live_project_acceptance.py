"""Resumable, bounded HTTP acceptance of the deployed Java -> real Agent path."""

from __future__ import annotations

import argparse
from datetime import date, timedelta, datetime, timezone
import json
from pathlib import Path
import re
import secrets
import time
from uuid import uuid4

import httpx


TERMINAL = {"succeeded", "needs_attention", "failed", "cancelled"}
ROOT = Path(__file__).resolve().parents[2]


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_task(client: httpx.Client, identity: str, deadline_seconds: int = 210) -> dict:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/trip/tasks/{identity}")
        response.raise_for_status()
        task = response.json()["data"]
        if task["status"] in TERMINAL:
            return task
        time.sleep(2)
    raise RuntimeError(f"Task {identity} did not reach a terminal state within {deadline_seconds}s")


def record(client: httpx.Client, identity: int) -> dict:
    response = client.get(f"/api/history/{identity}")
    response.raise_for_status()
    return response.json()["data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8380")
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("plan", "proposal"), required=True)
    args = parser.parse_args()
    private = args.private_dir.resolve()
    if private == ROOT or ROOT in private.parents:
        raise ValueError("Private credentials and tokens must stay outside the repository")
    private.mkdir(parents=True, exist_ok=True)
    state_path = private / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    report = {"mode": "live_project_http", "generated_at": datetime.now(timezone.utc).isoformat(),
              "stage": args.stage, "base_url": args.base_url, "passed": False,
              "scope": "HTTP client -> Nginx -> Java -> real Python Agent and AMap; no fixture route"}

    def persist() -> None:
        save(state_path, state)

    try:
        with httpx.Client(base_url=args.base_url, timeout=25, trust_env=False) as client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/api/capabilities").json()["agent"] == "available"
            assert client.get("/api/validation/fixture").status_code == 404
            if "username" not in state:
                state.update(username="live_accept_" + uuid4().hex[:10],
                             password=secrets.token_urlsafe(25))
                persist()
            login = client.post("/api/auth/login", json={k: state[k] for k in ("username", "password")})
            if login.status_code == 401:
                login = client.post("/api/auth/register", json={k: state[k] for k in ("username", "password")})
            login.raise_for_status()
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

            if "trip_task_id" not in state:
                start = (date.today() + timedelta(days=10)).isoformat()
                request = {"city": "北京", "start_date": start, "end_date": start,
                           "travel_days": 1, "transportation": "公共交通",
                           "accommodation": "经济型酒店", "preferences": ["历史文化"],
                           "constraints": {"must_visit": ["故宫博物院"], "avoid": ["八达岭长城"]},
                           "free_text_input": "节奏适中，必去故宫，不去八达岭；不确定的信息请标注。"}
                state["trip_request"] = request
                state.setdefault("trip_key", str(uuid4()))
                persist()
                submitted = client.post("/api/trip/tasks", json=request,
                                        headers={"Idempotency-Key": state["trip_key"]})
                assert submitted.status_code == 202, f"task submit HTTP {submitted.status_code}"
                state["trip_task_id"] = submitted.json()["data"]["id"]
                persist()
            trip_task = wait_task(client, state["trip_task_id"])
            report["plan_task"] = {"id": state["trip_task_id"], "status": trip_task["status"],
                                   "error_code": trip_task.get("error_code"), "usage": trip_task.get("usage")}
            assert trip_task["status"] == "succeeded", report["plan_task"]
            result = trip_task["result"]
            assert result.get("saved") is True and result.get("id"), "Plan was not persisted"
            original_id = result["id"]
            before = record(client, original_id)
            assert before["plan"] == result["data"], "Stored plan differs from Agent result"
            assert all(day["attractions"] for day in before["plan"]["days"])
            names = [item["name"] for day in before["plan"]["days"] for item in day["attractions"]]
            breakfasts = [meal for day in before["plan"]["days"] for meal in day.get("meals", [])
                          if meal.get("type") == "breakfast"]
            late_breakfasts = [meal.get("name", "") for meal in breakfasts
                               if re.search(r"(?:^|\D)11:00\s*[-~–—至]", meal.get("opening_hours", ""))]
            report["plan"] = {"record_id": original_id, "version": before["version"],
                              "quality_outcome": before["quality"].get("outcome"),
                              "rules_passed": before["quality"].get("rules_passed"),
                              "data_gaps": before["quality"].get("data_gaps", []),
                              "attraction_names": names,
                              "breakfast_count": len(breakfasts), "known_late_breakfasts": late_breakfasts,
                              "must_visit_present": "故宫博物院" in names,
                              "avoided_absent": "八达岭长城" not in names}
            assert report["plan"]["must_visit_present"] and report["plan"]["avoided_absent"]
            assert not late_breakfasts, "Known lunch-only restaurant was presented as breakfast"
            if args.stage == "plan":
                report["passed"] = True
            else:
                if "conversation_id" not in state:
                    created = client.post("/api/assistant/conversations", json={"active_trip_id": original_id})
                    created.raise_for_status()
                    state["conversation_id"] = created.json()["id"]
                    persist()
                conversation = state["conversation_id"]
                if "proposal_task_id" not in state:
                    state.setdefault("proposal_key", str(uuid4()))
                    persist()
                    submitted = client.post(
                        f"/api/assistant/conversations/{conversation}/messages",
                        json={"content": "调整第1天，少走路，保留故宫博物院", "mode": "auto"},
                        headers={"Idempotency-Key": state["proposal_key"]})
                    assert submitted.status_code == 202, f"proposal submit HTTP {submitted.status_code}"
                    state["proposal_task_id"] = submitted.json()["task_id"]
                    persist()
                task = wait_task(client, state["proposal_task_id"])
                report["proposal_task"] = {"id": state["proposal_task_id"], "status": task["status"],
                                           "error_code": task.get("error_code"), "usage": task.get("usage")}
                assert task["status"] == "needs_attention", report["proposal_task"]
                proposal_id = task["result"]["id"]
                proposal = record(client, proposal_id)
                quality = proposal["quality"]
                assert quality["assistant_proposal_status"] == "pending"
                assert quality["assistant_confirmation_required"] is True
                assert quality["revision_parent"] == {"record_id": original_id, "version": before["version"]}
                assert record(client, original_id)["plan"] == before["plan"]
                assert record(client, original_id)["version"] == before["version"]
                report["proposal"] = {"record_id": proposal_id, "validated_outcome": quality["validated_outcome"],
                                      "pending_before_confirm": True, "original_unchanged_before_confirm": True}
                url = f"/api/assistant/conversations/{conversation}/proposals/{proposal_id}/confirm"
                if not state.get("confirmed"):
                    confirmed = client.post(url, json={}, headers={"If-Match": str(proposal["version"])})
                    assert confirmed.status_code == 200, f"confirm HTTP {confirmed.status_code}"
                    state["confirmed"] = True
                    persist()
                after = record(client, original_id)
                assert after["version"] == before["version"] + 1
                assert after["plan"] == proposal["plan"]
                assert record(client, proposal_id)["quality"]["assistant_proposal_status"] == "confirmed"
                repeated = client.post(url, json={}, headers={"If-Match": str(proposal["version"])})
                assert repeated.status_code == 409
                assert record(client, original_id)["version"] == after["version"]
                report["proposal"].update(confirmed_once=True, repeat_conflict=True,
                                          original_version_after=after["version"])
                report["passed"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:240]
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        save(args.output, report)
        print(json.dumps({"stage": args.stage, "passed": report["passed"],
                          "plan_status": report.get("plan_task", {}).get("status"),
                          "proposal_status": report.get("proposal_task", {}).get("status"),
                          "error_type": report.get("error_type"), "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
