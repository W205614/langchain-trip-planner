"""Recover the isolated validation stack into a new database and temporary index directory.

No production configuration is read. The fixed Compose project is intentionally not configurable.
"""
import argparse
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-p", "trip-validation", "-f", str(ROOT / "docker-compose.validation.yml")]


def command(*args, data=None):
    completed = subprocess.run(COMPOSE + list(args), cwd=ROOT, input=data, capture_output=True, check=True)
    return completed.stdout


def sql(database, query):
    return command("exec", "-T", "postgres", "psql", "-U", "trip", "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query)


def validate_archive(content, expected):
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Archive checksum mismatch; refusing restoration")


def run(output):
    started = time.monotonic()
    target = "trip_restore_" + uuid.uuid4().hex[:12]
    data_dir = "/tmp/trip-restore-" + uuid.uuid4().hex[:12]
    # Seed one already-extracted public document; reconstruction must not invoke a vision model.
    sql("trip", "INSERT INTO knowledge_documents (submitted_by,city,title,original_filename,stored_path,sha256,media_type,source_tier,status,review_note,page_count,source_text,version) "
        "VALUES (1,'fixture','recovery-source','fixture.jpg','fixture.jpg','fixture','image/jpeg','reviewed','published','',1,'Verified recovery fixture source',1)")
    command("exec", "-T", "backend", "python", "-c",
        "from pathlib import Path; p=Path('/app/data/knowledge_uploads'); p.mkdir(parents=True, exist_ok=True); (p/'fixture.jpg').write_bytes(b'recovery-fixture')")
    files = command("exec", "-T", "backend", "tar", "-C", "/app/data", "-cf", "-", "knowledge_uploads")
    command("stop", "backend")
    created = False
    try:
        dump = command("exec", "-T", "postgres", "pg_dump", "-U", "trip", "-d", "trip", "-Fc")
        checksum = hashlib.sha256(dump).hexdigest()
        validate_archive(dump, checksum)
        try:
            validate_archive(dump[:-1], checksum)
        except ValueError:
            corrupt_rejected = True
        else:
            raise AssertionError("Corrupt archive accepted")
        tables = ("users", "trip_records", "trip_tasks", "rag_sync_jobs", "knowledge_documents", "knowledge_ingest_jobs", "user_travel_preferences")
        def digest(database, table):
            return sql(database, f"SELECT count(*) || ':' || md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY id),'')) FROM {table} t").decode().strip()
        expected = {table: digest("trip", table) for table in tables}
        command("exec", "-T", "postgres", "createdb", "-U", "trip", target)
        created = True
        command("exec", "-T", "postgres", "pg_restore", "-U", "trip", "-d", target, "--no-owner", "--exit-on-error", data=dump)
        assert expected == {table: digest(target, table) for table in tables}
        extraction = """import io, os, pathlib, runpy, sys, tarfile
root = pathlib.Path(os.environ['DATA_DIR']).resolve()
assert str(root).startswith('/tmp/trip-restore-')
content = sys.stdin.buffer.read()
import hashlib
assert hashlib.sha256(content).hexdigest() == os.environ['ARCHIVE_SHA256']
with tarfile.open(fileobj=io.BytesIO(content)) as archive:
    for member in archive.getmembers():
        path = (root / member.name).resolve()
        if not path.is_relative_to(root) or member.issym() or member.islnk():
            raise ValueError('Unsafe archive path')
        if member.isdir():
            path.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            path.parent.mkdir(parents=True, exist_ok=True)
            expected = archive.extractfile(member).read()
            path.write_bytes(expected)
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(expected).digest()
runpy.run_path('scripts/verify_recovery.py', run_name='__main__')
"""
        verification = command("run", "--rm", "-T", "--no-deps", "-e", f"DATABASE_URL=postgresql+psycopg://trip:validation-only@postgres:5432/{target}",
            "-e", f"DATA_DIR={data_dir}", "-e", f"CHROMA_DIR={data_dir}/chroma", "-e", f"UPLOAD_DIR={data_dir}/knowledge_uploads",
            "-e", "ARCHIVE_SHA256="+hashlib.sha256(files).hexdigest(), "backend", "python", "-c", extraction, data=files)
        report = {"mode": "isolated_postgres_and_deterministic_index", "tables_verified": len(tables),
            "row_digests": expected, "archive_sha256": checksum, "uploads_sha256": hashlib.sha256(files).hexdigest(),
            "corrupt_archive_rejected": corrupt_rejected, "index_verification": verification.decode().strip(),
            "seconds": round(time.monotonic()-started, 3), "production_data_touched": False}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"tables_verified": len(tables), "corrupt_archive_rejected": True, "seconds": report["seconds"]}))
    finally:
        if created:
            command("exec", "-T", "postgres", "dropdb", "-U", "trip", "--force", target)
        command("start", "backend")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output)
