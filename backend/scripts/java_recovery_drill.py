"""Restore an isolated Java database and uploaded files into a separate container.

Fixed test project only. Keep failed artifacts for inspection; never touch daily data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-p","trip-validation","-f",str(ROOT/"docker-compose.validation.yml")]


def compose_command(*args, data=None):
    result = subprocess.run(COMPOSE + list(args), input=data, capture_output=True)
    if result.returncode:
        # This script is fixed to fixture credentials. Keep command diagnostics
        # visible; never dump successful stdout, which may contain backup bytes.
        print(result.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        result.check_returncode()
    return result.stdout


def wait_for_service_health(command, timeout=90):
    """Portable across Compose versions; require both actual healthchecks to pass."""
    deadline = time.monotonic() + timeout
    states = {}
    while time.monotonic() < deadline:
        for service in ("backend", "agent"):
            identity = command("ps", "-a", "-q", service).decode().strip()
            if not identity:
                raise RuntimeError(f"Missing validation service: {service}")
            state = json.loads(subprocess.check_output(
                ["docker", "inspect", "--format", "{{json .State}}", identity]))
            states[service] = (state.get("Status"), state.get("Health", {}).get("Status"))
        if all(state == ("running", "healthy") for state in states.values()):
            return
        time.sleep(.5)
    raise RuntimeError(f"Validation services not healthy after recovery: {states}")


def wait_for_public_fixture(timeout=30):
    """Container started is not application ready; verify the Nginx route too."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:18080/api/validation/fixture", timeout=2) as response:
                fixture = json.load(response)
            if fixture.get("offline_fixture") is not True:
                raise RuntimeError("Restored entry is not the isolated offline fixture")
            return
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(.5)
    raise RuntimeError("Validation entry not ready after recovery") from last_error


def run(output):
    command = compose_command
    def sql(database,query):
        return command("exec","-T","postgres","psql","-U","trip","-d",database,"-At","-v","ON_ERROR_STOP=1","-c",query).decode().strip()
    target="java_restore_"+uuid4().hex[:12]
    container="trip-validation-restore-"+uuid4().hex[:12]
    tables=("users","trip_records","trip_tasks","rag_sync_jobs","knowledge_documents","knowledge_ingest_jobs","user_travel_preferences")
    def digest(database,table):
        return sql(database,f"SELECT count(*) || ':' || md5(COALESCE(string_agg(row_to_json(t)::text,'' ORDER BY id),'')) FROM {table} t")
    command("stop","backend","agent")
    created=False
    success=False
    try:
        dump=command("exec","-T","postgres","pg_dump","-U","trip","-d","trip","-Fc")
        expected={table:digest("trip",table) for table in tables}
        # Read files from a stopped source with a read-only temporary container.
        source_id=command("ps","-a","-q","backend").decode().strip()
        archive=subprocess.check_output(["docker","run","--rm","--volumes-from",source_id+":ro","--entrypoint","tar","trip-validation-backend","-C","/data","-cf","-","uploads"])
        command("exec","-T","postgres","createdb","-U","trip",target);created=True
        command("exec","-T","postgres","pg_restore","-U","trip","-d",target,"--no-owner","--exit-on-error",data=dump)
        assert expected=={table:digest(target,table) for table in tables}
        subprocess.run(["docker","run","-d","--name",container,"--network","trip-validation_default",
            "--tmpfs","/data/uploads:rw,mode=1777", "-e",f"JDBC_DATABASE_URL=jdbc:postgresql://postgres:5432/{target}",
            "-e","POSTGRES_USER=trip","-e","POSTGRES_PASSWORD=migration-fixture-only","-e","WORKERS_ENABLED=false",
            "-e","UPLOAD_DIR=/data/uploads","-e","JWT_SECRET_KEY=restore-fixture-01234567890123456789",
            "-e","INTERNAL_SERVICE_KEY=restore-fixture-internal-01234567890123456789","trip-validation-backend"],check=True,capture_output=True)
        # Archive was produced locally from our isolated upload volume, never user-supplied.
        subprocess.run(["docker","exec","-i","-u","root",container,"tar","-C","/data","-xf","-"],input=archive,check=True,capture_output=True)
        for _ in range(60):
            check=subprocess.run(["docker","exec",container,"curl","-fsS","http://localhost:9000/readyz"],capture_output=True)
            if check.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError("Restored Java service not ready")
        restored=subprocess.check_output(["docker","exec",container,"tar","-C","/data","-cf","-","uploads"])
        # Tar directory headers can differ after extraction. Compare regular file bytes by name.
        import io,tarfile
        def entries(content):
            with tarfile.open(fileobj=io.BytesIO(content)) as files:
                return {f.name:hashlib.sha256(files.extractfile(f).read()).hexdigest() for f in files if f.isfile()}
        assert entries(archive)==entries(restored)
        manifest=hashlib.sha256(dump).hexdigest()
        from recovery_drill import validate_archive
        validate_archive(dump,manifest)
        try:validate_archive(dump[:-1],manifest)
        except ValueError:pass
        else:raise AssertionError("Corrupt archive checksum accepted")
        report={"mode":"isolated_java_postgres_restore","tables_verified":len(tables),"row_digests":expected,
            "uploads_verified":len(entries(archive)),"restored_java_ready":True,"source_data_touched":False,
            "archive_sha256":manifest,"truncated_archive_checksum_rejected":True,"external_provider_calls":0}
        success=True
    finally:
        if success:
            subprocess.run(["docker","rm","-f",container],check=True,capture_output=True)
            if created:command("exec","-T","postgres","dropdb","-U","trip",target)
        else:
            print(f"Recovery artifacts retained: container={container}, database={target}")
        command("start","backend","agent")
        wait_for_service_health(command)
        wait_for_public_fixture()
    report.update(source_services_healthy=True, public_fixture_ready=True)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True)
    run(parser.parse_args().output)
