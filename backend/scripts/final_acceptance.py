"""Real HTTP role and planning acceptance, restricted to the isolated offline stack.

Uses real accounts, Java authorization/transactions, PostgreSQL and the real Agent
pipeline with provider fixtures. Does not claim live model or travel fact accuracy.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import quote
from uuid import uuid4

import httpx


TERMINAL = {"succeeded", "needs_attention", "failed", "cancelled"}


def run(base_url, output):
    report = {"mode": "isolated_final_planning_roles", "started_at": datetime.now(timezone.utc).isoformat(),
              "passed": False, "checks": [], "provider_calls": 0,
              "boundary": "Real HTTP authorization and persistence; map and model are offline substitutes."}
    clients = {}

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def response(client, method, path, expected=(200,), label=None, **kwargs):
        result = client.request(method, path, **kwargs)
        if result.status_code not in expected:
            # This runner is only usable against non-secret fixture data.
            raise AssertionError(f"{label or path}: HTTP {result.status_code}; {result.text[:1200]}")
        if label:
            report["checks"].append({"name": label, "passed": True, "http_status": result.status_code})
        return result.json() if result.content and "application/json" in result.headers.get("content-type", "") else {}

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report["checks"].append({"name": name, "passed": True})

    def task(client, identity):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = response(client, "GET", f"/api/trip/tasks/{identity}")["data"]
            if state["status"] in TERMINAL:
                if state["status"] not in {"succeeded", "needs_attention"}:
                    raise AssertionError(f"Task failed: {state.get('error_code')} {state.get('message')}")
                return state
            time.sleep(.3)
        raise AssertionError("Task remained nonterminal")

    try:
        anonymous = httpx.Client(base_url=base_url, timeout=45, trust_env=False)
        clients["anonymous"] = anonymous
        fixture = response(anonymous, "GET", "/api/validation/fixture")
        if fixture.get("offline_fixture") is not True:
            raise RuntimeError("Refusing non-fixture deployment")
        prefix = "final_" + uuid4().hex[:8]
        users = {}
        for role in ("owner", "editor", "viewer", "pending", "outsider", "removed"):
            client = httpx.Client(base_url=base_url, timeout=45, trust_env=False)
            clients[role] = client
            users[role] = prefix + "_" + role
            registered = response(client, "POST", "/api/auth/register", json={"username": users[role], "password": "isolated-final123"})
            client.headers["Authorization"] = "Bearer " + registered["access_token"]
        owner = clients["owner"]
        request = {"city": "北京", "start_date": "2026-11-01", "end_date": "2026-11-03", "travel_days": 3,
                   "transportation": "公共交通", "accommodation": "经济型酒店", "preferences": ["历史文化"],
                   "planning_input": {"timezone": "Asia/Shanghai", "day_start": "09:00", "day_end": "19:00"}}
        key = str(uuid4())
        submitted = response(owner, "POST", "/api/trip/tasks", (202,), "owner submits structured multi-day task",
                             json=request, headers={"Idempotency-Key": key})
        identity = submitted["data"]["id"]
        same = response(owner, "POST", "/api/trip/tasks", (202,), json=request, headers={"Idempotency-Key": key})
        check("same key reuses task", same["data"]["id"] == identity)
        state = task(owner, identity)
        trip_id = state["result"]["id"]
        original = response(owner, "GET", f"/api/history/{trip_id}")["data"]
        check("multi-day result persisted", len(original["plan"]["days"]) == 3)
        check("each day exposes timeline", all("timeline" in d for d in original["plan"]["days"]))
        check("stable activity ids", all(a.get("activity_id") for d in original["plan"]["days"] for a in d["attractions"]))

        invited = {}
        for role in ("editor", "viewer", "pending", "removed"):
            invited[role] = response(owner, "POST", f"/api/trips/{trip_id}/members",
                                     json={"username": users[role], "role": "editor" if role == "editor" else "viewer"})["user_id"]
            if role != "pending":
                response(clients[role], "POST", f"/api/trips/{trip_id}/members/accept", label=f"{role} accepts invitation")
        response(owner, "DELETE", f"/api/trips/{trip_id}/members/{invited['removed']}", label="owner revokes member")
        for role in ("editor", "viewer"):
            workspace = response(clients[role], "GET", f"/api/trips/{trip_id}/workspace", label=f"accepted {role} reads real backend")
            check(f"{role} sees current plan", workspace["data"]["plan"]["days"] == original["plan"]["days"])
        for role in ("pending", "removed", "outsider", "anonymous"):
            response(clients[role], "GET", f"/api/trips/{trip_id}/workspace", (401, 403, 404), f"{role} cannot read private trip")

        planning = response(owner, "GET", f"/api/trips/{trip_id}/planning-input")["data"]
        for role in ("editor", "viewer", "pending", "removed", "outsider", "anonymous"):
            response(clients[role], "PUT", f"/api/trips/{trip_id}/planning-input", (401, 403, 404),
                     f"{role} cannot change owner constraints", json={"planning_input": planning["planning_input"], "planning_revision": planning["planning_revision"]},
                     headers={"If-Match": str(planning["trip_version"])})
        changed = copy.deepcopy(planning["planning_input"])
        changed["day_end"] = "20:00"
        updated = response(owner, "PUT", f"/api/trips/{trip_id}/planning-input", label="owner updates constraints",
                           json={"planning_input": changed, "planning_revision": planning["planning_revision"]},
                           headers={"If-Match": str(planning["trip_version"])})["data"]
        check("planning revision increases", updated["planning_revision"] == planning["planning_revision"] + 1)
        response(owner, "PUT", f"/api/trips/{trip_id}/planning-input", (409,), "stale planning revision rejected",
                 json={"planning_input": changed, "planning_revision": planning["planning_revision"]},
                 headers={"If-Match": str(planning["trip_version"])})

        activity = updated["plan"]["days"][0]["attractions"][0]["activity_id"]
        activity_path = f"/api/trips/{trip_id}/activities/{quote(activity, safe='')}/state"
        locked = response(owner, "POST", activity_path, label="owner locks activity",
                          json={"planning_revision": updated["planning_revision"], "locked": True},
                          headers={"If-Match": str(updated["trip_version"])})["data"]
        for role in ("editor", "viewer", "outsider"):
            response(clients[role], "POST", activity_path, (403, 404), f"{role} cannot unlock owner's activity",
                     json={"planning_revision": locked["planning_revision"], "locked": False},
                     headers={"If-Match": str(locked["trip_version"])})
        before = response(owner, "GET", f"/api/history/{trip_id}")["data"]
        edited = copy.deepcopy(before["plan"])
        edited["days"][0]["attractions"][0]["visit_duration"] += 30
        response(owner, "PUT", f"/api/trips/{trip_id}", (409, 422), "manual write cannot bypass locked activity",
                 json=edited, headers={"If-Match": str(before["version"])})

        conversation = response(owner, "POST", "/api/assistant/conversations", json={"active_trip_id": trip_id})["id"]
        message = response(owner, "POST", f"/api/assistant/conversations/{conversation}/messages", (202,), "owner requests day-two proposal",
                           json={"content": "只调整第2天，其他日期保持不变", "mode": "revise", "record_id": trip_id,
                                 "version": before["version"], "day_index": 1,
                                 "planning_revision": locked["planning_revision"], "revision_scope": {"day_indices": [1]}},
                           headers={"Idempotency-Key": str(uuid4())})
        proposal_state = task(owner, message["task_id"])
        proposal_id = proposal_state["result"]["id"]
        proposal = response(owner, "GET", f"/api/history/{proposal_id}")["data"]
        unchanged = response(owner, "GET", f"/api/history/{trip_id}")["data"]
        check("preview leaves original untouched", unchanged["plan"] == before["plan"] and unchanged["version"] == before["version"])
        for index in (0, 2):
            check(f"non-target day {index} unchanged", proposal["plan"]["days"][index] == before["plan"]["days"][index])
        confirm_path = f"/api/assistant/conversations/{conversation}/proposals/{proposal_id}/confirm"
        response(clients["outsider"], "POST", confirm_path, (403, 404), "cross-user confirm rejected", json={}, headers={"If-Match": str(proposal["version"])})
        response(owner, "GET", f"/api/assistant/conversations/{conversation}/proposals/{proposal_id}", label="proposal differences readable")
        response(owner, "POST", confirm_path, label="owner confirms exact proposal", json={}, headers={"If-Match": str(proposal["version"])})
        response(owner, "POST", confirm_path, (409,), "duplicate confirm rejected", json={}, headers={"If-Match": str(proposal["version"])})
        after = response(owner, "GET", f"/api/history/{trip_id}")["data"]
        check("confirmation increments original exactly once", after["version"] == before["version"] + 1)

        response(anonymous, "POST", "/internal/v2/outbound/reserve", (401, 403, 404), "internal budget not publicly exposed",
                 json={"kind": "text", "request_key": "forged"})
        response(clients["outsider"], "GET", f"/api/trip/tasks/{identity}", (403, 404), "private task isolated")
        report["passed"] = True
        report["trip_id"] = trip_id
        report["checks_passed"] = len(report["checks"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        for client in clients.values():
            client.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.base_url, args.output)
    print(json.dumps({"passed": result["passed"], "checks_passed": result["checks_passed"]}))
