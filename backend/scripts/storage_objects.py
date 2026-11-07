"""Object snapshot helpers for coherent Java/Flyway maintenance backups.

Writers must already be stopped. Provider credentials remain in private env files;
only the Java SDK command connects to the object service.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from deployment_assets import docker, inspect, services, sql
from dotenv import dotenv_values


def object_references(postgres: str, database: str, user: str = "trip") -> list[dict]:
    present = sql(postgres, database, "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='knowledge_documents' AND column_name='storage_backend')", user)
    if present != "t":
        return []
    origins = sql(postgres, database, "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='knowledge_documents' AND column_name='storage_origin')", user)
    if origins != "t":
        has_s3 = sql(postgres, database, "SELECT EXISTS(SELECT 1 FROM knowledge_documents WHERE storage_backend='s3') OR EXISTS(SELECT 1 FROM upload_reservations WHERE storage_backend='s3')", user)
        if has_s3 == "t":
            raise RuntimeError("Existing S3 references need verified origin registration before snapshot")
        return []
    query = """WITH refs AS (
SELECT storage_backend,storage_bucket,storage_key,storage_origin,size_bytes,sha256,media_type,true AS required FROM knowledge_documents
WHERE storage_backend='s3' AND status<>'deleted'
UNION ALL
SELECT storage_backend,storage_bucket,storage_key,storage_origin,size_bytes,sha256,media_type,false AS required FROM upload_reservations
WHERE storage_backend='s3' AND status IN ('reserved','stored','uncertain')
UNION ALL
SELECT source_backend,source_bucket,source_key,source_origin,size_bytes,sha256,'application/octet-stream',false FROM storage_migrations WHERE source_backend='s3'
UNION ALL
SELECT target_backend,target_bucket,target_key,target_origin,size_bytes,sha256,'application/octet-stream',false FROM storage_migrations WHERE target_backend='s3'
), grouped AS (
 SELECT storage_backend,storage_bucket,storage_key,storage_origin,max(size_bytes) AS size_bytes,sha256,
 max(media_type) AS media_type,bool_or(required) AS required FROM refs
 GROUP BY storage_backend,storage_bucket,storage_key,storage_origin,sha256
)
SELECT COALESCE(json_agg(grouped ORDER BY storage_bucket,storage_key),'[]'::json) FROM grouped"""
    return json.loads(sql(postgres, database, query, user))


def export_snapshot(deployed: dict, output: Path, database_client=None) -> dict | None:
    """Export every referenced S3 upload using the selected backend's exact image/env."""
    if any(deployed[name]["State"]["Running"] for name in ("backend", "agent")):
        raise RuntimeError("Stop all writers before exporting object references")
    if database_client is None:
        from backup_database import DatabaseClient, private_directory
        output = output.resolve()
        private_directory(output, exist_ok=True)
        with DatabaseClient(deployed, output / ("database-client-" + uuid4().hex[:12])) as client:
            return export_snapshot(deployed, output, client)
    backend = deployed["backend"]
    refs = object_references(database_client.container, database_client.target["database"], database_client.target["user"])
    if not refs:
        return None
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if "," in str(output):
        raise ValueError("Docker bind paths cannot contain commas")
    (output / "object-refs.json").write_text(json.dumps(refs), encoding="utf-8")
    environment = {key: value for key, value in (item.split("=", 1) for item in backend["Config"]["Env"] if "=" in item)
                   if key in {"DATASET_ID", "UPLOAD_DIR", "UPLOAD_STORAGE_BACKEND"} or key.startswith(("S3_", "AWS_"))}
    private = output / "storage-runtime.env"
    if any("\n" in value or "\r" in value for value in environment.values()):
        raise ValueError("Unsupported multiline runtime configuration")
    private.write_text("".join(f"{key}={value}\n" for key, value in environment.items()), encoding="utf-8")
    private.chmod(0o600)
    # Host-created staging retains the selected private parent ACL on Windows;
    # Linux uses the operator UID below rather than the image's service UID.
    (output / "data").mkdir(exist_ok=True)
    networks = backend["NetworkSettings"]["Networks"]
    if not networks:
        raise RuntimeError("Backend has no object-service network")
    args = ["run", "--rm", "--network", next(iter(networks)), "--env-file", str(private),
            "--mount", f"type=bind,source={output},target=/snapshot"]
    if hasattr(os, "getuid"):
        args += ["--user", f"{os.getuid()}:{os.getgid()}"]
    args += [backend["Image"], "storage", "export", "--refs", "/snapshot/object-refs.json", "--directory", "/snapshot/data"]
    docker(*args)
    manifest = json.loads((output / "data" / "manifest.json").read_text(encoding="utf-8"))
    missing = sum(bool(row.get("missing")) for row in manifest["objects"])
    return {"schema": manifest["schema"], "dataset_id": manifest["dataset_id"], "directory": "objects/data", "objects": len(manifest["objects"]) - missing, "missing_optional_references": missing}


def apply_location_map(postgres: str, database: str, mapping_file: Path, user: str = "trip") -> int:
    """Explicit clone-only reference update, after the SDK has verified all restored bytes."""
    state = inspect(postgres)
    labels = state["Config"].get("Labels") or {}
    if labels.get("com.docker.compose.project") in {"langchain-trip-planner", "trip-production"}:
        raise ValueError("Refusing to rewrite a daily/production database")
    if not re.fullmatch(r"trip[a-zA-Z0-9_-]*(restore|recovery|validation|test|public)[a-zA-Z0-9_-]*", state["Name"].lstrip("/")):
        raise ValueError("Select an explicitly named isolated restore/test PostgreSQL container")
    rows = json.loads(mapping_file.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Location map must be an array")
    def literal(value):
        if not isinstance(value, str) or "\x00" in value or len(value) > 1024:
            raise ValueError("Invalid location field")
        return "'" + value.replace("'", "''") + "'"
    statements = ["BEGIN"]
    for row in rows:
        for side in ("source", "target"):
            if row[f"{side}_backend"] not in {"local", "s3"}:
                raise ValueError("Unknown storage backend")
        where = " AND ".join(f"storage_{field}={literal(row['source_'+field])}" for field in ("backend", "bucket", "key", "origin"))
        changes = ",".join(f"storage_{field}={literal(row['target_'+field])}" for field in ("backend", "bucket", "key", "origin"))
        for table in ("knowledge_documents", "upload_reservations"):
            statements.append(f"UPDATE {table} SET {changes} WHERE {where}")
        for side in ("source", "target"):
            migration_where = " AND ".join(f"{side}_{field}={literal(row['source_'+field])}" for field in ("backend", "bucket", "key", "origin"))
            migration_changes = ",".join(f"{side}_{field}={literal(row['target_'+field])}" for field in ("backend", "bucket", "key", "origin"))
            statements.append(f"UPDATE storage_migrations SET {migration_changes} WHERE {migration_where}")
    statements.append("COMMIT")
    sql(postgres, database, ";".join(statements), user)
    return len(rows)


def restore_snapshot(backup: Path, output: Path, image: str, network: str,
                     source_values: dict, object_config: Path) -> tuple[dict, dict]:
    """Restore only to an explicit destination, leaving backup files and source objects intact."""
    allowed = {"UPLOAD_STORAGE_BACKEND", "S3_ENDPOINT", "S3_REGION", "S3_BUCKET", "S3_PREFIX",
               "S3_PATH_STYLE", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "S3_ALLOW_HTTP"}
    configured = {key: value for key, value in dotenv_values(object_config).items() if value is not None}
    if configured.keys() - allowed:
        raise ValueError("Object restore config contains unrelated settings")
    if configured.get("UPLOAD_STORAGE_BACKEND") != "s3" or not all(configured.get(key) for key in ("S3_BUCKET", "S3_PREFIX", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")):
        raise ValueError("Object restore requires an explicit S3 bucket, new prefix and target credentials")
    values = {key: value for key, value in source_values.items() if not key.startswith(("S3_", "AWS_"))}
    values.update(configured)
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Unsupported multiline storage configuration")
    output = output.resolve()
    private = output / "restore-storage.env"
    command_values = {key: value for key, value in values.items()
                      if key in {"DATASET_ID", "UPLOAD_DIR", "UPLOAD_STORAGE_BACKEND"} or key.startswith(("S3_", "AWS_"))}
    private.write_text("".join(f"{key}={value}\n" for key, value in command_values.items()), encoding="utf-8")
    private.chmod(0o600)
    snapshot = (backup / "objects").resolve()
    if not snapshot.is_relative_to(backup.resolve()) or "," in str(snapshot) or "," in str(output):
        raise ValueError("Unsupported snapshot mount")
    args = ["run", "--rm", "--network", network, "--add-host", "host.docker.internal:host-gateway",
            "--env-file", str(private), "--mount", f"type=bind,source={snapshot},target=/snapshot,readonly",
            "--mount", f"type=bind,source={output},target=/restore"]
    if hasattr(os, "getuid"):
        args += ["--user", f"{os.getuid()}:{os.getgid()}"]
    args += [image, "storage", "restore", "--directory", "/snapshot/data", "--mapping-output", "/restore/object-location-map.json"]
    docker(*args)
    mapping = json.loads((output / "object-location-map.json").read_text(encoding="utf-8"))
    manifest = json.loads((snapshot / "data" / "manifest.json").read_text(encoding="utf-8"))
    missing = sum(bool(row.get("missing")) for row in manifest["objects"])
    return values, {"objects_verified": len(mapping) - missing, "missing_optional_references": missing, "source_unchanged": True, "target_configuration_explicit": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    export = sub.add_parser("export")
    export.add_argument("--project", required=True)
    export.add_argument("--output", required=True, type=Path)
    remap = sub.add_parser("apply-map")
    remap.add_argument("--postgres-container", required=True)
    remap.add_argument("--database", required=True)
    remap.add_argument("--user", default="trip")
    remap.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args()
    if args.operation == "export":
        print(json.dumps(export_snapshot(services(args.project, require_postgres=False), args.output)))
    else:
        print(json.dumps({"updated_locations": apply_location_map(args.postgres_container, args.database, args.mapping, args.user)}))


if __name__ == "__main__":
    main()
