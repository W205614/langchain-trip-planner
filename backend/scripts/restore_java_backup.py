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
import re
import subprocess
import tarfile
import time
from uuid import uuid4
from dotenv import dotenv_values
from deployment_assets import validate_manifest, extract_safe, docker, database_summary, sql
from enroll_dataset import tar_inventory

ROOT=Path(__file__).resolve().parents[2]
COMPOSE=["docker","compose","-f",str(ROOT/"docker-compose.yml")]


class RestoreVerificationFailure(RuntimeError):
    def __init__(self, report):
        super().__init__("Restore verification failed at " + report["stage"] + "; exact isolated resources retained in restore-report.json")
        self.report = report


def restored_inventory(folder, identity):
    """Compare extracted clone contents without following links or loading files."""
    result={}
    if folder.is_symlink():raise ValueError("Restored clone directory cannot be a symlink")
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():raise ValueError("Restored clone files cannot be symlinks")
        name=path.relative_to(folder).as_posix()
        if name==".dataset-id":
            if not path.is_file() or path.stat().st_size>256 or path.read_text(encoding="utf-8").strip()!=identity:
                raise ValueError("Restored clone marker belongs to another dataset")
            continue
        if path.is_dir():result[name]={"kind":"directory"};continue
        if not path.is_file():raise ValueError("Restored clone contains an unsupported file")
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda:stream.read(1024*1024),b""):digest.update(block)
        result[name]={"kind":"file","size":path.stat().st_size,"sha256":digest.hexdigest()}
    return result


def enroll_clone_dataset(backup, output, manifest, postgres, source_values):
    """Enroll only freshly restored, byte-verified clone files and original rows.

    No running daily application or four-service discovery is involved. Source
    markers/identity are retained if present and must all agree; none is replaced.
    """
    exists=sql(postgres,"trip_restore","SELECT to_regclass('public.deployment_identity') IS NOT NULL")=="t"
    current=sql(postgres,"trip_restore","SELECT dataset_id FROM deployment_identity WHERE id=1") if exists else ""
    identities={value for value in (current,source_values.get("DATASET_ID","")) if value}
    for name in ("knowledge_uploads","chroma","agent-runtime"):
        folder=output/name;marker=folder/".dataset-id"
        if folder.is_symlink() or marker.is_symlink():raise ValueError("Clone dataset markers cannot be symlinks")
        if marker.exists():
            if not marker.is_file() or marker.stat().st_size>256:raise ValueError("Invalid clone dataset marker")
            identities.add(marker.read_text(encoding="utf-8").strip())
    if len(identities)>1:raise ValueError("Restored clone database, environment and file identities disagree")
    identity=next(iter(identities)) if identities else uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}",identity):raise ValueError("Invalid restored dataset identity")
    expected=json.loads((backup/"database-summary.json").read_text(encoding="utf-8"))
    columns=json.loads((backup/"database-columns.json").read_text(encoding="utf-8"))
    if database_summary(postgres,"trip_restore",columns=columns)!=expected:
        raise RuntimeError("Original restored rows changed before clone dataset enrollment")
    for name in ("knowledge_uploads","chroma","agent-runtime"):
        archive_name=manifest["mounts"][name]["archive"]
        archive=(backup/archive_name).resolve(strict=True)
        if not archive.is_relative_to(backup.resolve()) or archive_name not in manifest["sha256"]:
            raise ValueError("Clone archive is not protected by the backup manifest")
        with archive.open("rb") as stream:original=tar_inventory(stream,identity)
        if restored_inventory(output/name,identity)!=original:
            raise ValueError("Restored clone files differ from verified snapshot")
    # Only metadata is added after every existing row and file has been checked.
    # IF NOT EXISTS exactly matches V11, whose Flyway history is still unapplied.
    sql(postgres,"trip_restore","CREATE TABLE IF NOT EXISTS deployment_identity (id INTEGER PRIMARY KEY CHECK(id=1),dataset_id VARCHAR(64) NOT NULL,enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    for name in ("knowledge_uploads","chroma","agent-runtime"):
        marker=output/name/".dataset-id"
        if not marker.exists():
            with marker.open("x",encoding="utf-8") as stream:stream.write(identity+"\n")
    sql(postgres,"trip_restore",f"INSERT INTO deployment_identity(id,dataset_id) VALUES(1,'{identity}') ON CONFLICT(id) DO NOTHING")
    if sql(postgres,"trip_restore","SELECT dataset_id FROM deployment_identity WHERE id=1")!=identity:
        raise RuntimeError("Clone dataset identity registration did not match")
    return {"dataset_id":identity,"scope":"fresh isolated clone only","original_rows_and_files_verified":True,
            "marker_directories":[str(output/name) for name in ("knowledge_uploads","chroma","agent-runtime")],
            "production_guard_required":True,"existing_markers_overwritten":False}


def expect_identity_rejection(name, args, image, environment, expected_error, report, save):
    resource={"role":"identity_guard_negative","name":name,"image":image,"created":False,"creation_attempted":True}
    report["resources"].append(resource);save()
    subprocess.run(args+[image],env=environment,capture_output=True,check=True)
    resource["created"]=True;report["containers"].append(name);save()
    for _ in range(120):
        state=json.loads(docker("inspect",name))[0]["State"]
        if state.get("Status") in {"exited","dead"}:
            logs=subprocess.run(["docker","logs",name],capture_output=True)
            known_reason=expected_error.encode() in logs.stdout+logs.stderr
            if int(state.get("ExitCode",0))==0 or not known_reason:
                raise RuntimeError("Negative identity startup failed for an unverified reason")
            resource.update(state=state["Status"],exit_code=state["ExitCode"],expected_identity_error_verified=True)
            save();return {"container":name,"rejected":True,"expected_error_verified":True}
        time.sleep(.5)
    raise RuntimeError("Negative identity startup did not reject the mismatch")


def run_clone_identity_guards(output, suffix, image, values, environment, backend_args, report, save):
    identity=values["DATASET_ID"];wrong=uuid4().hex
    while wrong==identity:wrong=uuid4().hex
    before={name:restored_inventory(output/name,identity) for name in ("knowledge_uploads","chroma","agent-runtime")}
    wrong_database="trip-final-restore-guard-db-"+suffix
    result={"database_mismatch":expect_identity_rejection(wrong_database,
        backend_args(wrong_database,output/"knowledge_uploads",values),image,
        {**environment,"DATASET_ID":wrong},"Database DATASET_ID does not match this environment",report,save)}
    wrong_upload=output/"guard-upload-mismatch"
    wrong_upload.mkdir()
    (wrong_upload/".dataset-id").write_text(wrong+"\n",encoding="utf-8")
    (wrong_upload/"sentinel.bin").write_bytes(b"isolated-mismatch-sentinel")
    guard_upload="trip-final-restore-guard-upload-"+suffix
    result["upload_mismatch"]=expect_identity_rejection(guard_upload,backend_args(guard_upload,wrong_upload,values),image,
        environment,"Persistent mount belongs to another dataset",report,save)
    if (wrong_upload/".dataset-id").read_text(encoding="utf-8").strip()!=wrong or (wrong_upload/"sentinel.bin").read_bytes()!=b"isolated-mismatch-sentinel":
        raise RuntimeError("Identity guard overwrote mismatched clone files")
    if before!={name:restored_inventory(output/name,identity) for name in before}:
        raise RuntimeError("Identity guard changed original clone file content")
    for name in before:
        if (output/name/".dataset-id").read_text(encoding="utf-8").strip()!=identity:
            raise RuntimeError("Identity guard changed an original clone marker")
    result["clone_files_and_markers_unchanged"]=True
    return result


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
    suffix=uuid4().hex[:12]
    postgres="trip-final-restore-pg-"+suffix
    backend="trip-final-restore-backend-"+suffix
    preserved="trip-final-restore-baseline-"+suffix
    network="trip-final-restore-"+suffix
    label="codex.task=trip-planner-final"
    baseline={"role":"baseline_backend","name":backend,"original_name":backend,
              "preserved_name":preserved,"image":manifest["images"]["backend"],"created":False}
    database_resource={"role":"isolated_postgres","name":postgres,"image":manifest["images"]["postgres"],"created":False}
    report={"checksums_verified":True,"passed":False,"stage":"extract_files","production_configuration_changed":False,
            "containers":[],"network":network,"network_created":False,"files":str(output),
            "resources":[database_resource,baseline],"mount_archives":list(manifest["mounts"]),
            "automatic_cleanup":False,"resource_retention":"All created clone containers and the isolated network are retained until separately authorized cleanup."}

    def save():
        (output/"restore-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")

    def stage(value):
        report["stage"]=value;save()

    save()
    try:
        for name, mount in manifest["mounts"].items():
            extract_safe(backup/mount["archive"],output/name)
        network_args=[]
        if network_subnet:
            stage("validate_network_subnet")
            requested=ipaddress.ip_network(network_subnet,strict=True)
            identities=docker("network","ls","-q").decode().split()
            networks=json.loads(docker("network","inspect",*identities)) if identities else []
            for existing in networks:
                for config in existing.get("IPAM",{}).get("Config") or []:
                    if config.get("Subnet") and requested.overlaps(ipaddress.ip_network(config["Subnet"])):
                        raise ValueError("Restore subnet overlaps an existing Docker network")
            network_args=["--subnet",str(requested)]
        stage("create_isolated_network")
        docker("network","create","--internal","--label",label,*network_args,network)
        report["network_created"]=True
        stage("start_isolated_postgres")
        database_resource["creation_attempted"]=True;save()
        docker("run","-d","--name",postgres,"--label",label,"--label","com.docker.compose.project=trip-final-restore",
               "--label","com.docker.compose.service=restore-postgres","--network",network,"--tmpfs","/var/lib/postgresql/data:rw,size=512m",
               "--memory","768m","--cpus","1","--pids-limit","128",
               "-e","POSTGRES_USER=trip","-e","POSTGRES_PASSWORD=restore-isolated-only",
               "-e","POSTGRES_DB=trip_restore",manifest["images"]["postgres"])
        database_resource["created"]=True;report["containers"].append(postgres)
        stage("await_isolated_postgres")
        for _ in range(120):
            ready=subprocess.run(["docker","exec",postgres,"pg_isready","-U","trip"],capture_output=True)
            if ready.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError("Isolated PostgreSQL did not become ready")
        stage("restore_original_database")
        with (backup/"database.dump").open("rb") as stream:
            subprocess.run(["docker","exec","-i",postgres,"pg_restore","-U","trip","-d","trip_restore",
                            "--no-owner","--no-acl","--exit-on-error"],stdin=stream,capture_output=True,check=True)
        expected=json.loads((backup/"database-summary.json").read_text(encoding="utf-8"))
        actual=database_summary(postgres,"trip_restore")
        if expected!=actual:raise RuntimeError("Restored table contents differ; artifacts retained")
        report.update(tables_verified=len(expected),rows_verified=sum(int(v.split(':')[0]) for v in expected.values()))
        previous_history=sql(postgres,"trip_restore","SELECT json_agg(s ORDER BY installed_rank) FROM flyway_schema_history s")
        environment=dict(os.environ)
        values=dict(value.split("=",1) for value in json.loads((backup/"runtime/backend.json").read_text()) if "=" in value)
        values.update(JDBC_DATABASE_URL=f"jdbc:postgresql://{postgres}:5432/trip_restore",POSTGRES_USER="trip",
                      POSTGRES_PASSWORD="restore-isolated-only",WORKERS_ENABLED="false",UPLOAD_DIR="/data/uploads")
        environment.update(values)
        def backend_args(name,uploads,settings):
            args=["docker","run","-d","--name",name,"--label",label,"--label","com.docker.compose.project=trip-final-restore",
                  "--label","com.docker.compose.service=restore-backend","--network",network,
                  "--memory","1g","--cpus","2","--pids-limit","256",
                  "--log-driver","json-file","--log-opt","max-size=10m","--log-opt","max-file=3",
                  "-v",str(uploads)+":/data/uploads:ro"]
            args.extend(value for key in settings for value in ("-e",key))
            return args
        args=backend_args(backend,output/"knowledge_uploads",values)
        stage("start_baseline_backend")
        baseline["creation_attempted"]=True;save()
        subprocess.run(args+[manifest["images"]["backend"]],env=environment,capture_output=True,check=True)
        baseline["created"]=True;baseline["state"]="running";report["containers"].append(backend)
        stage("await_baseline_backend")
        for _ in range(120):
            ready=subprocess.run(["docker","exec",backend,"curl","-fsS","http://localhost:9000/readyz"],capture_output=True)
            if ready.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError("Restored backend not ready; diagnostics retained")
        report["restored_java_ready"]=True
        report["baseline_ready_before_upgrade"]=True
        report["active_backend_ready"]=True
        if candidate_image:
            stage("validate_candidate_inputs")
            columns_file=backup/"database-columns.json"
            if not columns_file.is_file():raise RuntimeError("Upgrade verification requires original column metadata")
            columns=json.loads(columns_file.read_text(encoding="utf-8"));columns.pop("flyway_schema_history",None)
            # Preserve the successfully restored baseline. The candidate gets
            # the original clone name, while the old container remains stopped
            # under a unique name. There is no resource removal on any path.
            stage("stop_baseline_for_candidate")
            docker("stop","--time","30",backend);baseline["state"]="stopped"
            report["active_backend_ready"]=False
            stage("rename_retained_baseline")
            baseline["rename_attempted"]=True;save()
            docker("rename",backend,preserved)
            baseline["name"]=preserved;baseline["renamed"]=True
            report["containers"]=[preserved if name==backend else name for name in report["containers"]]
            report["rollback"]={"retained_baseline_container":preserved,"retained_baseline_image":manifest["images"]["backend"],
                "old_image_on_upgraded_schema":"not_tested","data_rollback_verified":False,
                "boundary":"Starting the retained old image uses the candidate-upgraded database; this is not a data rollback and compatibility is not guaranteed. Restore the verified backup into a separate fresh clone to verify an actual database rollback.",
                "inspection_restart_command":["docker","start",preserved]}
            stage("enroll_verified_clone_dataset")
            report["dataset_identity"]=enroll_clone_dataset(backup,output,manifest,postgres,values)
            values.update(DATASET_ID=report["dataset_identity"]["dataset_id"],DATASET_ENROLL_NEW="false",
                          APP_ENV="production",VALIDATION_ALLOW_FIXTURES="no")
            environment.update(values)
            stage("verify_production_identity_mismatch_guards")
            guard_history=sql(postgres,"trip_restore","SELECT json_agg(s ORDER BY installed_rank) FROM flyway_schema_history s")
            report["dataset_identity_guards"]=run_clone_identity_guards(output,suffix,candidate_image,values,environment,backend_args,report,save)
            if sql(postgres,"trip_restore","SELECT dataset_id FROM deployment_identity WHERE id=1")!=values["DATASET_ID"]:
                raise RuntimeError("Negative identity checks changed the clone database identity")
            after_guard_history=sql(postgres,"trip_restore","SELECT json_agg(s ORDER BY installed_rank) FROM flyway_schema_history s")
            if json.loads(after_guard_history)!=json.loads(guard_history):
                raise RuntimeError("Rejected identity checks advanced or changed Flyway migration history")
            report["dataset_identity_guards"]["flyway_history_unchanged"]=True
            candidate={"role":"candidate_backend","name":backend,"image":candidate_image,"created":False,"creation_attempted":True}
            report["resources"].append(candidate)
            stage("start_candidate_backend")
            subprocess.run(backend_args(backend,output/"knowledge_uploads",values)+[candidate_image],env=environment,capture_output=True,check=True)
            candidate["created"]=True;candidate["state"]="running";report["containers"].append(backend)
            stage("await_candidate_backend")
            for _ in range(120):
                ready=subprocess.run(["docker","exec",backend,"curl","-fsS","http://localhost:9000/readyz"],capture_output=True)
                if ready.returncode==0:break
                time.sleep(.5)
            else:raise RuntimeError("Candidate migration/readiness failed; isolated clones retained")
            report["active_backend_ready"]=True
            stage("verify_candidate_original_data_and_migrations")
            upgraded=database_summary(postgres,"trip_restore",columns=columns)
            old_tables={k:v for k,v in expected.items() if k!="flyway_schema_history"}
            if upgraded!=old_tables:raise RuntimeError("Candidate upgrade changed existing business rows; clones retained")
            history=json.loads(previous_history)
            highest=max(row["installed_rank"] for row in history)
            after_history=sql(postgres,"trip_restore",f"SELECT json_agg(s ORDER BY installed_rank) FROM flyway_schema_history s WHERE installed_rank<={highest}")
            if json.loads(after_history)!=history:raise RuntimeError("Published migration history changed")
            report["upgrade"]={"candidate_image_id":json.loads(docker("image","inspect",candidate_image))[0]["Id"],
                               "candidate_ready":True,"original_business_tables_unchanged":len(columns),
                               "original_migrations_unchanged":len(history),
                               "schema_version":sql(postgres,"trip_restore","SELECT version FROM flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1")}
        report["passed"]=True;stage("complete")
    except Exception as exception:
        report["error_type"]=type(exception).__name__
        report["passed"]=False;save()
        raise RestoreVerificationFailure(report) from None
    print(json.dumps(report))
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--backup",required=True,type=Path);parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--candidate-image",help="Verify a candidate migration only in the restored clone")
    parser.add_argument("--network-subnet",help="Optional unused CIDR when Docker's automatic address pools are exhausted")
    args=parser.parse_args();restore(args.backup,args.output,args.candidate_image,args.network_subnet)
