"""Public Java account/preferences contracts; fixed offline stack, no model calls."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx


def run(output):
    passed = []
    def check(name, condition):
        assert condition, name
        passed.append(name)
    with httpx.Client(base_url="http://127.0.0.1:18080", timeout=15) as client:
        marker = client.get("/api/validation/fixture")
        marker.raise_for_status()
        assert marker.json()["offline_fixture"] is True
        check("anonymous_history_rejected", client.get("/api/history").status_code == 401)
        check("invalid_token_rejected", client.get("/api/history", headers={"Authorization": "Bearer invalid"}).status_code == 401)
        name = "contract_" + uuid4().hex[:10]
        credentials = {"username": name, "password": "contract-test-123"}
        check("weak_password_rejected", client.post("/api/auth/register", json={**credentials, "password": "123"}).status_code == 422)
        registered = client.post("/api/auth/register", json=credentials)
        check("registration", registered.status_code == 200)
        first = {"Authorization": "Bearer " + registered.json()["access_token"]}
        check("duplicate_username_rejected", client.post("/api/auth/register", json=credentials).status_code == 409)
        check("wrong_password_rejected", client.post("/api/auth/login", json={**credentials, "password": "wrong"}).status_code == 401)
        check("unknown_account_rejected", client.post("/api/auth/login", json={**credentials, "username": "absent_"+uuid4().hex}).status_code == 401)
        check("login", client.post("/api/auth/login", json=credentials).status_code == 200)
        check("identity", client.get("/api/auth/me", headers=first).json()["username"] == name)
        check("empty_owner_history", client.get("/api/history", headers=first).json()["total"] == 0)
        check("preferences_opt_in", client.get("/api/preferences/me", headers=first).json()["data"]["saved"] is False)
        preferences = {"preferences": ["历史文化"], "transportation": "公共交通", "accommodation": "经济型酒店"}
        check("preferences_save", client.put("/api/preferences/me", headers=first, json=preferences).status_code == 200)
        check("preferences_read", client.get("/api/preferences/me", headers=first).json()["data"]["preferences"] == ["历史文化"])
        other = client.post("/api/auth/register", json={**credentials, "username": "other_"+uuid4().hex[:10]}).json()
        second = {"Authorization": "Bearer " + other["access_token"]}
        check("preferences_owner_isolation", client.get("/api/preferences/me", headers=second).json()["data"]["saved"] is False)
        check("preferences_delete", client.delete("/api/preferences/me", headers=first).status_code == 200)
        check("preferences_deleted", client.get("/api/preferences/me", headers=first).json()["data"]["saved"] is False)
        check("admin_rebuild_rejected", client.post("/api/rag/rebuild", headers=first).status_code == 403)
        check("invalid_photo_rejected_before_provider", client.get("/api/poi/photo/image", params={"name": ""}).status_code == 422)
        check("logout", client.post("/api/auth/logout", headers=first).status_code == 200)
        check("revoked_token_rejected", client.get("/api/auth/me", headers=first).status_code == 401)
    report = {"mode": "java_public_api_offline", "passed": len(passed), "checks": passed, "external_provider_calls": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output)
