"""One-time backup-backed enrollment of the EXACT stopped existing data mounts.

Default is inspection only. Run --execute only inside the coordinator deployment window.
No business rows, credentials or original file contents are rewritten.
"""
import argparse
import hashlib
import io
import json
import secrets
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from deployment_assets import services, docker, sql, validate_manifest, database_columns, database_summary
from prepare_java_deployment import _write
from dotenv import dotenv_values

MOUNTS = (("knowledge_uploads", "backend", "/data/uploads"),
          ("chroma", "agent", "/app/data/chroma"),
          ("agent-runtime", "agent", "/app/runtime"))


def tar_inventory(stream, identity):
    """Hash file streams without buffering an archive or a large file in memory.

    Only the root marker is optional metadata. Its contents must be the same
    dataset ID; a marker never exempts any other file from content comparison.
    """
    inventory, seen = {}, set()
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe persistent archive path")
            name = str(path)
            if name == ".":
                if not member.isdir():
                    raise ValueError("Invalid archive root")
                continue
            if name in seen:
                raise ValueError("Duplicate persistent archive member")
            seen.add(name)
            if member.isdir():
                inventory[name] = {"kind": "directory"}
                continue
            if not member.isfile():
                raise ValueError("Persistent archive links and special files require explicit handling")
            payload = archive.extractfile(member)
            if name == ".dataset-id":
                if member.size > 256 or payload.read(257).decode("utf-8").strip() != identity:
                    raise ValueError("Existing persistent marker belongs to another dataset")
                continue
            value = hashlib.sha256()
            size = 0
            for block in iter(lambda: payload.read(1024 * 1024), b""):
                size += len(block)
                value.update(block)
            if size != member.size:
                raise ValueError("Persistent archive file is truncated")
            inventory[name] = {"kind": "file", "size": member.size, "sha256": value.hexdigest()}
    return inventory


def actual_inventory(container, target, identity):
    # deployment_assets.docker() captures stdout, unsuitable for large archives.
    # Keep stderr private and return generic errors rather than file contents.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(["docker", "cp", f"{container}:{target}/.", "-"],
                                   stdout=subprocess.PIPE, stderr=errors)
        try:
            inventory = tar_inventory(process.stdout, identity)
            if process.wait() != 0:
                raise RuntimeError("Cannot read persistent mount inventory")
            return inventory
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()


def verify_file_contents(deployed, manifest, backup, identity):
    for name, service, target in MOUNTS:
        mount = next((mount for mount in deployed[service]["Mounts"] if mount["Destination"] == target), None)
        expected = manifest["mounts"][name]
        if mount is None or (mount.get("Name") or mount["Source"]) != expected["source"] or mount["Type"] != expected["type"] or expected["destination"] != target:
            raise ValueError("Backup and actual persistent mount identity differ")
        archive_name = expected["archive"]
        archive_path = (backup / archive_name).resolve(strict=True)
        if not archive_path.is_relative_to(backup.resolve()) or archive_name not in manifest["sha256"]:
            raise ValueError("Persistent archive is not protected by the backup manifest")
        with archive_path.open("rb") as stream:
            expected_files = tar_inventory(stream, identity)
        current_files = actual_inventory(deployed[service]["Id"], target, identity)
        if expected_files != current_files:
            raise ValueError("Persistent mount contents differ from the verified backup: " + name)


def verify_database_snapshot(postgres, manifest, backup):
    if not postgres["State"]["Running"]:
        raise RuntimeError("PostgreSQL must be running for read-only enrollment verification")
    values = dict(value.split("=", 1) for value in postgres["Config"]["Env"] if "=" in value)
    user = values.get("POSTGRES_USER", "trip")
    database = values.get("POSTGRES_DB", user)
    if database != manifest.get("database") or user != manifest.get("database_user"):
        raise ValueError("Backup and actual PostgreSQL database or user differ")
    required = {"database.dump", "database-summary.json", "database-columns.json"}
    if not required <= manifest["sha256"].keys():
        raise ValueError("Enrollment requires a complete database summary and original-column manifest")
    columns = json.loads((backup / "database-columns.json").read_text(encoding="utf-8"))
    expected = json.loads((backup / "database-summary.json").read_text(encoding="utf-8"))
    if not isinstance(columns, dict) or not columns or set(columns) != set(expected):
        raise ValueError("Backup database summary and column inventory disagree")
    actual_columns = database_columns(postgres["Id"], database, user)
    for table, names in columns.items():
        if not isinstance(names, list) or not names or table not in actual_columns or not set(names) <= set(actual_columns[table]):
            raise ValueError("Actual PostgreSQL lacks original backup tables or columns")
    # Compare the original columns, so an explicitly added schema column cannot
    # hide a changed business row and a historical table is never silently lost.
    if database_summary(postgres["Id"], database, user, columns=columns) != expected:
        raise ValueError("Actual PostgreSQL original business content differs from the verified backup")
    pg_mount = next((mount for mount in postgres["Mounts"] if mount["Destination"] == "/var/lib/postgresql/data"), None)
    if pg_mount is None:
        raise ValueError("Actual PostgreSQL has no persistent data mount")
    identity = {"type": pg_mount["Type"], "source": pg_mount.get("Name") or pg_mount["Source"], "destination": pg_mount["Destination"]}
    if manifest.get("postgres_mount") is not None and manifest["postgres_mount"] != identity:
        raise ValueError("Backup and actual PostgreSQL persistent mount identity differ")
    return database, user, {
        "original_tables_and_columns_content_match": True,
        "database_name_and_user_match": True,
        "postgres_mount_identity_compared": manifest.get("postgres_mount") is not None,
        "postgres_mount_evidence": "manifest_identity_and_original_content" if manifest.get("postgres_mount") is not None else "legacy_manifest_missing_mount_identity; original_content_comparison_only",
        "extra_tables_not_in_original_backup": sorted(set(actual_columns) - set(columns)),
        "extra_columns_not_in_original_backup": {table: sorted(set(actual_columns[table]) - set(names)) for table, names in columns.items() if set(actual_columns[table]) - set(names)},
    }

def marker_bytes(identity):
    data=(identity+"\n").encode(); stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode="w") as archive:
        entry=tarfile.TarInfo(".dataset-id");entry.size=len(data);entry.mode=0o444
        archive.addfile(entry,io.BytesIO(data))
    return stream.getvalue()

def enroll(project, backup, runtime, execute=False):
    backup = backup.resolve(strict=True)
    manifest=validate_manifest(backup)
    if manifest.get("project")!=project:raise ValueError("Backup belongs to another project")
    deployed=services(project)
    if any(deployed[s]["State"]["Running"] for s in ("backend","agent")):
        raise RuntimeError("Stop writers in the authorized maintenance window before enrollment")
    business={k:v for k,v in dotenv_values(runtime/"business.env").items() if v is not None}
    agent={k:v for k,v in dotenv_values(runtime/"agent.env").items() if v is not None}
    identity=business.get("DATASET_ID") or secrets.token_hex(16)
    if agent.get("DATASET_ID") not in (None,"",identity):raise ValueError("Runtime dataset identities disagree")
    if not identity.isascii() or not identity.isalnum() or len(identity)!=32:raise ValueError("Expected a generated 32-character dataset identity")
    pg=deployed["postgres"]
    database,user,database_evidence=verify_database_snapshot(pg,manifest,backup)
    verify_file_contents(deployed,manifest,backup,identity)
    report={"project":project,"dataset_id":identity,"backup_verified":True,"mounts_match":True,
            "persistent_file_inventory_match":True,"database_evidence":database_evidence,"executed":execute}
    if not execute:return report
    # This schema is identical to V11. IF NOT EXISTS permits the subsequent Flyway migration.
    sql(pg["Id"],database,"CREATE TABLE IF NOT EXISTS deployment_identity (id INTEGER PRIMARY KEY CHECK(id=1),dataset_id VARCHAR(64) NOT NULL,enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now())",user)
    current=sql(pg["Id"],database,"SELECT dataset_id FROM deployment_identity WHERE id=1",user)
    if current and current!=identity:raise ValueError("Database belongs to a different dataset")
    for service,target in (("backend","/data/uploads"),("agent","/app/data/chroma"),("agent","/app/runtime")):
        try:
            raw=docker("cp",f"{deployed[service]['Id']}:{target}/.dataset-id","-")
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive: existing=archive.extractfile(archive.getmembers()[0]).read().decode().strip()
            if existing!=identity:raise ValueError("Existing mount marker mismatch")
        except RuntimeError:
            docker("cp","-",f"{deployed[service]['Id']}:{target}",data=marker_bytes(identity))
    sql(pg["Id"],database,f"INSERT INTO deployment_identity(id,dataset_id) VALUES(1,'{identity}') ON CONFLICT(id) DO NOTHING",user)
    for values in (business,agent):values.update(DATASET_ID=identity,DATASET_ENROLL_NEW="false")
    _write(runtime/"business.env",business);_write(runtime/"agent.env",agent)
    return report

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--project",default="langchain-trip-planner");p.add_argument("--backup",type=Path,required=True);p.add_argument("--runtime",type=Path,required=True);p.add_argument("--execute",action="store_true")
    a=p.parse_args();print(json.dumps(enroll(a.project,a.backup,a.runtime,a.execute)))
