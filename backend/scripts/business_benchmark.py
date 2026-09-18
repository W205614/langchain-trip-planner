"""Full HTTP contract evaluation. Default target is the isolated offline fixture stack."""
import argparse
import json
import time
import uuid
from pathlib import Path
import httpx


def run(base_url, output, live=False, token=None, max_requests=20):
    cases = json.loads((Path(__file__).resolve().parents[1] / "evals/business_cases.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=base_url, timeout=320) as client:
        if live:
            if not token:
                raise SystemExit("Live evaluation requires an admin token in EVAL_TOKEN")
            client.headers["Authorization"] = f"Bearer {token}"
            policy = client.get("/api/trip/eval-policy")
            policy.raise_for_status()
            if not policy.json().get("enabled"):
                raise SystemExit("Server live evaluation call and cost limits must be enabled")
            cases = [c for c in cases if not c["request"]["free_text_input"].startswith("fixture:")][:max_requests]
        else:
            marker = client.get("/api/validation/fixture")
            if marker.status_code != 200 or marker.json().get("offline_fixture") is not True:
                raise SystemExit("Offline evaluation requires the isolated fixture server; use --live explicitly")
            response = client.post("/api/auth/register", json={"username": "eval_" + uuid.uuid4().hex[:10], "password": "evaluation123"})
            response.raise_for_status()
            client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        rows = []
        for case in cases:
            print(f"Business case {case['id']}: starting", flush=True)
            if live and rows:
                time.sleep(13)  # Respect the normal 5/minute submission limit.
            started = time.monotonic()
            response = client.post("/api/trip/tasks", json=case["request"], headers={"Idempotency-Key": str(uuid.uuid4())})
            if response.status_code == 422:
                state = {"status": "validation_error"}
            else:
                response.raise_for_status()
                task_id = response.json()["data"]["id"]
                while True:
                    current = client.get(f"/api/trip/tasks/{task_id}")
                    current.raise_for_status()
                    state = current.json()["data"]
                    if state["status"] in {"succeeded", "needs_attention", "failed", "cancelled"}:
                        break
                    if time.monotonic() - started > 325:
                        raise RuntimeError(f"Business case {case['id']}: task {task_id} did not terminate (status={state['status']})")
                    time.sleep(0.2)
            result = state.get("result", {})
            quality = result.get("quality", {})
            plan_days = result.get("data", {}).get("days", [])
            empty_days = [
                index + 1 for index, day in enumerate(plan_days)
                if not day.get("attractions")
            ]
            issue_codes = {item.get("code") for item in quality.get("issues", [])}
            expected_issue = case.get("expected_issue")
            invariant_passed = (
                state["status"] not in {"succeeded", "needs_attention"}
                or (bool(plan_days) and not empty_days)
            )
            expected_issue_passed = expected_issue is None or expected_issue in issue_codes
            persisted = False
            if result.get("id"):
                history = client.get(f"/api/history/{result['id']}")
                persisted = history.status_code == 200 and history.json()["data"]["plan"] == result["data"]
            rows.append({"id": case["id"], "status": state["status"],
                "expected": case["expected"],
                "contract_passed": state["status"] == case["expected"] and invariant_passed and expected_issue_passed,
                "rule_passed": quality.get("passed"), "degraded_days": quality.get("degraded_days", []),
                "empty_days": empty_days, "expected_issue": expected_issue,
                "expected_issue_passed": expected_issue_passed,
                "persisted": persisted, "seconds": round(time.monotonic()-started, 3),
                "token_usage": quality.get("usage"),
                "human_satisfaction": None})
            print(f"Business case {case['id']}: {state['status']} (expected {case['expected']})", flush=True)
        report = {"mode": "live_http" if live else "offline_fixture_http", "cases": rows,
            "contract_passed": sum(row["contract_passed"] for row in rows), "total": len(rows),
            "boundary": "HTTP/auth/task/database/quality contracts; fixture timing is not real model latency or SLA"}
        successes = [row for row in rows if row["status"] == "succeeded"]
        report["summary"] = {"successful_generations": len(successes),
            "draft_generations": sum(row["status"] == "needs_attention" for row in rows),
            "rule_pass_rate": sum(bool(row["rule_passed"]) for row in successes) / len(successes) if successes else None,
            "degraded_rate": sum(bool(row["degraded_days"]) for row in successes) / len(successes) if successes else None,
            "persistence_rate": sum(row["persisted"] for row in successes) / len(successes) if successes else None,
            "mean_seconds_all_cases": sum(row["seconds"] for row in rows) / len(rows) if rows else None}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"mode": report["mode"], "passed": report["contract_passed"], "total": len(rows)}))
        if not live and not all(row["contract_passed"] for row in rows):
            raise SystemExit(1)


if __name__ == "__main__":
    import os
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-requests", type=int, default=20)
    args = parser.parse_args()
    run(args.base_url, args.output, args.live, os.environ.get("EVAL_TOKEN"), args.max_requests)
