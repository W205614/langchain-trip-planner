"""Read-only recovery check against a dedicated worker-disabled Java container.

Credentials belong to the isolated validator, never the source deployment.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from jose import jwt


def verify(backup, container, output, port=19009):
    payload = json.loads((backup / "business.json").read_text(encoding="utf-8"))
    def get(path, header):
        return subprocess.check_output(["docker", "exec", container, "curl", "-fsS", "-H", header,
            f"http://localhost:{port}" + path])
    tokens = {}
    for user in payload["users"]:
        token = jwt.encode({"sub": str(user["id"]), "ver": user["token_version"],
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, os.environ["RESTORE_JWT_SECRET"], algorithm="HS256")
        tokens[user["id"]] = "Authorization: Bearer " + token
        profile = json.loads(get("/api/auth/me", tokens[user["id"]]))
        assert profile["id"] == user["id"] and profile["username"] == user["username"]
        assert profile["is_admin"] == bool(user["is_admin"])
    orphan_ids = []
    for record in payload["trip_records"]:
        if record["user_id"] not in tokens:
            orphan_ids.append(record["id"])
            continue
        row = json.loads(get(f"/api/history/{record['id']}", tokens[record["user_id"]]))["data"]
        assert row["version"] == record["version"]
        assert row["plan"] == json.loads(record["plan_json"])
    checked = 0
    for document in payload["knowledge_documents"]:
        if document["status"] == "deleted":
            continue
        content = get(f"/internal/v1/documents/{document['id']}/{document['version']}/original",
            "X-Service-Key: " + os.environ["RESTORE_INTERNAL_KEY"])
        assert hashlib.sha256(content).hexdigest() == document["sha256"]
        checked += 1
    result = {"restored_accounts_readable": len(tokens), "owned_restored_plans_equal": len(payload["trip_records"])-len(orphan_ids),
        "preexisting_orphan_record_ids": orphan_ids, "orphan_policy": "retained unchanged; no reassignment",
        "original_files_hash_verified": checked,
        "boundary": ("Imported daily Java before enabling workers" if port==9000 else "Restored Java clone only")+"; signed-token compatibility and data reads, not knowledge of users' passwords"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--daily", action="store_true", help="Read private runtime keys; verify imported daily Java while workers are disabled")
    args = parser.parse_args()
    if args.daily:
        from dotenv import dotenv_values
        config=dotenv_values(Path(__file__).resolve().parents[2]/"deploy/runtime/business.env")
        if config.get("WORKERS_ENABLED")!="false":
            raise RuntimeError("Verify baseline before enabling workers")
        os.environ["RESTORE_JWT_SECRET"]=config["JWT_SECRET_KEY"]
        os.environ["RESTORE_INTERNAL_KEY"]=config["INTERNAL_SERVICE_KEY"]
    verify(args.backup, args.container, args.output, 9000 if args.daily else 19009)
