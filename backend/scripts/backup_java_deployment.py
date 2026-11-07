"""Maintenance backup of the Java stack. Stop backend and agent before running."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import tarfile
from deployment_assets import services, docker, digest, database_summary, database_columns
from backup_database import DatabaseClient, private_directory

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-f",str(ROOT/"docker-compose.yml")]


def backup(output, project="langchain-trip-planner", archive_images=False, private_config_dir=None):
    """Version two uses container-side archives for bind mounts AND named volumes."""
    deployed = services(project, require_postgres=False)
    for service in ("backend", "agent"):
        if deployed[service]["State"]["Running"]:
            raise RuntimeError("Stop backend and agent before a consistent backup")
    output = output.resolve()
    if output == ROOT or (ROOT in output.parents and not output.is_relative_to(ROOT / "backups")):
        raise ValueError("Use a fresh external directory or the ignored backups directory")
    if output.exists():
        raise ValueError("Backup target must not already exist")
    if private_config_dir is not None:
        private_config_dir=private_config_dir.resolve(strict=True)
        if not private_config_dir.is_dir() or output.is_relative_to(private_config_dir):
            raise ValueError("Private configuration must be a separate directory")
        if any(path.is_symlink() for path in private_config_dir.rglob("*")):
            raise ValueError("Private configuration snapshot does not follow symbolic links")
    # Validate the effective JDBC source before creating a partial backup directory.
    client = DatabaseClient(deployed, output / "database-client")
    private_directory(output)
    with client:
        return _backup_connected(output, project, archive_images, private_config_dir, deployed, client)


def _backup_connected(output, project, archive_images, private_config_dir, deployed, client):
    database, user = client.target["database"], client.target["user"]
    pg_mount = None
    if client.target["local"]:
        pg_mount = next((m for m in deployed["postgres"]["Mounts"] if m["Destination"] == "/var/lib/postgresql/data"), None)
        if pg_mount is None:
            raise RuntimeError("Missing PostgreSQL persistent data mount")
    with (output / "database.dump").open("wb") as stream:
        subprocess.run(["docker", "exec", client.container, "pg_dump", "-U", user, "-d", database,
                        "-Fc", "--no-owner"], stdout=stream, stderr=subprocess.PIPE, check=True)
    summary = database_summary(client.container, database, user)
    (output / "database-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "database-columns.json").write_text(json.dumps(database_columns(client.container,database,user),indent=2),encoding="utf-8")
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
    private_directory(runtime)
    for name, container in deployed.items():
        # Private backup only. Preserve effective environment without printing credentials.
        (runtime / f"{name}.json").write_text(json.dumps(container["Config"]["Env"]), encoding="utf-8")
    private_configuration=None
    if private_config_dir is not None:
        with tarfile.open(output/"private-configuration.tar","w") as archive:
            archive.add(private_config_dir,arcname="private-configuration")
        private_configuration={"archive":"private-configuration.tar",
            "files":sum(path.is_file() for path in private_config_dir.rglob("*"))}
    from storage_objects import export_snapshot
    object_snapshot=export_snapshot(deployed,output/"objects",client)
    images = {name: item["Image"] for name, item in deployed.items()}
    # A logical external dump restores into this known PostgreSQL17 server image,
    # never into an unrelated local decoy container or the source cloud service.
    images["postgres"] = client.image
    if archive_images:
        docker("image", "save", "-o", output / "images.tar", *sorted(set(images.values())))
    manifest = {"schema": "Java/Flyway-v2", "created_at": datetime.now(timezone.utc).isoformat(),
                "project": project, "database": database, "database_user": user, "database_provenance":client.provenance, "images": images,
                "mounts": mounts, "object_snapshot":object_snapshot,"private_configuration":private_configuration, "compose_files": deployed["backend"]["Config"]["Labels"].get("com.docker.compose.project.config_files", ""),
                "postgres_mount": {"type": pg_mount["Type"], "source": pg_mount.get("Name") or pg_mount["Source"], "destination": pg_mount["Destination"]} if pg_mount else None,
                "sha256": {f.relative_to(output).as_posix(): digest(f) for f in output.rglob("*") if f.is_file()}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"backup": str(output), "files": len(manifest["sha256"]), "tables": len(summary),
                      "schema": manifest["schema"], "images_archived": archive_images}))
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--project",default="langchain-trip-planner")
    parser.add_argument("--archive-images",action="store_true")
    parser.add_argument("--private-config-dir",type=Path,help="Also archive the selected private runtime/alert configuration directory")
    args=parser.parse_args();backup(args.output,args.project,args.archive_images,args.private_config_dir)
