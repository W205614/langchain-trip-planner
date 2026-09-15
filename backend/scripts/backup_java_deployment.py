"""Maintenance backup of the Java stack. Stop backend and agent before running."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-f",str(ROOT/"docker-compose.yml")]


def backup(output):
    output=output.resolve()
    if output==ROOT or ROOT in output.parents:
        raise ValueError("Choose a new backup directory outside the repository")
    images={}
    for service in ("backend","agent","frontend"):
        identity=subprocess.check_output(COMPOSE+["ps","-a","-q",service],cwd=ROOT,text=True).strip()
        if not identity:raise RuntimeError("Expected deployed container is missing: "+service)
        info=json.loads(subprocess.check_output(["docker","inspect",identity]))[0]
        if service in {"backend","agent"} and info["State"]["Running"]:
            raise RuntimeError("Stop backend and agent before backup")
        images[service]=info["Image"]
    output.mkdir(parents=True,exist_ok=False)
    dump=subprocess.check_output(COMPOSE+["exec","-T","postgres","pg_dump","-U","trip","-d","trip_java","-Fc","--no-owner"],cwd=ROOT)
    (output/"database.dump").write_bytes(dump)
    with tarfile.open(output/"agent-and-uploads.tar.gz","w:gz") as archive:
        for name in ("knowledge_uploads","chroma","agent-runtime"):
            source=ROOT/"backend/data"/name
            if source.exists():archive.add(source,arcname=name)
    shutil.copytree(ROOT/"deploy/runtime",output/"runtime")
    for name in ("docker-compose.yml","docker-compose.production.yml"):
        shutil.copy2(ROOT/name,output/name)
    hashes={f.relative_to(output).as_posix():hashlib.sha256(f.read_bytes()).hexdigest() for f in output.rglob("*") if f.is_file()}
    manifest={"created_at":datetime.now(timezone.utc).isoformat(),"schema":"Java/Flyway","images":images,"sha256":hashes}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps({"backup":str(output),"files":len(hashes),"restore_target":"new PostgreSQL database only"}))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",required=True,type=Path)
    backup(parser.parse_args().output)
