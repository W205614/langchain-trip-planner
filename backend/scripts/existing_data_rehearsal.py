"""Plan or run existing-data upgrade and independent snapshot rollback clones.

Default only verifies private backup files and writes a command/resource plan.
--execute creates isolated clones; never stops or changes daily containers and
never removes Docker resources. The coordinator runs it serially after builds.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import ipaddress
import json
from pathlib import Path
import re
import sys
import time

from deployment_assets import docker, services, validate_manifest, database_summary
from restore_java_backup import ROOT, restore, RestoreVerificationFailure


def daily_snapshot(project):
    return {name: {"id": item["Id"], "image": item["Image"], "running": item["State"]["Running"],
                   "mounts": [{"type": mount["Type"], "source": mount.get("Name") or mount["Source"],
                               "destination": mount["Destination"]} for mount in item["Mounts"]]}
            for name, item in services(project).items()}


def preflight(backup, output, candidate_image=None, upgrade_subnet=None, rollback_subnet=None):
    backup, output = backup.resolve(strict=True), output.resolve()
    if output.exists() or output == ROOT or ROOT in output.parents or output == backup or backup in output.parents:
        raise ValueError("Rehearsal output must be a fresh external directory outside repository and backup")
    if candidate_image and not re.fullmatch(r"sha256:[a-f0-9]{64}", candidate_image):
        raise ValueError("Candidate must be a fixed local sha256 image ID")
    networks = [ipaddress.ip_network(value, strict=True) for value in (upgrade_subnet, rollback_subnet) if value]
    if len(networks) == 2 and networks[0].overlaps(networks[1]):
        raise ValueError("Upgrade and rollback clones require distinct nonoverlapping subnets")
    manifest = validate_manifest(backup)
    if manifest.get("schema") != "Java/Flyway-v2":
        raise ValueError("This runner refuses the V1 path that creates a database in the daily PostgreSQL")
    required = {"database.dump", "database-summary.json", "database-columns.json", "runtime/backend.json"}
    if not required <= manifest["sha256"].keys():
        raise ValueError("Existing-data rehearsal requires original columns, summary and protected runtime snapshot")
    if not {"knowledge_uploads", "chroma", "agent-runtime"} <= manifest["mounts"].keys():
        raise ValueError("Existing-data rehearsal requires the complete persistent mount set")
    summary = json.loads((backup / "database-summary.json").read_text(encoding="utf-8"))
    columns = json.loads((backup / "database-columns.json").read_text(encoding="utf-8"))
    if set(summary) != set(columns):
        raise ValueError("Original backup table summary and column metadata disagree")
    table_counts = {table: int(value.split(":")[0]) for table, value in summary.items()}
    if table_counts.get("trip_records", 0) < 1:
        raise ValueError("Existing-data rehearsal cannot be proven with an empty itinerary database")
    plan = {
        "mode": "existing_data_upgrade_and_independent_snapshot_rollback",
        "created_at": datetime.now(timezone.utc).isoformat(), "execute": False, "passed": False,
        "preflight_verified": True, "candidate_image_id": candidate_image,
        "ready_to_execute": candidate_image is not None,
        "local_image_availability": "not_checked_in_plan_mode",
        "backup": str(backup), "backup_created_at": manifest.get("created_at"),
        "backup_schema": manifest["schema"], "source_project": manifest.get("project"),
        "table_counts": table_counts, "table_count": len(table_counts),
        "baseline_images": {name: manifest["images"][name] for name in ("postgres", "backend")},
        "output": str(output), "automatic_cleanup": False,
        "upgrade_output": str(output / "upgrade"), "rollback_output": str(output / "rollback"),
        "upgrade_subnet": upgrade_subnet, "rollback_subnet": rollback_subnet,
        "expected_resources": {"isolated_postgres": 2, "retained_baseline_backend": 1,
                               "candidate_backend": 1, "independent_rollback_backend": 1,
                               "identity_guard_negative_backends": 2,
                               "isolated_networks": 2, "automatic_deletions": 0},
        "rpo_target_seconds": 86400, "rto_target_seconds": 3600,
        "targets_are_objectives_not_measured_guarantees": True,
        "boundaries": [
            "This recovery point is the selected backup snapshot; it is not proof of the current daily database or a new pre-deployment backup.",
            "Upgrade clone A restores original columns and checks candidate migrations. Rollback clone B restores the original backup again with the old image, in a separate database and directory.",
            "Before the correct candidate starts, verified clone rows/files are enrolled under one dataset ID; production startup is exercised with deliberately wrong DB/upload IDs and must reject them without overwriting markers.",
            "Starting retained old image A against A's upgraded database is not the rollback proof.",
            "Only isolated PostgreSQL and workers-disabled Java clones are started. Agent/frontend business flows are separate candidate acceptance gates; extracted Chroma/runtime bytes alone are not runtime-availability proof.",
            "Elapsed clone timings do not include incident detection, a production maintenance window or off-host transfer, and do not prove a production RTO.",
            "No daily service mutation, Docker cleanup, provider call or deployment-window grant is authorized by this runner.",
        ],
    }
    return manifest, plan


def run(backup, output, candidate_image=None, daily_project="langchain-trip-planner", execute=False,
        upgrade_subnet=None, rollback_subnet=None):
    manifest, report = preflight(backup, output, candidate_image, upgrade_subnet, rollback_subnet)
    if not execute:
        return report
    if not report["ready_to_execute"]:
        raise ValueError("A completed candidate build pinned by sha256 ID is required before execution")
    # Inspect only the selected images; never load/retag/build/remove images here.
    for role, image in {"postgres": manifest["images"]["postgres"],
                        "baseline": manifest["images"]["backend"], "candidate": candidate_image}.items():
        actual = json.loads(docker("image", "inspect", image))[0]
        if actual["Id"] != image:
            raise ValueError("Image identity changed before rehearsal")
        report.setdefault("image_revisions", {})[role] = actual.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
    report["local_image_availability"] = "exact_ids_verified"
    before = daily_snapshot(daily_project)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report.update(execute=True, daily_project=daily_project, daily_before=before,
                  started_at=datetime.now(timezone.utc).isoformat(), steps=[], containers=[], networks=[])
    start = time.perf_counter()
    def save():
        (output / "rehearsal-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    save()
    for name, destination, image, subnet in (("upgrade", output / "upgrade", candidate_image, upgrade_subnet),
                                           ("independent_snapshot_rollback", output / "rollback", None, rollback_subnet)):
        began = time.perf_counter()
        step = {"name": name, "started_at": datetime.now(timezone.utc).isoformat(), "passed": False}
        report["steps"].append(step); save()
        try:
            # Child restore emits a non-sensitive summary, but keep the parent
            # CLI output as one machine-readable report.
            with redirect_stdout(io.StringIO()):
                child = restore(backup, destination, candidate_image=image, network_subnet=subnet)
            step["restore_report"] = child
            if name == "independent_snapshot_rollback" and child.get("passed"):
                postgres = next(resource["name"] for resource in child["resources"] if resource["role"] == "isolated_postgres")
                expected = json.loads((backup / "database-summary.json").read_text(encoding="utf-8"))
                if database_summary(postgres, "trip_restore") != expected:
                    raise RuntimeError("Original snapshot differs after the old backend became ready")
                step["original_snapshot_rechecked_after_old_backend_ready"] = True
            step.update(passed=bool(child.get("passed")), restore_report=child)
        except RestoreVerificationFailure as exception:
            step.update(error_type=type(exception).__name__, restore_report=exception.report)
        except Exception as exception:
            step["error_type"] = type(exception).__name__
        step["elapsed_seconds"] = round(time.perf_counter() - began, 3)
        child = step.get("restore_report", {})
        report["containers"].extend(child.get("containers", []))
        if child.get("network"):
            report["networks"].append(child["network"])
        save()
    try:
        report["daily_after"] = daily_snapshot(daily_project)
        report["daily_before_after_equal"] = report["daily_after"] == before
    except Exception as exception:
        report["daily_before_after_equal"] = False
        report["daily_snapshot_error_type"] = type(exception).__name__
    report.update(elapsed_seconds=round(time.perf_counter() - start, 3),
                  completed_at=datetime.now(timezone.utc).isoformat())
    report["true_snapshot_data_rollback_verified"] = any(step["name"] == "independent_snapshot_rollback" and step["passed"] for step in report["steps"])
    report["passed"] = report["daily_before_after_equal"] and all(step["passed"] for step in report["steps"])
    save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-image")
    parser.add_argument("--daily-project", default="langchain-trip-planner")
    parser.add_argument("--upgrade-subnet"); parser.add_argument("--rollback-subnet")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.backup, args.output, args.candidate_image, args.daily_project,
                     args.execute, args.upgrade_subnet, args.rollback_subnet)
        code = 0 if not args.execute or result.get("passed") else 1
    except Exception as exception:
        result = {"execute": args.execute, "passed": False, "error_type": type(exception).__name__}
        code = 1
    text = json.dumps(result, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
