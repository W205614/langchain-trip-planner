"""Restore identity enrollment/negative-startup report counterexamples, no Docker."""
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/"scripts"))
import restore_java_backup as restore

IDENTITY="0123456789abcdef0123456789abcdef"
GROUPS=("knowledge_uploads","chroma","agent-runtime")


def clone(tmp_path,monkeypatch):
    backup=tmp_path/"backup";backup.mkdir()
    output=tmp_path/"clone";output.mkdir()
    manifest={"mounts":{},"sha256":{}}
    for name in GROUPS:
        folder=output/name;folder.mkdir();(folder/"original.bin").write_bytes((name+"-verified-content").encode())
        archive=name+".tar"
        with tarfile.open(backup/archive,"w") as stream:stream.add(folder/"original.bin",arcname="original.bin")
        manifest["mounts"][name]={"archive":archive};manifest["sha256"][archive]="validated-by-caller"
    summary={"trip_records":"2:original-content","flyway_schema_history":"10:published-history"}
    columns={"trip_records":["id","plan_json"],"flyway_schema_history":["installed_rank"]}
    (backup/"database-summary.json").write_text(json.dumps(summary),encoding="utf-8")
    (backup/"database-columns.json").write_text(json.dumps(columns),encoding="utf-8")
    state={"exists":False,"identity":""};queries=[]
    def sql(_container,_db,query):
        queries.append(query)
        if query.startswith("SELECT to_regclass"):return "t" if state["exists"] else "f"
        if query.startswith("SELECT dataset_id"):return state["identity"]
        if query.startswith("CREATE TABLE"):state["exists"]=True
        if query.startswith("INSERT INTO deployment_identity"):
            if not state["identity"]:state["identity"]=re.search(r"VALUES\(1,'([^']+)'\)",query)[1]
        return ""
    monkeypatch.setattr(restore,"sql",sql)
    monkeypatch.setattr(restore,"database_summary",lambda *_args,**_kwargs:deepcopy(summary))
    return backup,output,manifest,state,queries


def test_verified_clone_enrolls_db_and_three_directories_without_overwriting_data(tmp_path,monkeypatch):
    backup,output,manifest,state,_=clone(tmp_path,monkeypatch)
    originals={name:(output/name/"original.bin").read_bytes() for name in GROUPS}
    report=restore.enroll_clone_dataset(backup,output,manifest,"isolated-clone-pg",{"DATASET_ID":IDENTITY})
    assert report["dataset_id"]==IDENTITY and report["production_guard_required"] is True
    assert state["identity"]==IDENTITY
    assert report["existing_markers_overwritten"] is False
    for name in GROUPS:
        assert (output/name/".dataset-id").read_text().strip()==IDENTITY
        assert (output/name/"original.bin").read_bytes()==originals[name]


@pytest.mark.parametrize("mismatch",["db","upload","new_file","changed_file"])
def test_foreign_identity_or_unverified_snapshot_fails_before_metadata_write(tmp_path,monkeypatch,mismatch):
    backup,output,manifest,state,queries=clone(tmp_path,monkeypatch)
    if mismatch=="db":state.update(exists=True,identity="foreign-dataset-000000000000000")
    elif mismatch=="upload":(output/"knowledge_uploads/.dataset-id").write_text("foreign-dataset-000000000000000")
    elif mismatch=="new_file":(output/"chroma/new.bin").write_bytes(b"unbacked")
    else:(output/"agent-runtime/original.bin").write_bytes(b"changed")
    before={str(path):path.read_bytes() for path in output.rglob("*") if path.is_file()}
    with pytest.raises((ValueError,RuntimeError)):
        restore.enroll_clone_dataset(backup,output,manifest,"isolated-clone-pg",{"DATASET_ID":IDENTITY})
    assert not any(query.startswith(("CREATE","INSERT")) for query in queries)
    assert before=={str(path):path.read_bytes() for path in output.rglob("*") if path.is_file()}


def test_negative_startups_require_specific_guard_reason_and_keep_every_resource(tmp_path,monkeypatch):
    _,output,_,_,_=clone(tmp_path,monkeypatch)
    for name in GROUPS:(output/name/".dataset-id").write_text(IDENTITY+"\n")
    values={"DATASET_ID":IDENTITY,"DATASET_ENROLL_NEW":"false","APP_ENV":"production","WORKERS_ENABLED":"false"}
    commands=[];environments=[]
    def command(args,**kwargs):
        commands.append(args)
        if args[:2]==["docker","logs"]:
            message="Database DATASET_ID does not match this environment" if "guard-db-" in args[-1] else "Persistent mount belongs to another dataset"
            return subprocess.CompletedProcess(args,0,stdout=message.encode(),stderr=b"")
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(args,0,stdout=b"isolated-container")
    monkeypatch.setattr(restore.subprocess,"run",command)
    monkeypatch.setattr(restore,"docker",lambda *_:json.dumps([{"State":{"Status":"exited","ExitCode":1}}]).encode())
    def args(name,folder,_settings):return ["docker","run","-d","--name",name,"-v",str(folder)+":/data/uploads:ro","-e","DATASET_ID"]
    report={"resources":[],"containers":[]}
    result=restore.run_clone_identity_guards(output,"unique", "candidate",values,dict(values),args,report,lambda:None)
    assert result["database_mismatch"]["rejected"] and result["upload_mismatch"]["rejected"]
    assert result["clone_files_and_markers_unchanged"] is True
    assert len(report["resources"])==len(report["containers"])==2
    assert environments[0]["DATASET_ID"]!=IDENTITY and environments[1]["DATASET_ID"]==IDENTITY
    assert all(env["APP_ENV"]=="production" and env["DATASET_ENROLL_NEW"]=="false" for env in environments)
    assert not any("rm" in cmd or "--rm" in cmd or "prune" in cmd for cmd in commands)


def test_arbitrary_startup_failure_cannot_pass_identity_guard_validation(monkeypatch):
    monkeypatch.setattr(restore.subprocess,"run",lambda args,**kwargs:subprocess.CompletedProcess(args,0,stdout=b"unrelated startup failure",stderr=b""))
    monkeypatch.setattr(restore,"docker",lambda *_:b'[{"State":{"Status":"exited","ExitCode":1}}]')
    report={"resources":[],"containers":[]}
    with pytest.raises(RuntimeError,match="unverified reason"):
        restore.expect_identity_rejection("exact-clone",["docker","run"],"candidate",{},"Persistent mount belongs",report,lambda:None)
    assert report["resources"][0]["created"] is True
    assert report["containers"]==["exact-clone"]
