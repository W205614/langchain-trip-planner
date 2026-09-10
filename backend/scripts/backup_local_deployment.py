"""Back up the stopped local Compose deployment without printing credentials.

This command only reads the existing container configuration and data. The backup
contains private data; keep it outside source control and restrict local access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from urllib.parse import urlsplit, unquote


def main(output):
    info = json.loads(subprocess.check_output(["docker", "inspect", "langchain-trip-planner-backend-1"]))[0]
    if info["State"]["Running"]:
        raise RuntimeError("Stop the original backend before taking a maintenance backup")
    source = Path(next(m["Source"] for m in info["Mounts"] if m["Destination"] == "/app/data")).resolve()
    output = output.resolve()
    if output.is_relative_to(source) or output.exists():
        raise RuntimeError("Choose a new backup directory outside the application data directory")
    env = dict(entry.split("=", 1) for entry in info["Config"]["Env"] if "=" in entry)
    url = urlsplit(env["DATABASE_URL"])
    if url.scheme != "postgresql+psycopg":
        raise RuntimeError("This local deployment backup expects PostgreSQL")
    output.mkdir(parents=True)
    process_env = os.environ | {"PGPASSWORD": unquote(url.password or "")}
    result = subprocess.run(["docker", "run", "--rm", "-e", "PGPASSWORD", "postgres:17.6-alpine",
        "pg_dump", "-h", url.hostname, "-p", str(url.port or 5432), "-U", unquote(url.username or ""),
        "-Fc", "--no-owner", url.path.lstrip("/")], env=process_env, capture_output=True)
    if result.returncode:
        raise RuntimeError("Database backup failed; original deployment was not changed")
    (output / "database.dump").write_bytes(result.stdout)
    with tarfile.open(output / "application-data.tar.gz", "w:gz") as archive:
        archive.add(source, arcname="data", filter=lambda item: None if "/backups/" in item.name or item.name == "data/backups" else item)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    (output / "manifest.json").write_text(json.dumps({"backend_image": info["Image"], "sha256": hashes}, indent=2))
    print(json.dumps({"backup_directory": str(output), "files": list(hashes)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    main(parser.parse_args().output)
