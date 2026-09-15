"""Import a verified legacy backup into the new empty daily Flyway database.

Administrative one-shot container only; the running Agent never gets DB credentials.
"""
import argparse
import os
from pathlib import Path
import subprocess
from urllib.parse import quote
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]

def run(backup):
    cfg=dotenv_values(ROOT/"deploy/runtime/business.env")
    if cfg.get("WORKERS_ENABLED")!="false":
        raise RuntimeError("Keep Java workers disabled during import")
    database=dotenv_values(ROOT/"deploy/runtime/postgres.env")
    env=dict(os.environ)
    env["TARGET_DATABASE_URL"]=("postgresql+psycopg://"+quote(database["POSTGRES_USER"],safe="")+":"+
        quote(database["POSTGRES_PASSWORD"],safe="")+"@postgres:5432/"+database["POSTGRES_DB"])
    subprocess.run(["docker","run","--rm","--network","langchain-trip-planner_default",
        "-e","TARGET_DATABASE_URL","-v",str(backup.resolve())+":/backup:ro",
        "--entrypoint","python","langchain-trip-planner-agent","scripts/migrate_java_business.py",
        "restore","--backup-dir","/backup"],env=env,cwd=ROOT,check=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--backup",required=True,type=Path)
    run(parser.parse_args().backup)
