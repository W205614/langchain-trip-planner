"""Evidence-only, operator-run hardened mount backup/restore rehearsal.

Default is a plan with no Docker/HTTP. The coordinator alone runs --execute.
No daily project, PostgreSQL restore, resource removal, external supplier, or
existing directory overwrite is performed. Logs and /tmp remain fresh tmpfs.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend/scripts"))
from automated_maintenance import MaintenanceFailure, run_backup
from deployment_assets import digest, extract_safe, services, validate_manifest

PROJECT = "trip-opt-hardened-20261026"
BUSINESS_SHA = "220257ad32e50d08cb987d565882f8a038e36c6f"
RESTORE_NAME = "trip-opt-hardened-file-restore-20261026-agent"
RESTORE_PROJECT = "trip-opt-hardened-file-restore-20261026"
NETWORK = PROJECT + "_default"
RELEASE = EVIDENCE / "release-candidate-images-220257a.json"
PROBE = EVIDENCE / "hardened-runtime-probe.py"
PROBE_REPORT = EVIDENCE / "hardened-runtime-probe-result.json"
BACKUP_ROOT = ROOT / "backups/optimization-hardened-20261026"
REPLICA_ROOT = ROOT / "backups/optimization-hardened-replicas-20261026"
VISUAL_ROOT = Path(r"C:\Users\25452\.codex\visualizations\2026\10\26\01a176e2-8174-7042-8567-4a3b00affe90")
MOUNTS = {
    "knowledge_uploads": ("backend", "/data/uploads"),
    "chroma": ("agent", "/app/data/chroma"),
    "agent-runtime": ("agent", "/app/runtime"),
}
EXPECTED_TMPFS = {
    "/tmp": "rw,nosuid,nodev,noexec,size=128m,uid=1000,gid=1000,mode=1777",
    "/app/logs": "rw,nosuid,nodev,noexec,size=64m,uid=1000,gid=1000,mode=0750",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def env_map(item):
    return dict(value.split("=", 1) for value in item.get("Config", {}).get("Env", []) if "=" in value)


def snapshot(found):
    # Deliberately no Config.Env, execution arguments, health output or logs.
    return {role: {"id": item["Id"], "image_id": item["Image"],
                   "running": item["State"]["Running"],
                   "mounts": [{key: mount[key] for key in ("Type", "Name", "Source", "Destination", "RW") if key in mount}
                              for mount in item.get("Mounts", [])]}
            for role, item in found.items()}


def tar_inventory(path):
    """All regular files, including dataset markers, hashed in bounded memory."""
    result = {}
    with tarfile.open(path, "r|*") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts, "Unsafe archive path")
            relative = str(name)
            if relative == ".":
                require(member.isdir(), "Invalid archive root")
                continue
            require(relative not in result, "Duplicate archive member")
            if member.isdir():
                result[relative] = {"kind": "directory"}
                continue
            require(member.isfile(), "Links/special files require separate handling")
            value, size = hashlib.sha256(), 0
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(block); size += len(block)
            require(size == member.size, "Truncated archive member")
            result[relative] = {"kind": "file", "size": size, "sha256": value.hexdigest()}
    return result


def directory_inventory(path):
    require(path.is_dir() and not path.is_symlink(), "Invalid restored directory")
    result = {}
    for item in sorted(path.rglob("*")):
        require(not item.is_symlink(), "Restored symlink is not allowed")
        name = item.relative_to(path).as_posix()
        if item.is_dir():
            result[name] = {"kind": "directory"}
        else:
            require(item.is_file(), "Restored special file is not allowed")
            result[name] = {"kind": "file", "size": item.stat().st_size, "sha256": digest(item)}
    return result


def inventory_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Rehearsal:
    def __init__(self, output):
        require(not output.exists(), "Use a fresh report; previous failures must be retained")
        self.output = output
        self.exe = shutil.which("docker")
        require(bool(self.exe), "Docker executable unavailable")
        self.identity = uuid4().hex
        self.restore_id = None
        self.creation_attempted = False
        self.report = {
            "scope": "hardened isolated mount-snapshot restore with actual uvicorn Agent",
            "project": PROJECT, "business_sha": BUSINESS_SHA,
            "started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
            "execute": True, "steps": [], "errors": [], "automatic_cleanup": False,
            "resource": {"name": RESTORE_NAME, "id": None, "created": False, "retained": True},
            "direct_external_paid_calls": 0,
            "boundaries": [
                "Restores three file archives and starts an independent real Agent using restored runtime/Chroma.",
                "The existing running Java service/dataset identity is reused. No PostgreSQL database is restored or modified by this runner's explicit operations.",
                "This is not independent PostgreSQL disaster recovery, an empty-daemon image restore, off-host recovery, live-provider RAG or general answer-quality proof.",
                "knowledge_uploads is restored and hash-checked but not mounted into Agent; /app/logs and /tmp are fresh tmpfs, not persistent log recovery.",
                "Source maintenance restores the original four states; finally only stops this run's uniquely owned new restore Agent. All objects/files/backups remain retained.",
                "Readiness/identity may read the same live Java service. No planning/model/map/remote-embedding request is made by this runner; component read uses explicit deterministic embeddings.",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(self.report, stream, indent=2)

    def save(self):
        self.output.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def stage(self, value):
        self.report["stage"] = value
        self.report["steps"].append({"stage": value, "at": datetime.now(timezone.utc).isoformat()})
        self.save()

    def command(self, *args, data=None, environment=None, timeout=120):
        # Resolve Docker absolutely before forwarding container PATH/defaults.
        # Credentials occur only in the child environment, never CLI/report.
        result = subprocess.run([self.exe, *map(str, args)], input=data, env=environment,
                                capture_output=True, timeout=timeout, check=False)
        require(result.returncode == 0, "Docker operation failed; raw diagnostics kept out of public report")
        return result.stdout

    def inspected(self, kind, identity):
        result = json.loads(self.command(kind, "inspect", identity))
        require(len(result) == 1, "Ambiguous Docker identity")
        return result[0]

    def find_owned_restore(self):
        ids = self.command("ps", "-aq", "--no-trunc", "--filter", "name=^/" + RESTORE_NAME + "$",
                           "--filter", "label=com.tripplanner.hardened-file-restore-run=" + self.identity).decode().split()
        require(len(ids) <= 1, "Ambiguous new restore ownership")
        if not ids:
            return None
        item = self.inspected("container", ids[0])
        require(item["Name"] == "/" + RESTORE_NAME and
                (item["Config"].get("Labels") or {}).get("com.tripplanner.hardened-file-restore-run") == self.identity,
                "New resource ownership changed")
        return item

    def execute(self):
        began = time.perf_counter()
        source_before = None
        try:
            self.stage("verify_fixed_release_source_and_probe")
            release, probe = load(RELEASE), load(PROBE_REPORT)
            require(release["business_sha"] == BUSINESS_SHA, "Release business SHA mismatch")
            require(probe.get("passed") is True, "Canonical runtime probe did not pass")
            canary = probe["data"]["persistent_independent_chroma_roundtrip"]
            probe_id = canary["probe_id"]
            require(re.fullmatch(r"[a-f0-9]{32}", probe_id or "") is not None and probe.get("probe_id") == probe_id,
                    "Invalid or mixed probe identity")
            canary_directory = "/app/data/chroma/.hardened-chroma-probe-" + probe_id
            require(canary["retained_database_directory"] == canary_directory, "Unexpected canary database path")
            found = services(PROJECT); source_before = snapshot(found)
            self.report["source_before"] = source_before
            images = {}
            for role in ("backend", "agent", "postgres", "frontend"):
                expected = release["images"][role]
                require(found[role]["Image"] == expected["id"], "Selected source image differs from fixed release")
                image = self.inspected("image", expected["id"])
                require(image["Id"] == expected["id"] and image.get("RootFS", {}).get("Layers") == expected["rootfs_layers"],
                        "Exact image/RootFS differs from fixed release")
                if role != "postgres":
                    require(expected["revision"] == BUSINESS_SHA and
                            (image["Config"].get("Labels") or {}).get("org.opencontainers.image.revision") == BUSINESS_SHA,
                            "Application image business SHA mismatch")
                images[role] = {"image_id": image["Id"], "fixed_release_matches": True,
                                "revision": expected.get("revision"), "official_postgres_separately_pinned": role == "postgres"}
            self.report["images"] = images
            canonical = found["agent"]; host = canonical["HostConfig"]
            require(host.get("ReadonlyRootfs") is True and "ALL" in (host.get("CapDrop") or []) and
                    not host.get("CapAdd") and any(value in {"no-new-privileges", "no-new-privileges:true"}
                    for value in host.get("SecurityOpt") or []), "Canonical Agent hardening differs")
            require(host.get("Tmpfs") == EXPECTED_TMPFS, "Canonical tmpfs differs from reviewed base")
            require(host.get("Memory", 0) > 0 and host.get("NanoCpus", 0) > 0 and host.get("PidsLimit", 0) > 0,
                    "Canonical resource limits unavailable")
            require(host.get("LogConfig", {}).get("Type") == "json-file" and
                    set(host.get("LogConfig", {}).get("Config", {})) <= {"max-size", "max-file"}, "Unexpected canonical log configuration")
            cmd = canonical["Config"].get("Cmd") or []
            require(any(PurePosixPath(value).name == "uvicorn" for value in cmd) and "app.agent_api.main:app" in cmd,
                    "Canonical service is not real uvicorn")
            values = env_map(canonical)
            for key, expected in {"DATA_DIR": "/app/runtime", "CHROMA_DIR": "/app/data/chroma",
                                  "LOG_DIR": "/app/logs", "PYTHONDONTWRITEBYTECODE": "1"}.items():
                require(values.get(key) == expected, "Canonical writable-path environment differs")
            require(re.fullmatch(r"[A-Za-z0-9_-]{16,64}", values.get("DATASET_ID", "")) is not None,
                    "Canonical dataset identity missing")
            require(not self.command("ps", "-aq", "--no-trunc", "--filter", "name=^/" + RESTORE_NAME + "$").strip(),
                    "Fixed restore container already exists; never overwrite/remove it")
            network = self.inspected("network", NETWORK)
            require((network.get("Labels") or {}).get("com.docker.compose.project") == PROJECT and
                    network.get("Internal") is True and NETWORK in canonical["NetworkSettings"]["Networks"],
                    "Selected internal network is not the hardened project")
            # Refuse a shared writable snapshot; in particular the browser
            # provider fixture must have its own Chroma/runtime volumes.
            self.report["snapshot_writer_checks"] = []
            for name, (role, target) in MOUNTS.items():
                mount = next(item for item in found[role]["Mounts"] if item["Destination"] == target)
                require(mount["Type"] == "volume", "Expected isolated named persistent volume")
                refs = self.command("ps", "-q", "--no-trunc", "--filter", "volume=" + mount["Name"]).decode().split()
                for identity in refs:
                    item = self.inspected("container", identity)
                    if identity in {found["backend"]["Id"], canonical["Id"]}:
                        continue
                    require(not any(part.get("RW") and part.get("Name") == mount["Name"] for part in item.get("Mounts", [])),
                            "Another running writer shares the snapshot volume")
                self.report["snapshot_writer_checks"].append({"archive": name, "volume": mount["Name"], "no_other_running_writer": True})
            self.report.update(probe_report_sha256=digest(PROBE_REPORT), probe_script_sha256=digest(PROBE),
                               release_metadata_sha256=digest(RELEASE), probe_id=probe_id)

            self.stage("maintenance_backup_and_local_replica")
            try:
                with redirect_stdout(io.StringIO()):
                    maintenance = run_backup(PROJECT, BACKUP_ROOT, REPLICA_ROOT, execute=True)
                self.report["maintenance"] = maintenance
            except MaintenanceFailure as error:
                self.report["maintenance"] = error.report
                raise RuntimeError("Source maintenance did not fully succeed") from None
            require(maintenance.get("passed") is True and maintenance.get("original_running_state_restored") is True,
                    "Backup did not restore source states/readiness")
            require(snapshot(services(PROJECT)) == source_before, "Source identities/states/mounts changed after backup")

            self.stage("verify_all_archive_hashes_and_extract_fresh")
            backup_path = Path(maintenance["backup"]).resolve(strict=True)
            manifest = validate_manifest(backup_path)
            require(manifest.get("project") == PROJECT and manifest.get("schema") == "Java/Flyway-v2" and
                    all(manifest["images"].get(role) == release["images"][role]["id"] for role in images), "Backup fixed image/project identity mismatch")
            parent = VISUAL_ROOT.resolve(strict=True)
            require(parent.is_dir() and not parent.is_symlink(), "Task visualization parent unavailable")
            destination = parent / ("trip-hardened-file-restore-20261026-" + datetime.now(timezone.utc).strftime("%H%M%S") + "-" + self.identity[:8])
            require(destination.parent == parent and not destination.exists(), "Restore target must be fresh under this task")
            destination.mkdir(mode=0o700, exist_ok=False)
            self.report["restore_directory"] = str(destination)
            self.report["archives"] = {}
            inventories = {}
            for name, (role, target) in MOUNTS.items():
                entry = manifest["mounts"][name]
                require(entry["service"] == role and entry["destination"] == target, "Archive mount contract differs")
                archive = (backup_path / entry["archive"]).resolve(strict=True)
                require(archive.is_relative_to(backup_path) and entry["archive"] in manifest["sha256"], "Unprotected mount archive")
                original = tar_inventory(archive)
                restored = destination / name
                extract_safe(archive, restored)
                actual = directory_inventory(restored)
                require(actual == original, "Full extracted file inventory differs from archive")
                marker = restored / ".dataset-id"
                require(marker.is_file() and marker.stat().st_size <= 256 and marker.read_text(encoding="utf-8").strip() == values["DATASET_ID"],
                        "Restored persistent marker differs; refusing enrollment/overwrite")
                # Detailed names/hashes stay with private retained restored files.
                with (destination / (name + "-inventory.json")).open("x", encoding="utf-8") as stream:
                    json.dump(original, stream, ensure_ascii=False, indent=2)
                inventories[name] = original
                self.report["archives"][name] = {"archive_sha256": manifest["sha256"][entry["archive"]],
                    "inventory_sha256": inventory_digest(original), "file_count": sum(item["kind"] == "file" for item in original.values()),
                    "full_inventory_matches": True, "marker_matches_without_overwrite": True, "restored_to": str(restored)}
                self.save()
            for key, name, prefix in (("writable_DATA_DIR", "agent-runtime", "/app/runtime"),
                                      ("writable_CHROMA_DIR", "chroma", "/app/data/chroma")):
                item = probe["data"][key]
                relative = PurePosixPath(item["retained_file"]).relative_to(prefix).as_posix()
                expected = {"kind": "file", "size": item["bytes"], "sha256": item["sha256"]}
                require(inventories[name].get(relative) == expected, "Persistent source write/readback canary missing or changed")
            sqlite_key = ".hardened-chroma-probe-" + probe_id + "/chroma.sqlite3"
            sqlite = inventories["chroma"].get(sqlite_key, {})
            require(sqlite.get("kind") == "file" and sqlite.get("size") == canary["sqlite_bytes"] and sqlite["size"] > 0,
                    "Independent Chroma SQLite canary missing or changed")
            self.report["canary_snapshot"] = {"database_directory": canary_directory, "probe_id": probe_id,
                "sqlite_sha256": sqlite["sha256"], "sqlite_bytes": sqlite["size"], "source_runtime_and_chroma_write_canaries_match": True}

            self.stage("create_independent_hardened_restore_agent")
            image = self.inspected("image", release["images"]["agent"]["id"])
            defaults = env_map(image)
            canonical_values = {**values, "DATASET_ENROLL_NEW": "false"}
            forwarded = {key: value for key, value in canonical_values.items() if defaults.get(key) != value}
            require(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in forwarded), "Unsafe environment key")
            require({**defaults, **forwarded} == canonical_values, "Cannot exactly reproduce canonical environment")
            args = ["run", "-d", "--pull", "never", "--name", RESTORE_NAME, "--network", NETWORK,
                    "--label", "com.tripplanner.optimization=20261026",
                    "--label", "com.tripplanner.validation=hardened-file-restore",
                    "--label", "com.docker.compose.project=" + RESTORE_PROJECT,
                    "--label", "com.docker.compose.service=agent", "--label", "com.docker.compose.oneoff=True",
                    "--label", "com.tripplanner.hardened-file-restore-run=" + self.identity,
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--read-only", "--restart", "no",
                    "--memory", str(host["Memory"]), "--cpus", str(host["NanoCpus"] / 1e9), "--pids-limit", str(host["PidsLimit"]),
                    "--log-driver", "json-file"]
            for key, value in host["LogConfig"].get("Config", {}).items():
                args += ["--log-opt", key + "=" + value]
            for target, options in host["Tmpfs"].items():
                args += ["--tmpfs", target + ":" + options]
            for name, target in (("agent-runtime", "/app/runtime"), ("chroma", "/app/data/chroma")):
                args += ["--mount", "type=bind,source=" + str(destination / name) + ",target=" + target]
            if canonical["Config"].get("User"):
                args += ["--user", canonical["Config"]["User"]]
            if canonical["Config"].get("WorkingDir"):
                args += ["--workdir", canonical["Config"]["WorkingDir"]]
            for key in sorted(forwarded):
                args += ["-e", key]
            args.append(image["Id"])
            self.creation_attempted = True
            self.command(*args, environment={**os.environ, **forwarded})
            restored_agent = self.find_owned_restore()
            require(restored_agent is not None, "New Agent identity could not be confirmed")
            self.restore_id = restored_agent["Id"]
            self.report["resource"].update(id=self.restore_id, image_id=restored_agent["Image"], created=True)
            require(restored_agent["Image"] == image["Id"] and env_map(restored_agent) == canonical_values,
                    "Restored Agent image/environment differs")
            actual_host = restored_agent["HostConfig"]
            require(actual_host.get("ReadonlyRootfs") is True and actual_host.get("Tmpfs") == EXPECTED_TMPFS and
                    "ALL" in (actual_host.get("CapDrop") or []) and not actual_host.get("CapAdd") and
                    any(value in {"no-new-privileges", "no-new-privileges:true"} for value in actual_host.get("SecurityOpt") or []),
                    "Restored Agent hardening differs")
            self.report["restore_boundary"] = {"read_only_rootfs": True, "cap_drop_all": True, "no_new_privileges": True,
                "tmpfs": EXPECTED_TMPFS, "canonical_environment_matches_except_enroll_false": True,
                "secrets_forwarded_by_environment_only": True, "new_network_created": False, "postgres_restored": False}

            self.stage("transfer_probe_as_actual_appuser_and_verify_uvicorn_readiness")
            # Docker cp INTO a read-only-rootfs container is rejected by the
            # daemon even for a writable tmpfs. Write stdin via the app UID.
            transfer = "import hashlib,json,sys; from pathlib import Path; p=Path('/tmp/hardened-runtime-probe.py'); b=sys.stdin.buffer.read(); p.open('xb').write(b); print(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"
            receipt = json.loads(self.command("exec", "-i", self.restore_id, "python", "-c", transfer, data=PROBE.read_bytes()))
            require(receipt["sha256"] == digest(PROBE), "Transferred probe bytes differ")
            boundary_code = "import importlib.util,json; s=importlib.util.spec_from_file_location('restore_probe','/tmp/hardened-runtime-probe.py'); p=importlib.util.module_from_spec(s); s.loader.exec_module(p); print(json.dumps({'process':p.process_boundary(),'rootfs':p.rootfs_boundary(True),'readiness':p.real_readiness()}))"
            ready = None
            for attempt in range(90):
                try:
                    ready = json.loads(self.command("exec", self.restore_id, "python", "-c", boundary_code, timeout=20))
                    break
                except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                    if attempt == 89:
                        raise RuntimeError("Actual restored uvicorn did not become ready") from None
                    time.sleep(1)
            require(ready["readiness"]["status"] == 200 and ready["rootfs"]["read_only"] is True, "Real readiness/boundary did not pass")
            self.report["actual_restore_agent"] = ready

            self.stage("fresh_process_reads_restored_independent_chroma")
            raw = self.command("exec", self.restore_id, "python", "/tmp/hardened-runtime-probe.py",
                               "--chroma-child", "read", "--chroma-directory", canary_directory, "--probe-id", probe_id, timeout=120)
            child = json.loads(raw.decode().strip().splitlines()[-1])
            require(child.get("passed") is True, "Restored Chroma child did not pass")
            data = child["data"]
            require(data.get("probe_id") == probe_id and data.get("database_directory") == canary_directory and
                    data.get("readback_verified") is True and data.get("retrieval_verified") is True and
                    data.get("stored_count") == canary["operations"][-1]["stored_count"], "Restored Chroma readback identity/content/count differs")
            self.report["restored_chroma_fresh_process"] = data
            self.report["passed"] = True
        except BaseException as error:
            self.report["passed"] = False
            self.report["errors"].append({"stage": self.report.get("stage"), "error_type": type(error).__name__})
        finally:
            if self.creation_attempted:
                try:
                    own = self.find_owned_restore()
                    if own is not None:
                        self.restore_id = own["Id"]
                        self.report["resource"].update(id=self.restore_id, created=True, image_id=own["Image"])
                        if own["State"]["Running"]:
                            self.command("stop", "--time", "30", self.restore_id, timeout=60)
                        stopped = self.inspected("container", self.restore_id)
                        require(not stopped["State"]["Running"], "New restore Agent remained running")
                        self.report["resource"].update(final_state=stopped["State"]["Status"], stop_verified=True, retained=True)
                except BaseException as error:
                    self.report["passed"] = False
                    self.report["errors"].append({"stage": "stop_only_this_runs_new_agent", "error_type": type(error).__name__})
            if source_before is not None:
                try:
                    self.report["source_after"] = snapshot(services(PROJECT))
                    same = self.report["source_after"] == source_before
                    self.report["source_four_original_id_image_state_mounts_unchanged"] = same
                    require(same, "Original source four states/identities/mounts were not restored")
                except BaseException as error:
                    self.report["passed"] = False
                    self.report["errors"].append({"stage": "verify_source_four_original_states", "error_type": type(error).__name__})
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.report["elapsed_seconds"] = round(time.perf_counter() - began, 3)
            self.save()
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Coordinator-only explicit isolated backup/restore execution")
    parser.add_argument("--output", type=Path, default=EVIDENCE / "hardened-backup-file-restore-result.json")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"execute": False, "project": PROJECT, "business_sha": BUSINESS_SHA,
            "probe_report_required": str(PROBE_REPORT), "release_required": str(RELEASE),
            "new_restore_agent": RESTORE_NAME, "restore_parent": str(VISUAL_ROOT),
            "backup_root": str(BACKUP_ROOT), "local_replica_root": str(REPLICA_ROOT),
            "docker_or_http_executed": False, "automatic_cleanup": False}))
        return 0
    try:
        report = Rehearsal(args.output).execute()
    except BaseException as error:
        print(json.dumps({"passed": False, "error_type": type(error).__name__, "report_created": args.output.exists()}))
        return 1
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "retained_restore_agent": report["resource"],
                      "failed_steps": report["errors"], "automatic_cleanup": False}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
