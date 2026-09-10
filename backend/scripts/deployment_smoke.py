"""Local deployment acceptance without invoking paid upstream generation."""
import argparse
import json
import secrets
from pathlib import Path
from uuid import uuid4
import httpx


def run(output):
    checks = {}
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=15) as client:
        def check(name, value):
            checks[name] = bool(value)
            assert value, name
        check("ready", client.get("/readyz").status_code == 200)
        check("frontend", client.get("/").status_code == 200)
        check("fixture_disabled", client.get("/api/validation/fixture").status_code == 404)
        # Nginx exposes only /api and health routes; inspect contracts using authenticated requests.
        account = {"username": "deploy_" + uuid4().hex[:10], "password": secrets.token_urlsafe(18)}
        registered = client.post("/api/auth/register", json=account)
        check("register", registered.status_code == 200)
        client.headers["Authorization"] = "Bearer " + registered.json()["access_token"]
        check("task_list", client.get("/api/trip/tasks").json()["total"] == 0)
        body = {"city": "北京", "start_date": "2026-09-11", "end_date": "2026-09-11", "travel_days": 1,
                "transportation": "步行", "accommodation": "酒店", "constraints": {"must_visit": ["同一景点"], "avoid": ["同一景点"]}}
        check("contradictory_constraints_rejected", client.post("/api/trip/tasks", json=body).status_code == 422)
        check("no_generation_created", client.get("/api/trip/tasks").json()["total"] == 0)
        check("logout", client.post("/api/auth/logout").status_code == 200)
        check("old_token_revoked", client.get("/api/auth/me").status_code == 401)
    report = {"mode": "daily_docker_deployment", "checks": checks,
              "boundary": "Deployment/auth/schema acceptance; no generation or paid upstream evaluation."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checks_passed": len(checks)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
