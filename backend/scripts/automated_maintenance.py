"""Maintenance-window backups and controlled scheduler recovery for one named project.

Inspection is the default. No pruning, deletion, credential output, or external notifications.
A UNC replica is a configured remote target; its failure domain is not inferred.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import tempfile
import time
from urllib.parse import urlsplit
from deployment_assets import services, docker, validate_manifest, digest, sql
from backup_java_deployment import backup


class MaintenanceFailure(RuntimeError):
    def __init__(self, report):
        super().__init__("Maintenance failed; inspect the structured step results")
        self.report = report


class MaintenanceBusy(RuntimeError):
    pass


@contextmanager
def maintenance_lock(project):
    """Nonblocking OS lock shared by backup/recovery entry points.

    Keep the inode/file permanently: deleting a locked path can let another
    process create a new inode and acquire an unrelated lock at the same name.
    All execution users must configure the same writable lock directory when
    sharing a Docker daemon; the default covers this user's manual/task runs.
    """
    folder=Path(os.environ.get("TRIP_MAINTENANCE_LOCK_DIR") or Path(tempfile.gettempdir())/"trip-planner-maintenance-locks")
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/("project-"+hashlib.sha256(project.encode("utf-8")).hexdigest()+".lock")
    stream=path.open("a+b")
    acquired=False
    try:
        try:
            # Windows mandatory byte locks also reject reads from another
            # process. Inspect length without reading the locked first byte.
            if os.fstat(stream.fileno()).st_size==0:
                stream.write(b"0");stream.flush()
            stream.seek(0)
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except OSError:
            raise MaintenanceBusy("Another maintenance process owns this project's lock") from None
        yield path
    finally:
        try:
            if acquired:
                stream.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        finally:
            stream.close()


def _failure(report, step, service, exception):
    # Subprocess/provider exception strings may contain private host paths.
    report.setdefault("errors", []).append({"step": step, "service": service,
                                             "error_type": type(exception).__name__})


def _loopback(host):
    value = host.casefold().rstrip(".").strip("[]")
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def _remote_target(path):
    text = str(path).replace("\\", "/")
    if not text.startswith("//"):
        return False
    server = text[2:].split("/", 1)[0].casefold().rstrip(".").strip("[]")
    local = {socket.gethostname().casefold().rstrip("."), socket.getfqdn().casefold().rstrip("."),
             os.environ.get("COMPUTERNAME", "").casefold().rstrip(".")}
    return bool(server and server not in local and not _loopback(server))


def _container_probe(container, service="backend"):
    # Probe the exact selected container, not a caller-controlled network URL.
    if service == "backend":
        raw = docker("exec", container, "curl", "-sS", "--max-time", "5", "-w", "\n%{http_code}", "http://127.0.0.1:9000/readyz")
        content, status = raw.decode("utf-8").rsplit("\n", 1)
        try:
            body = json.loads(content)
        except ValueError:
            body = {}
        return int(status.strip()), body
    # The Agent image already has Python/httpx; no shell interpolation or
    # secrets are placed in command arguments.
    code = "import json,urllib.request,urllib.error; " \
           "r=urllib.request.urlopen('http://127.0.0.1:9000/readyz',timeout=5); " \
           "print(json.dumps({'status':r.status,'body':json.loads(r.read())}))"
    result = json.loads(docker("exec", container, "python", "-c", code))
    return int(result["status"]), result["body"]


def _wait_ready(container, service, attempts=45):
    for attempt in range(attempts):
        try:
            if _container_probe(container, service)[0] == 200:
                return
        except (RuntimeError, ValueError, KeyError):
            pass
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("Selected service did not become ready after state restoration")


def _validate_project_url(base_url, frontend):
    """A compatibility URL may only identify this project's loopback binding."""
    if base_url is None:
        return
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not _loopback(parsed.hostname) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Recovery accepts only the selected project's loopback frontend URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    bindings = (frontend.get("NetworkSettings", {}).get("Ports", {}) or {}).get("80/tcp") or []
    if not any(int(binding["HostPort"]) == port for binding in bindings):
        raise ValueError("Recovery URL port does not belong to the selected frontend")

def snapshot(project):
    return {name:{"id":c["Id"],"image":c["Image"],"running":c["State"]["Running"]} for name,c in services(project).items()}

def replicate(source, destination):
    """Create a fresh copy and verify every member; never synchronize/delete a destination."""
    validate_manifest(source)
    if destination.exists():raise ValueError("Replica target must be a new directory")
    shutil.copytree(source,destination)
    validate_manifest(destination)
    if digest(source/"manifest.json")!=digest(destination/"manifest.json"):raise ValueError("Replica manifest differs")
    return {"path":str(destination),"verified":True,"off_host":False,
            "configured_remote_target":_remote_target(destination),
            "failure_domain_verified":False,
            "boundary":"Copy bytes verified; a UNC hostname alone does not prove an independent host or disaster-recovery domain."}

def run_backup(project,root,replica_root=None,execute=False):
    with maintenance_lock(project):
        return _run_backup_locked(project,root,replica_root,execute)


def _preflight_images(before,report):
    """Inspect the four selected containers' exact images before any writes.

    Running rootfs layers do not establish that Docker can still export their
    image identity. Never resolve a mutable tag as a substitute for that image.
    """
    results=[];missing=[]
    for name in ("backend","agent","postgres","frontend"):
        identity=before[name]["image"]
        item={"service":name,"image_id":identity}
        try:
            inspected=json.loads(docker("image","inspect",identity))
            if not isinstance(inspected,list) or len(inspected)!=1 or not isinstance(inspected[0],dict):
                raise ValueError("Unexpected image inspect shape")
            if inspected[0].get("Id")!=identity:
                item["status"]="identity_mismatch"
                raise ValueError("Image inspect did not return the exact selected identity")
            item["status"]="verified"
        except Exception as exception:
            # Only Docker's explicit missing-image diagnostic establishes
            # absence. Daemon/permission/JSON failures remain unclassified;
            # exception messages can contain private details and are omitted.
            if isinstance(exception,RuntimeError) and str(exception).strip().endswith("No such image: "+identity):
                item["status"]="missing"
                if identity not in missing:missing.append(identity)
            else:item.setdefault("status","inspect_failed")
            item["error_type"]=type(exception).__name__
            report.setdefault("errors",[]).append({"step":"image_preflight",**item})
        results.append(item)
    report["image_preflight"]={"passed":all(item["status"]=="verified" for item in results),
                               "images":results,"missing_image_ids":missing}
    if not report["image_preflight"]["passed"]:
        report.update(passed=False,execution_started=False,no_container_mutations_attempted=True,
                      last_success_updated=False)
        raise MaintenanceFailure(report)


def _run_backup_locked(project,root,replica_root=None,execute=False):
    before=snapshot(project)
    report={"operation":"backup","project":project,"before":before,"executed":execute,
            "rpo_target_seconds":86400,"rto_target_seconds":3600,
            "targets_are_objectives_not_measured_guarantees":True}
    if not execute:return report
    _preflight_images(before,report)
    report["execution_started"]=True
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination=root.resolve()/f"scheduled-{stamp}"
    start=time.monotonic();pg_started=False
    report.update(errors=[],ready_after_restore={})
    try:
        for name in ("backend","agent"):
            if before[name]["running"]:docker("stop","--time","30",before[name]["id"])
        if not before["postgres"]["running"]:
            docker("start",before["postgres"]["id"]);pg_started=True
        for _ in range(30):
            try:docker("exec",before["postgres"]["id"],"pg_isready","-U","trip");break
            except RuntimeError:time.sleep(1)
        backup(destination,project,archive_images=True)
        validate_manifest(destination)
        report.update(backup=str(destination),local_verified=True)
        if replica_root:report["replica"]=replicate(destination,replica_root/f"scheduled-{stamp}")
    except Exception as exception:
        _failure(report,"backup_or_replication","backup",exception)
    finally:
        for name in ("backend","agent"):
            if before[name]["running"]:
                try:
                    docker("start",before[name]["id"])
                except Exception as exception:
                    _failure(report,"restore_start",name,exception)
                    continue
                try:
                    _wait_ready(before[name]["id"],name)
                    report["ready_after_restore"][name]=True
                except Exception as exception:
                    report["ready_after_restore"][name]=False
                    _failure(report,"restore_readiness",name,exception)
        if pg_started or not before["postgres"]["running"]:
            try:docker("stop",before["postgres"]["id"])
            except Exception as exception:_failure(report,"restore_original_stopped_state","postgres",exception)
        try:report["after"]=snapshot(project)
        except Exception as exception:_failure(report,"restore_snapshot","project",exception)
        report["elapsed_seconds"]=round(time.monotonic()-start,2)
    report["original_running_state_restored"]=bool(report.get("after")) and all(before[n]["running"]==report["after"][n]["running"] for n in before)
    if not report["original_running_state_restored"]:
        report["errors"].append({"step":"restore_running_states","service":"project","error_type":"StateMismatch"})
    if not report["errors"] and report.get("local_verified"):
        heartbeat={"observed_at":datetime.now(timezone.utc).isoformat(),"backup":str(destination),
                   "replica_verified":bool(report.get("replica",{}).get("verified")),
                   "original_running_state_restored":True,
                   "readiness_applicable":bool(report["ready_after_restore"]),
                   "readiness_verified":bool(report["ready_after_restore"]) and all(report["ready_after_restore"].values())}
        try:
            # The success marker is written only after all original running
            # services are ready; a valid archive cannot hide a failed restart.
            if before["postgres"]["running"]:
                pg=services(project)["postgres"]
                env=dict(v.split("=",1) for v in pg["Config"]["Env"] if "=" in v)
                database=env.get("POSTGRES_DB","trip_java");user=env.get("POSTGRES_USER","trip")
                if sql(pg["Id"],database,"SELECT to_regclass('public.operational_heartbeats') IS NOT NULL",user)=="t":
                    names=["backup"]+(["remote_backup_copy"] if report.get("replica",{}).get("configured_remote_target") else [])
                    for name in names:sql(pg["Id"],database,f"INSERT INTO operational_heartbeats(name,observed_at) VALUES('{name}','{heartbeat['observed_at']}') ON CONFLICT(name) DO UPDATE SET observed_at=excluded.observed_at",user)
            root.mkdir(parents=True,exist_ok=True)
            (root/"last-success.json").write_text(json.dumps(heartbeat,indent=2),encoding="utf-8")
        except Exception as exception:_failure(report,"success_heartbeat","backup",exception)
    report["passed"]=not report["errors"] and bool(report.get("local_verified"))
    if not report["passed"]:raise MaintenanceFailure(report)
    return report

def recover_scheduler(project,base_url=None,execute=False):
    with maintenance_lock(project):
        return _recover_scheduler_locked(project,base_url,execute)


def _recover_scheduler_locked(project,base_url=None,execute=False):
    before=snapshot(project)
    if not before["backend"]["running"]:raise RuntimeError("Stopped user services are not eligible for automatic restart")
    _validate_project_url(base_url,services(project)["frontend"])
    status,body=_container_probe(before["backend"]["id"])
    if status!=503 or body.get("code")!="SCHEDULER_OWNERSHIP_LOST":
        raise RuntimeError("Recovery requires the explicit scheduler-ownership failure, not an arbitrary outage")
    report={"operation":"scheduler_restart","project":project,"before":before,"executed":execute,
            "probe":"selected_backend_container_loopback","errors":[]}
    if execute:
        # The new process calls TaskMapper.recover before taking any new work.
        try:
            docker("restart","--time","30",before["backend"]["id"])
            _wait_ready(before["backend"]["id"],"backend")
            report["ready_after_restart"]=True
        except Exception as exception:
            _failure(report,"controlled_restart_or_readiness","backend",exception)
            report["passed"]=False
            raise MaintenanceFailure(report) from None
    report["passed"]=True
    return report

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("operation",choices=("backup","recover-scheduler"));p.add_argument("--project",default="langchain-trip-planner");p.add_argument("--backup-root",type=Path,default=Path(__file__).resolve().parents[2]/"backups");p.add_argument("--replica-root",type=Path);p.add_argument("--base-url");p.add_argument("--execute",action="store_true");p.add_argument("--output",type=Path)
    a=p.parse_args(argv)
    try:
        result=run_backup(a.project,a.backup_root,a.replica_root,a.execute) if a.operation=="backup" else recover_scheduler(a.project,a.base_url,a.execute)
        exit_code=0
    except MaintenanceFailure as exception:
        result=exception.report;exit_code=1
    except Exception as exception:
        result={"operation":a.operation,"project":a.project,"passed":False,"error_type":type(exception).__name__};exit_code=1
    text=json.dumps(result,indent=2);print(text)
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(text,encoding="utf-8")
    return exit_code


if __name__=="__main__":
    raise SystemExit(main())
