"""Read actual Docker assets; never infer data mounts from a Compose default."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def docker(*args, data=None):
    result = subprocess.run(["docker", *map(str, args)], input=data, capture_output=True)
    if result.returncode:
        # Docker arguments may include paths, but provider credentials are never arguments.
        raise RuntimeError("Docker operation failed: " + result.stderr.decode("utf-8", errors="replace")[-2000:])
    return result.stdout


def inspect(identity):
    return json.loads(docker("inspect", identity))[0]


def services(project):
    ids = docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").decode().split()
    found = {}
    for identity in ids:
        item = inspect(identity)
        labels = item["Config"].get("Labels") or {}
        name = labels.get("com.docker.compose.service")
        # Images made from an old Compose container can inherit project/service
        # labels. Require positive container-level Compose ownership evidence;
        # an ad-hoc test container is not a fifth daily service to stop or back up.
        managed = labels.get("com.docker.compose.oneoff", "").lower() == "false" and bool(labels.get("com.docker.compose.project.config_files"))
        if name in {"backend", "agent", "postgres", "frontend"} and managed:
            if name in found:
                raise RuntimeError(f"Multiple {name} containers; select an unambiguous project")
            found[name] = item
    if set(found) != {"backend", "agent", "postgres", "frontend"}:
        raise RuntimeError("Expected exactly the four application services")
    return found


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def sql(container, database, query, user="trip"):
    return docker("exec", container, "psql", "-U", user, "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query).decode().strip()


def database_columns(container, database, user="trip"):
    value = sql(container, database, "SELECT json_object_agg(table_name,columns) FROM (SELECT table_name,json_agg(column_name ORDER BY ordinal_position) AS columns FROM information_schema.columns WHERE table_schema='public' GROUP BY table_name) s", user)
    return json.loads(value)


def database_summary(container, database, user="trip", columns=None):
    tables = sorted(columns) if columns else sql(container, database, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename", user).splitlines()
    result = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        # Order whole row representations, including tables without an id column.
        source = quoted
        if columns:
            names = ','.join('"'+column.replace('"','""')+'"' for column in columns[table])
            source = f"(SELECT {names} FROM {quoted})"
        query = f"SELECT count(*) || ':' || md5(COALESCE(string_agg(row_to_json(t)::text,'' ORDER BY row_to_json(t)::text),'')) FROM {source} t"
        result[table] = sql(container, database, query, user)
    return result


def extract_safe(archive, target):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as stream:
        for item in stream:
            destination = (target / item.name).resolve()
            if not destination.is_relative_to(target) or not (item.isfile() or item.isdir()):
                raise ValueError("Unsafe archive member")
        stream.extractall(target, filter="data")


def validate_manifest(folder):
    folder = Path(folder).resolve(strict=True)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    hashes = manifest.get("sha256", {})
    if "database.dump" not in hashes or not hashes:
        raise ValueError("Incomplete backup manifest")
    for name, expected in hashes.items():
        item = (folder / name).resolve(strict=True)
        if not item.is_relative_to(folder) or not item.is_file() or digest(item) != expected:
            raise ValueError("Backup checksum mismatch")
    return manifest
