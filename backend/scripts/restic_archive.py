"""Encrypt an already coherent backup, or restore it to a fresh isolated directory.

No scheduler is installed, no production service is stopped, and no retention prune is performed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values
from deployment_assets import validate_manifest

ROOT=Path(__file__).resolve().parents[2]
RESTIC_VERSION="0.19.1"


def environment(config):
    values=dotenv_values(config)
    allowed={key:str(value) for key,value in values.items() if value is not None and key.startswith(("RESTIC_","AWS_"))}
    if not allowed.get("RESTIC_REPOSITORY") or not (allowed.get("RESTIC_PASSWORD") or allowed.get("RESTIC_PASSWORD_FILE")):
        raise ValueError("Configure RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE (or RESTIC_PASSWORD)")
    return {**os.environ,**allowed}


def call(binary,args,env):
    process=subprocess.run([str(binary),*args],env=env,capture_output=True,text=True,encoding="utf-8")
    if process.returncode:
        # Repository URLs and provider responses may contain sensitive information.
        raise RuntimeError("Restic command failed (exit "+str(process.returncode)+"); inspect the configured repository and access permissions")
    return process.stdout


def run(args):
    env=environment(args.env_file)
    version=call(args.binary,["version"],env)
    if not version.startswith("restic "+RESTIC_VERSION+" "):
        raise ValueError("This adapter is verified with restic "+RESTIC_VERSION)
    if args.command=="init":
        if not args.execute:return {"planned":"initialize encrypted repository","executed":False}
        call(args.binary,["init"],env)
        return {"initialized":True}
    if args.command=="archive":
        source=args.source.resolve(strict=True)
        manifest=validate_manifest(source)
        if not args.execute:
            return {"planned":"archive coherent backup","files_verified":len(manifest["sha256"]),"executed":False}
        output=call(args.binary,["backup","--json","--tag","trip-planner","--",str(source)],env)
        summary=next((json.loads(line) for line in output.splitlines() if json.loads(line).get("message_type")=="summary"),None)
        if not summary or not summary.get("snapshot_id"):raise RuntimeError("Restic did not confirm a snapshot")
        return {"archived":True,"snapshot_id":summary["snapshot_id"],"off_host_verified":False}
    if args.command=="check":
        call(args.binary,["check"],env)
        return {"repository_metadata_checked":True,"all_data_read":False,"restore_drill_completed":False}
    target=args.target.resolve()
    if target==ROOT or ROOT in target.parents or target.exists():
        raise ValueError("Restore target must be a new directory outside the project")
    if not args.execute:return {"planned":"restore encrypted archive","executed":False}
    call(args.binary,["restore",args.snapshot,"--target",str(target),"--tag","trip-planner"],env)
    candidates=[]
    for path in target.rglob("manifest.json"):
        value=json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema")=="Java/Flyway-v2":candidates.append(path.parent)
    if len(candidates)!=1:raise ValueError("Restore must contain exactly one coherent Java backup")
    validate_manifest(candidates[0])
    return {"restored":True,"checksums_verified":True,"backup_directory":str(candidates[0]),"application_restore_completed":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["init","archive","check","restore"])
    parser.add_argument("--env-file",required=True,type=Path)
    parser.add_argument("--binary",default="restic",type=Path)
    parser.add_argument("--source",type=Path)
    parser.add_argument("--target",type=Path)
    parser.add_argument("--snapshot",default="latest")
    parser.add_argument("--execute",action="store_true")
    args=parser.parse_args()
    if args.command=="archive" and args.source is None:parser.error("archive requires --source")
    if args.command=="restore" and args.target is None:parser.error("restore requires --target")
    print(json.dumps(run(args),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
