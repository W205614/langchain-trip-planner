"""Restore Java backup into a new isolated database and external file directory.

Never overwrites a running database/data directory. Keeps the clone for inspection.
Run from the daily Docker host. Switching service configuration is a separate step.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
from uuid import uuid4
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-f",str(ROOT/"docker-compose.yml")]


def restore(backup,output):
    backup=backup.resolve();output=output.resolve()
    if output==ROOT or ROOT in output.parents or output==backup or backup in output.parents:
        raise ValueError("Restore files into a fresh directory outside repository and backup")
    manifest=json.loads((backup/"manifest.json").read_text(encoding="utf-8"))
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

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--backup",required=True,type=Path);parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args();restore(args.backup,args.output)
