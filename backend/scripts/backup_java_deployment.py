"""Maintenance backup of the Java stack. Stop backend and agent before running."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
from deployment_assets import services, docker, digest, database_summary, database_columns

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-f",str(ROOT/"docker-compose.yml")]


def backup(output, project="langchain-trip-planner", archive_images=False):
    """Version two uses container-side archives for bind mounts AND named volumes."""
    deployed = services(project)
    for service in ("backend", "agent"):
        if deployed[service]["State"]["Running"]:
            raise RuntimeError("Stop backend and agent before a consistent backup")
    output = output.resolve()
    if output == ROOT or (ROOT in output.parents and not output.is_relative_to(ROOT / "backups")):
        raise ValueError("Use a fresh external directory or the ignored backups directory")
    if output.exists():
        raise ValueError("Backup target must not already exist")
    output.mkdir(parents=True)
    postgres = deployed["postgres"]
    pg_mount = next((mount for mount in postgres["Mounts"] if mount["Destination"] == "/var/lib/postgresql/data"), None)
    if pg_mount is None:
        raise RuntimeError("Missing PostgreSQL persistent data mount")
    pg_env = dict(value.split("=", 1) for value in postgres["Config"]["Env"] if "=" in value)
    user = pg_env.get("POSTGRES_USER", "trip")
    database = pg_env.get("POSTGRES_DB", user)
    with (output / "database.dump").open("wb") as stream:
        subprocess.run(["docker", "exec", postgres["Id"], "pg_dump", "-U", user, "-d", database,
                        "-Fc", "--no-owner"], stdout=stream, stderr=subprocess.PIPE, check=True)
    summary = database_summary(postgres["Id"], database, user)
    (output / "database-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "database-columns.json").write_text(json.dumps(database_columns(postgres["Id"],database,user),indent=2),encoding="utf-8")
    mounts = {}
    for name, service, destination in (("knowledge_uploads", "backend", "/data/uploads"),
                                      ("chroma", "agent", "/app/data/chroma"),
                                      ("agent-runtime", "agent", "/app/runtime")):
        container = deployed[service]
        mount = next((m for m in container["Mounts"] if m["Destination"] == destination), None)
        if mount is None:
            raise RuntimeError(f"Missing expected persistent mount: {destination}")
        with (output / f"{name}.tar").open("wb") as stream:
            subprocess.run(["docker", "cp", f"{container['Id']}:{destination}/.", "-"],
                           stdout=stream, stderr=subprocess.PIPE, check=True)
        mounts[name] = {"service": service, "destination": destination, "type": mount["Type"],
                        "source": mount.get("Name") or mount["Source"], "archive": f"{name}.tar"}
    runtime = output / "runtime"
    runtime.mkdir()
    for name, container in deployed.items():
        # Private backup only. Preserve effective environment without printing credentials.
        (runtime / f"{name}.json").write_text(json.dumps(container["Config"]["Env"]), encoding="utf-8")
    images = {name: item["Image"] for name, item in deployed.items()}
    if archive_images:
        docker("image", "save", "-o", output / "images.tar", *sorted(set(images.values())))
    manifest = {"schema": "Java/Flyway-v2", "created_at": datetime.now(timezone.utc).isoformat(),
                "project": project, "database": database, "database_user": user, "images": images,
                "mounts": mounts, "compose_files": deployed["backend"]["Config"]["Labels"].get("com.docker.compose.project.config_files", ""),
                "postgres_mount": {"type": pg_mount["Type"], "source": pg_mount.get("Name") or pg_mount["Source"], "destination": pg_mount["Destination"]},
                "sha256": {f.relative_to(output).as_posix(): digest(f) for f in output.rglob("*") if f.is_file()}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"backup": str(output), "files": len(manifest["sha256"]), "tables": len(summary),
                      "schema": manifest["schema"], "images_archived": archive_images}))
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--project",default="langchain-trip-planner")
    parser.add_argument("--archive-images",action="store_true")
    args=parser.parse_args();backup(args.output,args.project,args.archive_images)
