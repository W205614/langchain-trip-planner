"""Evidence-only exact, window-gated cleanup. Operator execution only.

Never changes the approved plan, removes images/files, prunes, forces removal,
or removes anonymous volumes. Default prints the prerequisite contract only.
Use --preflight for read-only checks or --execute after the receipt exists.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
SHARED = Path(r"C:\Users\25452\.codex\visualizations\2026\10\25\01a172e2-8a39-7783-b536-b6dbe2f478cd\execution-20261026")
WINDOW = SHARED / "deployment-window.json"
GRANT = SHARED / "trip-deployment-window-grant.json"
PLAN = EVIDENCE / "cleanup-plan.json"
PLAN_SHA = "761c9f864e0e01b98691cd29a4ab2673ab59881e8ed200929673c3a42a0e3dca"
BUSINESS_SHA = "220257ad32e50d08cb987d565882f8a038e36c6f"
COUNTS = {"containers": 32, "networks": 7, "named_synthetic_volumes": 14, "images": 0}
SYNTHETIC = {"trip-opt-baseline-20261026", "trip-opt-candidate-20261026",
             "trip-opt-candidate-20261026-v2", "trip-opt-unit-20261026"}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def metadata_labels(item):
    return {key: value for key, value in (item or {}).items()
            if key.startswith(("com.docker.compose.", "com.tripplanner.")) or
            key in {"org.opencontainers.image.revision", "org.opencontainers.image.source", "org.opencontainers.image.version"}}


def normalized_mounts(mounts, expected=None):
    result = {}
    original = {mount["Destination"]: mount for mount in expected or []}
    for mount in mounts:
        destination = mount.get("Destination")
        require(destination and destination not in result, "unknown_or_duplicate_mount_destination")
        copy = dict(mount)
        # The sole permitted extra field: audit omitted Docker's local Driver.
        # Keep every other actual field and every originally recorded value.
        if expected is not None and "Driver" not in original.get(destination, {}) and "Driver" in copy:
            require(copy.get("Type") == "volume" and copy["Driver"] == "local", "unknown_added_mount_driver")
            copy.pop("Driver")
        result[destination] = copy
    return result


def overlap(left, right):
    a, b = str(left).replace("\\", "/").rstrip("/").casefold(), str(right).replace("\\", "/").rstrip("/").casefold()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def sanitize_container(item):
    return {"id": item["Id"], "name": item["Name"].lstrip("/"), "image": item["Image"],
            "labels": metadata_labels(item.get("Config", {}).get("Labels")),
            "mounts": item.get("Mounts", []), "running": item["State"]["Running"],
            "status": item["State"]["Status"],
            "networks": {name: value.get("NetworkID") for name, value in item.get("NetworkSettings", {}).get("Networks", {}).items()},
            "network_mode": item.get("HostConfig", {}).get("NetworkMode")}


class Cleanup:
    def __init__(self, receipt, output, execute):
        require(receipt and receipt.is_file(), "root_prerequisite_receipt_required")
        require(output.resolve().is_relative_to(EVIDENCE.resolve()) and not output.exists(), "fresh_evidence_output_required")
        self.receipt_path, self.output, self.execute = receipt, output, execute
        self.receipt_sha = sha(receipt)
        self.grant_sha = sha(GRANT)
        self.plan = load(PLAN)
        self.grant = load(GRANT)
        self.allowed = self.grant["cleanup_exact_allowed"]
        self.gate()
        require({key: len(value) for key, value in self.allowed.items()} == COUNTS, "approved_batch_counts_changed")
        require(self.allowed == self.plan["window_after_validation_candidates"], "grant_plan_exact_metadata_differs")
        require(not self.allowed["images"] and not self.grant.get("images_authorized_for_cleanup"), "image_deletion_forbidden")
        self.ids = {"container": {item["id"] for item in self.allowed["containers"]},
                    "network": {item["id"] for item in self.allowed["networks"]},
                    "volume": {item["id"] for item in self.allowed["named_synthetic_volumes"]}}
        require(all(len(self.ids[kind]) == count for kind, count in (("container", 32), ("network", 7), ("volume", 14))), "duplicate_approved_identity")
        inventory_path = ROOT / self.plan["inventory_file"]
        require(sha(inventory_path) == self.plan["inventory_sha256"], "approved_inventory_hash_changed")
        inventory = load(inventory_path)
        self.original = {"container": {item["id"]: item for item in inventory["containers"]},
                         "network": {item["id"]: item for item in inventory["networks"]},
                         "volume": {item["name"]: item for item in inventory["volumes"]}}
        require(all(identity in self.original[kind] for kind in self.ids for identity in self.ids[kind]), "original_metadata_missing")
        protected = self.grant["protected"]
        self.protected = {"container": set(), "network": set(), "volume": set()}
        self.protected_paths = []
        for key, values in protected.items():
            if not isinstance(values, list):
                continue
            if "container_ids" in key:
                self.protected["container"].update(values)
            elif "network_ids" in key:
                self.protected["network"].update(values)
            elif "volume_names" in key:
                self.protected["volume"].update(values)
            elif "directory_paths" in key or key == "hardened_restore_and_backup_paths":
                self.protected_paths.extend(values)
        self.receipt = load(receipt)
        daily = self.receipt["daily_deployment"]["current_container_ids"]
        require(isinstance(daily, dict) and set(daily) == {"backend", "agent", "postgres", "frontend"}, "exact_current_daily_four_required")
        require(all(re.fullmatch(r"[a-f0-9]{64}", identity) for identity in daily.values()), "full_daily_ids_required")
        self.protected["container"].update(daily.values())
        for kind, values in self.receipt.get("rollback_required_resources", {}).items():
            require(kind in self.protected and isinstance(values, list), "unknown_rollback_protection_kind")
            self.protected[kind].update(values)
        self.exe = shutil.which("docker")
        require(bool(self.exe), "docker_unavailable")
        self.run_id = uuid4().hex
        self.logs = ROOT / (".codex-optimization/exact-cleanup-private-" + self.run_id)
        self.logs.mkdir(parents=True, exist_ok=False)
        self.removed = {kind: set() for kind in self.ids}
        self.cache = {}
        self.report = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
            "execute": execute, "passed": False, "preflight_complete": False,
            "approved_plan_sha256": PLAN_SHA, "grant_sha256": self.grant_sha,
            "root_prerequisite_receipt": str(receipt), "receipt_sha256": self.receipt_sha,
            "business_artifact_source_sha": BUSINESS_SHA, "approved_counts": COUNTS,
            "manifest": [], "actions": [], "skips": [], "errors": [], "images_removed": 0, "files_deleted": 0,
            "boundary": "Exact approved objects only; every skip retained. No image/anonymous-volume/file deletion, force, prune, down or network disconnect. Whole batch metadata is recorded before the first lifecycle operation.",
            "private_error_log_directory": str(self.logs)}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(self.report, stream, indent=2)

    def save(self):
        self.output.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def gate(self):
        window, grant = load(WINDOW), load(GRANT)
        require(window.get("owner") == grant.get("owner") == "Trip" and window.get("state") == grant.get("state") == "granted", "window_not_granted_to_Trip")
        require(window.get("cleanup_plan_sha256") == grant.get("cleanup_plan_sha256") == PLAN_SHA == sha(PLAN), "approved_plan_hash_changed")
        require(sha(GRANT) == self.grant_sha and sha(self.receipt_path) == self.receipt_sha, "grant_or_receipt_changed")
        receipt = load(self.receipt_path)
        require(receipt.get("project") == "langchain-trip-planner" and receipt.get("business_artifact_source_sha") == BUSINESS_SHA == grant.get("business_artifact_source_sha"), "receipt_project_source_mismatch")
        deployment = receipt.get("daily_deployment", {})
        require(deployment.get("passed") is True and deployment.get("fresh_backup_verified") is True and deployment.get("rollback_archive_closure_verified") is True, "daily_backup_and_deployment_receipt_incomplete")
        require(receipt.get("daily_readback", {}).get("passed") is True, "daily_readback_not_passed")
        require(receipt.get("windows_backup_task", {}).get("passed") is True and receipt["windows_backup_task"].get("installed") is True, "windows_task_not_installed_successfully")

    def call(self, *args):
        allowed = {("container", "inspect"), ("network", "inspect"), ("volume", "inspect"),
                   ("network", "ls"), ("volume", "ls")}
        require(args[0] == "ps" or args[:2] in allowed, "unapproved_read_command")
        result = subprocess.run([self.exe, *args], capture_output=True, timeout=60)
        if result.returncode:
            # No inspect stdout/Env is persisted. Raw stderr stays private.
            with (self.logs / "errors.log").open("ab") as stream:
                stream.write(result.stderr + b"\n")
            raise RuntimeError("inspect_missing_or_unknown")
        return result.stdout

    def inspect(self, kind, identity):
        data = json.loads(self.call(kind, "inspect", identity))
        require(len(data) == 1, "ambiguous_inspect")
        return data[0]

    def container_metadata(self, item):
        baseline = self.original["container"][item["Id"]]
        identity = item["Id"]
        require(identity not in self.protected["container"], "protected_container")
        require(item["Name"].lstrip("/") == baseline["name"] and item["Image"] == baseline["image_id"] and
                item.get("Created") == baseline.get("created"), "container_name_image_or_created_drift")
        actual_labels = metadata_labels(item["Config"].get("Labels"))
        require(actual_labels == baseline["labels"], "container_labels_drift")
        require(normalized_mounts(item.get("Mounts", []), baseline["mounts"]) == normalized_mounts(baseline["mounts"]), "mounts_changed")
        require(item["State"]["Status"] in {"running", "exited", "created"} and not item["State"].get("Paused"), "unknown_container_state")
        approval = next(value for value in self.allowed["containers"] if value["id"] == identity)
        require(approval["name"] == baseline["name"] and approval["image"] == baseline["image_id"] and
                normalized_mounts(approval["mounts"]) == normalized_mounts(baseline["mounts"]), "approval_original_metadata_differs")
        require(approval.get("project") in SYNTHETIC or any("security" in entry for entry in approval.get("evidence", [])), "synthetic_purpose_unproven")
        for mount in item.get("Mounts", []):
            require(mount.get("Name") not in self.protected["volume"], "protected_volume_mounted")
            if mount.get("RW") and mount.get("Source"):
                require(not any(overlap(mount["Source"], path) for path in self.protected_paths), "protected_writable_source")
                if mount.get("Type") == "bind":
                    present = self.refresh_new_containers()
                    for other_id, other in self.cache.items():
                        if other_id in present and other_id not in self.ids["container"]:
                            require(not any(part.get("Source") and overlap(mount["Source"], part["Source"])
                                            for part in other["mounts"]), "foreign_or_retained_writable_bind_reference")

    def volume_refs(self, name):
        ids = self.call("ps", "-aq", "--no-trunc", "--filter", "volume=" + name).decode().split()
        refs = []
        for identity in ids:
            item = self.inspect("container", identity)
            matching = [mount for mount in item.get("Mounts", []) if mount.get("Name") == name]
            require(bool(matching), "volume_filter_reference_unknown")
            refs.append({"id": identity, "project": (item["Config"].get("Labels") or {}).get("com.docker.compose.project")})
        return refs

    def refresh_new_containers(self):
        present = set(self.call("ps", "-aq", "--no-trunc").decode().split())
        for identity in present - set(self.cache):
            self.cache[identity] = sanitize_container(self.inspect("container", identity))
        return present

    def network_refs(self, item):
        present = self.refresh_new_containers()
        ids = set(self.call("ps", "-aq", "--no-trunc", "--filter", "network=" + item["Name"]).decode().split())
        ids.update((item.get("Containers") or {}).keys())
        for identity, value in self.cache.items():
            if identity in present and (item["Id"] in value["networks"].values() or item["Name"] in value["networks"] or value["network_mode"] in {item["Id"], item["Name"]}):
                ids.add(identity)
        return sorted(ids)

    def check(self, kind, identity, final=False):
        require(identity in self.ids[kind] and identity not in self.protected[kind], "not_approved_or_protected")
        item = self.inspect(kind, identity)
        if kind == "container":
            self.container_metadata(item)
            refs = {}
            for mount in item.get("Mounts", []):
                if mount.get("Name"):
                    refs[mount["Name"]] = self.volume_refs(mount["Name"])
                    require(all(ref["id"] in self.ids["container"] for ref in refs[mount["Name"]]), "foreign_or_retained_volume_reference")
            return {"actual": sanitize_container(item), "volume_references": refs}
        baseline = self.original[kind][identity]
        if kind == "network":
            require(item["Id"] == identity and item["Name"] == baseline["name"], "network_identity_drift")
            require(metadata_labels(item.get("Labels")) == baseline["labels"] and item.get("Driver") == baseline["driver"] and
                    item.get("Scope") == baseline["scope"] and item.get("Internal") == baseline["internal"] and item.get("IPAM") == baseline["ipam"], "network_metadata_drift")
            require(item.get("Created") == baseline.get("created"), "network_created_drift")
            refs = self.network_refs(item)
            require(all(ref in self.ids["container"] for ref in refs), "foreign_or_retained_network_reference")
            if final:
                require(not refs and not item.get("Containers"), "network_endpoints_or_declared_refs_remain")
            return {"actual": {"id": identity, "name": item["Name"], "labels": metadata_labels(item.get("Labels")), "driver": item["Driver"]}, "container_references": refs}
        require(not re.fullmatch(r"[a-f0-9]{64}", identity), "anonymous_volume_forbidden")
        require(item["Name"] == identity and item.get("Driver") == baseline["driver"] == "local" and
                item.get("Scope") == baseline["scope"] and item.get("Mountpoint") == baseline["mountpoint"] and
                item.get("CreatedAt") == baseline.get("created_at") and
                metadata_labels(item.get("Labels")) == baseline["labels"] and not item.get("Options"), "volume_metadata_drift_or_unknown")
        require((item.get("Labels") or {}).get("com.docker.compose.project") in SYNTHETIC and
                bool((item.get("Labels") or {}).get("com.docker.compose.volume")), "named_synthetic_volume_ownership_unproven")
        refs = self.volume_refs(identity)
        require(all(ref["id"] in self.ids["container"] for ref in refs), "foreign_or_retained_volume_reference")
        if final:
            require(not refs, "any_host_container_volume_reference_remains")
        return {"actual": {"name": identity, "driver": item["Driver"], "scope": item["Scope"], "mountpoint": item["Mountpoint"], "labels": metadata_labels(item.get("Labels"))}, "container_references": refs}

    def mutate(self, kind, identity, stop=False):
        self.gate()
        require(self.execute and identity in self.ids[kind] and identity not in self.protected[kind], "mutation_not_authorized")
        # No generic shell, -f, -v, --volumes, network disconnect, image command
        # or filesystem cleanup operation exists in this tool.
        args = ["container", "stop", "--time", "30", identity] if stop else [kind, "rm", identity]
        result = subprocess.run([self.exe, *args], capture_output=True, timeout=90)
        entry = {"kind": kind, "id": identity, "operation": "stop" if stop else "rm", "returncode": result.returncode,
                 "at": datetime.now(timezone.utc).isoformat()}
        self.report["actions"].append(entry); self.save()
        if result.returncode:
            with (self.logs / "errors.log").open("ab") as stream:
                stream.write(result.stderr + b"\n")
            raise RuntimeError("nonforce_docker_operation_failed")
        if not stop:
            self.removed[kind].add(identity)

    def run(self):
        protected_before = {}
        try:
            self.gate()
            all_ids = self.call("ps", "-aq", "--no-trunc").decode().split()
            for start in range(0, len(all_ids), 35):
                for item in json.loads(self.call("container", "inspect", *all_ids[start:start + 35])):
                    self.cache[item["Id"]] = sanitize_container(item)
            release = load(EVIDENCE / "release-candidate-images-220257a.json")
            for role, identity in self.receipt["daily_deployment"]["current_container_ids"].items():
                item = self.inspect("container", identity)
                labels = item["Config"].get("Labels") or {}
                require(labels.get("com.docker.compose.project") == "langchain-trip-planner" and labels.get("com.docker.compose.service") == role and
                        item["Image"] == release["images"][role]["id"], "current_daily_receipt_identity_mismatch")
                for mount in item.get("Mounts", []):
                    if mount.get("Name"):
                        self.protected["volume"].add(mount["Name"])
                    if mount.get("Source"):
                        self.protected_paths.append(mount["Source"])
            for identity in self.protected["container"] & set(self.cache):
                protected_before[identity] = self.cache[identity]
            self.report["protected_present_before"] = protected_before
            for kind, name in (("container", "containers"), ("network", "networks"), ("volume", "named_synthetic_volumes")):
                for approval in self.allowed[name]:
                    entry = {"kind": kind, "id": approval["id"], "eligible_at_preflight": False}
                    try:
                        entry.update(self.check(kind, approval["id"])); entry["eligible_at_preflight"] = True
                    except Exception as error:
                        entry["skip_reason"] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    self.report["manifest"].append(entry)
            self.report["preflight_complete"] = True
            self.report["preflight_saved_before_any_lifecycle_operation"] = not self.report["actions"]
            self.save()
            for entry in self.report["manifest"]:
                if not entry["eligible_at_preflight"]:
                    self.report["skips"].append({"kind": entry["kind"], "id": entry["id"], "reason": entry["skip_reason"], "retained": True})
            if self.execute:
                for kind in ("container", "network", "volume"):
                    for entry in [value for value in self.report["manifest"] if value["kind"] == kind and value["eligible_at_preflight"]]:
                        identity = entry["id"]
                        try:
                            self.gate()
                            checked = self.check(kind, identity, final=kind != "container")
                            if kind == "container" and checked["actual"]["running"]:
                                self.mutate(kind, identity, stop=True)
                                self.check(kind, identity)
                            # Repeat identity/static metadata/references directly
                            # before rm, including after an authorized stop.
                            self.check(kind, identity, final=kind != "container")
                            self.mutate(kind, identity)
                        except Exception as error:
                            self.report["skips"].append({"kind": kind, "id": identity, "reason": str(error) if isinstance(error, RuntimeError) else type(error).__name__, "retained_if_still_present": True})
                        self.save()
            for identity, before in protected_before.items():
                actual = sanitize_container(self.inspect("container", identity))
                require(all(actual[key] == before[key] for key in ("id", "name", "image", "labels", "mounts", "running", "networks", "network_mode")), "protected_container_changed")
            if self.execute:
                remaining = {
                    "container": set(self.call("ps", "-aq", "--no-trunc").decode().split()),
                    "network": set(self.call("network", "ls", "-q", "--no-trunc").decode().split()),
                    "volume": set(self.call("volume", "ls", "-q").decode().split()),
                }
                self.report["post_removal_absence_verified"] = {
                    kind: sorted(values - remaining[kind]) for kind, values in self.removed.items()}
                self.report["removed_identity_or_name_now_present_again"] = {
                    kind: sorted(values & remaining[kind]) for kind, values in self.removed.items()}
                require(not any(values & remaining[kind] for kind, values in self.removed.items()),
                        "removed_identity_or_name_reappeared_no_retry")
            self.report["protected_present_metadata_unchanged"] = True
            self.report["passed"] = True
        except BaseException as error:
            self.report["errors"].append({"error_type": type(error).__name__, "reason": str(error) if isinstance(error, RuntimeError) else "unexpected_operation_error"})
        self.report["removed"] = {kind: sorted(values) for kind, values in self.removed.items()}
        self.report["removed_counts"] = {kind: len(values) for kind, values in self.removed.items()}
        self.report["all_approved_objects_removed"] = all(self.removed[kind] == identities for kind, identities in self.ids.items())
        self.report["status"] = "executed_with_exact_results_and_retained_skips" if self.execute else "read_only_preflight"
        self.report["finished_at"] = datetime.now(timezone.utc).isoformat(); self.save()
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prerequisite-receipt", type=Path)
    parser.add_argument("--output", type=Path, default=EVIDENCE / "exact-window-cleanup-result.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if not args.execute and not args.preflight:
        print(json.dumps({"execute": False, "plan_sha256": PLAN_SHA, "approved_counts": COUNTS,
            "receipt_contract": {"project": "langchain-trip-planner", "business_artifact_source_sha": BUSINESS_SHA,
                "daily_deployment": {"passed": True, "fresh_backup_verified": True, "rollback_archive_closure_verified": True,
                                     "current_container_ids": {role: "FULL_ID" for role in ("backend", "agent", "postgres", "frontend")}},
                "daily_readback": {"passed": True}, "windows_backup_task": {"passed": True, "installed": True}},
            "docker_executed": False, "images_or_files_deleted": 0}))
        return 0
    try:
        result = Cleanup(args.prerequisite_receipt, args.output, args.execute).run()
    except BaseException as error:
        print(json.dumps({"passed": False, "error_type": type(error).__name__, "reason": str(error) if isinstance(error, RuntimeError) else "invalid_inputs"})); return 1
    print(json.dumps({"passed": result["passed"], "report": str(args.output), "status": result["status"],
                      "removed_counts": result["removed_counts"], "all_approved_objects_removed": result["all_approved_objects_removed"],
                      "skips": len(result["skips"]), "images_removed": 0, "files_deleted": 0}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
