"""Offline, lossless business export/import for the Java migration.

Run from backend. Credentials come from existing settings / TARGET_DATABASE_URL,
never command-line arguments. Only an empty, Flyway-initialized target is accepted.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, inspect, text
from app.config import get_settings

TABLES=("users","user_travel_preferences","trip_records","trip_tasks","rag_sync_jobs","knowledge_documents","knowledge_ingest_jobs")
ROOT=Path(__file__).resolve().parents[2]


def source():
    cfg=get_settings()
    data=Path(cfg.data_dir).resolve() if cfg.data_dir else ROOT/"backend"/"data"
    engine=create_engine(cfg.database_url or "sqlite:///"+(data/"trip_planner.db").as_posix())
    return engine,data


def rows(engine):
    present=set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        if engine.dialect.name=="postgresql":
            connection=connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            return {table:[dict(row) for row in connection.execute(text(f'SELECT * FROM "{table}" ORDER BY id')).mappings()]
                    if table in present else [] for table in TABLES}


def serialize(value):
    if isinstance(value,datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def encode(data):
    return json.dumps(data,ensure_ascii=False,sort_keys=True,default=serialize,separators=(",",":"))


def backup(destination):
    target=Path(destination).resolve()
    if target==ROOT or ROOT in target.parents:
        raise ValueError("Backup must be outside the Git workspace")
    target.mkdir(parents=True,exist_ok=False)
    engine,data=source()
    snapshot=rows(engine)
    (target/"business.json").write_text(encode(snapshot),encoding="utf-8")
    if data.exists():
        shutil.copytree(data,target/"application-data")
    for name in (".env",".env.docker"):
        file=ROOT/"backend"/name
        if file.exists():
            shutil.copy2(file,target/("backend"+name))
    for name in ("docker-compose.yml", "docker-compose.production.yml", "deploy/legacy-compose.yml"):
        file=ROOT/name
        if file.exists():
            destination=target/"compose"/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(file,destination)
    manifest={"created_at":datetime.now(timezone.utc).isoformat(),"source_dialect":engine.dialect.name,
              "commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
              "counts":{t:len(v) for t,v in snapshot.items()},"files":{},"images":{}}
    for service in ("backend","frontend"):
        image=f"langchain-trip-planner-{service}:pre-java-migration"
        result=subprocess.run(["docker","image","inspect","--format","{{.Id}}",image],capture_output=True,text=True)
        if result.returncode==0:
            manifest["images"][image]=result.stdout.strip()
    for file in target.rglob("*"):
        if file.is_file():
            manifest["files"][file.relative_to(target).as_posix()]=hashlib.sha256(file.read_bytes()).hexdigest()
    (target/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    verify(target)
    print(json.dumps({"backup":str(target),"counts":manifest["counts"],"checksums_verified":True}))


def verify(target):
    target=Path(target).resolve()
    manifest=json.loads((target/"manifest.json").read_text(encoding="utf-8"))
    for name,digest in manifest["files"].items():
        file=(target/name).resolve()
        if target not in file.parents or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            raise ValueError("Backup checksum mismatch")
    return manifest


def restore(target):
    target=Path(target).resolve();verify(target)
    payload=json.loads((target/"business.json").read_text(encoding="utf-8"))
    engine=create_engine(os.environ["TARGET_DATABASE_URL"])
    if engine.dialect.name!="postgresql":
        raise ValueError("Target must be PostgreSQL")
    metadata=inspect(engine)
    if "flyway_schema_history" not in metadata.get_table_names():
        raise ValueError("Initialize target with Flyway before import")
    with engine.begin() as db:
        for table in TABLES:
            if db.scalar(text(f'SELECT count(*) FROM "{table}"')):
                raise ValueError("Target business tables must be empty")
        for table in TABLES:
            columns={c["name"]:c for c in metadata.get_columns(table)}
            for raw in payload[table]:
                row=dict(raw)
                if set(row)-set(columns):
                    raise ValueError("Source contains unmapped columns")
                for name,value in row.items():
                    if value is not None and "TIMESTAMP" in str(columns[name]["type"]):
                        row[name]=datetime.fromisoformat(value)
                    if value is not None and "BOOLEAN" in str(columns[name]["type"]):
                        row[name]=bool(value)
                names=','.join(f'"{n}"' for n in row)
                params=','.join(':'+n for n in row)
                db.execute(text(f'INSERT INTO "{table}" ({names}) VALUES ({params})'),row)
            if table!="trip_tasks":
                db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}','id'),COALESCE((SELECT MAX(id) FROM {table}),1),(SELECT count(*)>0 FROM {table}))"))
    actual=rows(engine)
    for table,expected in payload.items():
        source_names=set(expected[0]) if expected else set()
        trimmed=[{k:v for k,v in row.items() if k in source_names} for row in actual[table]]
        # Normalize SQLite boolean representation without altering saved payloads.
        if table=="users":
            for row in expected:
                row["is_admin"]=bool(row["is_admin"])
        if encode(trimmed)!=encode(expected):
            raise ValueError(f"Imported table verification failed: {table}; retain target for inspection")
    print(json.dumps({"restored":True,"counts":{t:len(r) for t,r in actual.items()},"verified":True}))


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["inspect","backup","verify","restore"]);parser.add_argument("--backup-dir")
    args=parser.parse_args()
    if args.action=="inspect":
        engine,data=source();snapshot=rows(engine);print(json.dumps({"dialect":engine.dialect.name,"data_dir":str(data),"counts":{t:len(r) for t,r in snapshot.items()}}))
    elif not args.backup_dir:
        parser.error("--backup-dir is required")
    elif args.action=="backup":backup(args.backup_dir)
    elif args.action=="verify":verify(args.backup_dir);print("Backup checksums verified")
    else:restore(args.backup_dir)
