"""Explicit one-day real-upstream acceptance for the local deployment.

Never run in CI. Creates dedicated ordinary accounts and one saved itinerary.
The private state file permits UI verification without printing credentials.
"""
import argparse
import json
import secrets
import time
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
import httpx


def run(state_path, output):
    started = time.monotonic()
    report = {"mode": "local_real_upstream_functional", "checks": {}}
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=320) as client:
        def check(name, condition):
            report["checks"][name] = bool(condition)
            assert condition, name
        check("ready", client.get("/readyz").status_code == 200)
        check("fixture_disabled", client.get("/api/validation/fixture").status_code == 404)
        check("anonymous_rebuild_denied", client.post("/api/rag/rebuild").status_code == 401)
        account = {"username": "accept_"+uuid4().hex[:10], "password": secrets.token_urlsafe(18)}
        registered = client.post("/api/auth/register", json=account)
        registered.raise_for_status()
        login = client.post("/api/auth/login", json=account)
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        check("register_and_login", client.get("/api/auth/me").json()["username"] == account["username"])
        check("ordinary_rebuild_denied", client.post("/api/rag/rebuild").status_code == 403)
        preference = {"preferences": ["历史文化"], "transportation": "公共交通", "accommodation": "经济型酒店"}
        check("save_preferences", client.put("/api/preferences/me", json=preference).status_code == 200)
        check("read_preferences", client.get("/api/preferences/me").json()["data"]["preferences"] == ["历史文化"])
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        body = {"city": "北京", "start_date": tomorrow, "end_date": tomorrow, "travel_days": 1,
                **preference, "free_text_input": "一天安排两到三个景点，节奏轻松，不安排购物。"}
        key = str(uuid4())
        created = client.post("/api/trip/tasks", json=body, headers={"Idempotency-Key": key})
        check("task_accepted", created.status_code == 202)
        task_id = created.json()["data"]["id"]
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({**account, "task_id": task_id}), encoding="utf-8")
        repeated = client.post("/api/trip/tasks", json=body, headers={"Idempotency-Key": key})
        check("same_key_same_task", repeated.json()["data"]["id"] == task_id)
        conflicting = client.post("/api/trip/tasks", json=body | {"city": "上海"}, headers={"Idempotency-Key": key})
        check("same_key_changed_body_conflict", conflicting.status_code == 409)
        while True:
            state = client.get(f"/api/trip/tasks/{task_id}").json()["data"]
            if state["status"] in {"succeeded", "failed"}:
                break
            if time.monotonic() - started > 330:
                raise RuntimeError("Task did not terminate within contract")
            time.sleep(2)
        report["task_status"] = state["status"]
        report["error_code"] = state.get("error_code")
        check("generation_saved", state["status"] == "succeeded" and state["result"]["saved"])
        result = state["result"]
        record_id = result["id"]
        plan = result["data"]
        check("real_poi_ids", all(a["poi_id"] and not a["poi_id"].startswith("fixture") for d in plan["days"] for a in d["attractions"]))
        report["quality"] = result["quality"]
        report["attractions"] = [{"name": a["name"], "poi_id": a["poi_id"]} for d in plan["days"] for a in d["attractions"]]
        detail = client.get(f"/api/history/{record_id}").json()["data"]
        check("history_matches_result", detail["plan"] == plan and detail["quality"] == result["quality"])
        edited = client.put(f"/api/history/{record_id}", json=plan, headers={"If-Match": str(detail["version"])})
        check("history_save", edited.status_code == 200 and edited.json()["version"] == detail["version"] + 1)
        check("stale_edit_conflict", client.put(f"/api/history/{record_id}", json=plan, headers={"If-Match": str(detail["version"])}).status_code == 409)
        other = client.post("/api/auth/register", json={"username": "isolate_"+uuid4().hex[:10], "password": secrets.token_urlsafe(18)})
        other.raise_for_status()
        other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
        check("cross_user_task_denied", client.get(f"/api/trip/tasks/{task_id}", headers=other_headers).status_code == 404)
        check("cross_user_history_denied", client.get(f"/api/history/{record_id}", headers=other_headers).status_code == 404)
        research = client.post("/api/research", json={"city": "北京", "query": "故宫参观预约"})
        check("public_research", research.status_code == 200)
        report["research_evidence_count"] = len(research.json()["data"]["evidence"])
        report["record_id"] = record_id
        report["seconds"] = round(time.monotonic() - started, 2)
        report["boundary"] = "One local real-upstream request; not a quality benchmark or SLA; model output may degrade."
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"checks_passed": len(report["checks"]), "seconds": report["seconds"],
                          "degraded_days": result["quality"].get("degraded_days"), "record_id": record_id}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-real-upstream", action="store_true", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.state, args.output)
