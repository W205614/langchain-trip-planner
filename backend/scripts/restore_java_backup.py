"""Restore Java backup into a new isolated database and external file directory.

Never overwrites a running database/data directory. Keeps the clone for inspection.
Run from the daily Docker host. Switching service configuration is a separate step.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
from uuid import uuid4
from dotenv import dotenv_values
from deployment_assets import validate_manifest, extract_safe, docker, database_summary, sql

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-f",str(ROOT/"docker-compose.yml")]


def restore(backup,output,candidate_image=None,network_subnet=None):
    backup=backup.resolve();output=output.resolve()
    if output==ROOT or ROOT in output.parents or output==backup or backup in output.parents:
        raise ValueError("Restore files into a fresh directory outside repository and backup")
    manifest=json.loads((backup/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema")=="Java/Flyway-v2":
        return restore_v2(backup,output,candidate_image,network_subnet)
    if candidate_image:
        raise ValueError("Candidate upgrade verification requires a v2 backup")
    if manifest.get("schema")!="Java/Flyway":raise ValueError("Not a Java backup")
    for name,digest in manifest["sha256"].items():
        file=(backup/name).resolve()
        if backup not in file.parents or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise ValueError("Checksum mismatch")
    output.mkdir(parents=True,exist_ok=False)
    with tarfile.open(backup/"agent-and-uploads.tar.gz") as archive:
        for member in archive:
            path=(output/member.name).resolve()
            if output not in path.parents or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe archive member")
        archive.extractall(output,filter="data")
    database="java_restore_"+uuid4().hex[:12]
    container="trip-java-restore-"+uuid4().hex[:12]
    subprocess.run(COMPOSE+["exec","-T","postgres","createdb","-U","trip",database],cwd=ROOT,check=True,capture_output=True)
    subprocess.run(COMPOSE+["exec","-T","postgres","pg_restore","-U","trip","-d",database,"--no-owner","--exit-on-error"],
        input=(backup/"database.dump").read_bytes(),cwd=ROOT,check=True,capture_output=True)
    # Docker CLI --env-file does not unquote Compose env_file values.
    environment={**os.environ,**dotenv_values(backup/"runtime/business.env")}
    private_keys=list(dotenv_values(backup/"runtime/business.env"))
    forwarded=[value for key in private_keys for value in ("-e",key)]
    subprocess.run(["docker","run","-d","--name",container,"--network","langchain-trip-planner_default",
        *forwarded,"-e","WORKERS_ENABLED=false",
        "-e",f"JDBC_DATABASE_URL=jdbc:postgresql://postgres:5432/{database}",
        "-v",str(output/"knowledge_uploads")+":/data/uploads:ro",manifest["images"]["backend"]],env=environment,check=True,capture_output=True)
    for _ in range(60):
        result=subprocess.run(["docker","exec",container,"curl","-fsS","http://localhost:9000/readyz"],capture_output=True)
        if result.returncode==0:break
        time.sleep(.5)
    else:raise RuntimeError("Restored Java failed readiness; clone and logs retained")
    report={"checksums_verified":True,"database":database,"container":container,"files":str(output),
        "restored_java_ready":True,"production_configuration_changed":False}
    (output/"restore-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


def restore_v2(backup,output,candidate_image=None,network_subnet=None):
    manifest=validate_manifest(backup)
    output.mkdir(parents=True,exist_ok=False)
    for name, mount in manifest["mounts"].items():
        extract_safe(backup/mount["archive"],output/name)
    suffix=uuid4().hex[:12]
    postgres="trip-final-restore-pg-"+suffix
    backend="trip-final-restore-backend-"+suffix
    network="trip-final-restore-"+suffix
    label="codex.task=trip-planner-final"
    network_args=[]
    if network_subnet:
        requested=ipaddress.ip_network(network_subnet,strict=True)
        identities=docker("network","ls","-q").decode().split()
        networks=json.loads(docker("network","inspect",*identities)) if identities else []
        for existing in networks:
            for config in existing.get("IPAM",{}).get("Config") or []:
                if config.get("Subnet") and requested.overlaps(ipaddress.ip_network(config["Subnet"])):
                    raise ValueError("Restore subnet overlaps an existing Docker network")
        network_args=["--subnet",str(requested)]
    docker("network","create","--label",label,*network_args,network)
    docker("run","-d","--name",postgres,"--label",label,"--label","com.docker.compose.project=trip-final-restore",
           "--label","com.docker.compose.service=restore-postgres","--network",network,"--tmpfs","/var/lib/postgresql/data:rw,size=512m",
           "-e","POSTGRES_USER=trip","-e","POSTGRES_PASSWORD=restore-isolated-only",
           "-e","POSTGRES_DB=trip_restore",manifest["images"]["postgres"])
    for _ in range(120):
        ready=subprocess.run(["docker","exec",postgres,"pg_isready","-U","trip"],capture_output=True)
        if ready.returncode==0:break
        time.sleep(.5)
    else:raise RuntimeError("Isolated PostgreSQL did not become ready")
    with (backup/"database.dump").open("rb") as stream:
        subprocess.run(["docker","exec","-i",postgres,"pg_restore","-U","trip","-d","trip_restore",
                        "--no-owner","--no-acl","--exit-on-error"],stdin=stream,capture_output=True,check=True)
    expected=json.loads((backup/"database-summary.json").read_text(encoding="utf-8"))
    actual=database_summary(postgres,"trip_restore")
    if expected!=actual:raise RuntimeError("Restored table contents differ; artifacts retained")
    previous_history=sql(postgres,"trip_restore","SELECT json_agg(s ORDER BY installed_rank) FROM flyway_schema_history s")
    environment=dict(os.environ)
    values=dict(value.split("=",1) for value in json.loads((backup/"runtime/backend.json").read_text()) if "=" in value)
    values.update(JDBC_DATABASE_URL=f"jdbc:postgresql://{postgres}:5432/trip_restore",POSTGRES_USER="trip",
                  POSTGRES_PASSWORD="restore-isolated-only",WORKERS_ENABLED="false",UPLOAD_DIR="/data/uploads")
    environment.update(values)
    args=["docker","run","-d","--name",backend,"--label",label,"--label","com.docker.compose.project=trip-final-restore",
          "--label","com.docker.compose.service=restore-backend","--network",network,
          "-v",str(output/"knowledge_uploads")+":/data/uploads:ro"]
    args.extend(value for key in values for value in ("-e",key))
    subprocess.run(args+[manifest["images"]["backend"]],env=environment,capture_output=True,check=True)
    for _ in range(120):
        ready=subprocess.run(["docker","exec",backend,"curl","-fsS","http://localhost:9000/readyz"],capture_output=True)
        if ready.returncode==0:break
        time.sleep(.5)
    else:raise RuntimeError("Restored backend not ready; diagnostics retained")
    report={"checksums_verified":True,"tables_verified":len(expected),"rows_verified":sum(int(v.split(':')[0]) for v in expected.values()),
            "restored_java_ready":True,"production_configuration_changed":False,
            "containers":[postgres,backend],"network":network,"files":str(output),"mount_archives":list(manifest["mounts"])}
    if candidate_image:
        columns_file=backup/"database-columns.json"
        if not columns_file.is_file():
            raise RuntimeError("Upgrade verification requires original column metadata")
        columns=json.loads(columns_file.read_text(encoding="utf-8"))
        columns.pop("flyway_schema_history",None)
        docker("rm","-f",backend)
        subprocess.run(args+[candidate_image],env=environment,capture_output=True,check=True)
        for _ in range(120):
            ready=subprocess.run(["docker","exec",backend,"curl","-fsS","http://localhost:9000/readyz"],capture_output=True)
            if ready.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError("Candidate migration/readiness failed; isolated clone retained")
        upgraded=database_summary(postgres,"trip_restore",columns=columns)
        old_tables={k:v for k,v in expected.items() if k!="flyway_schema_history"}
        if upgraded!=old_tables:
            raise RuntimeError("Candidate upgrade changed existing business rows; clone retained")
        history=json.loads(previous_history)
        highest=max(row["installed_rank"] for row in history)
        after_history=sql(postgres,"trip_restore",f"SELECT json_agg(s ORDER BY installed_rank) FROM flyway_schema_history s WHERE installed_rank<={highest}")
        if json.loads(after_history)!=history:
            raise RuntimeError("Published migration history changed")
        report["upgrade"]={"candidate_image_id":json.loads(docker("image","inspect",candidate_image))[0]["Id"],
                           "candidate_ready":True,"original_business_tables_unchanged":len(columns),
                           "original_migrations_unchanged":len(history),
                           "schema_version":sql(postgres,"trip_restore","SELECT version FROM flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1")}
    (output/"restore-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--backup",required=True,type=Path);parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--candidate-image",help="Verify a candidate migration only in the restored clone")
    parser.add_argument("--network-subnet",help="Optional unused CIDR when Docker's automatic address pools are exhausted")
    args=parser.parse_args();restore(args.backup,args.output,args.candidate_image,args.network_subnet)
