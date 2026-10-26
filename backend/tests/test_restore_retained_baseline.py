"""Candidate checks retain every isolated clone; no Docker commands execute."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import restore_java_backup as restore


def fixture(tmp_path, monkeypatch, failure=None):
    backup = tmp_path / "backup"
    (backup / "runtime").mkdir(parents=True)
    (backup / "database.dump").write_bytes(b"isolated-backup")
    expected = {"users": "1:user-row-hash", "flyway_schema_history": "1:migration-row-hash"}
    (backup / "database-summary.json").write_text(json.dumps(expected), encoding="utf-8")
    (backup / "database-columns.json").write_text(json.dumps({"users": ["id", "username"], "flyway_schema_history": ["installed_rank"]}), encoding="utf-8")
    (backup / "runtime/backend.json").write_text(json.dumps(["JWT_SECRET_KEY=private-test-secret-never-report", "DATASET_ID=isolated-test-dataset"]), encoding="utf-8")
    manifest = {"images": {"postgres": "sha256:baseline-pg", "backend": "sha256:baseline-backend"},
                "mounts": {name: {"archive": name+".tar"} for name in ("knowledge_uploads","chroma","agent-runtime")}}
    calls = []
    candidate_started = False
    monkeypatch.setattr(restore, "validate_manifest", lambda _: manifest)
    monkeypatch.setattr(restore, "extract_safe", lambda _archive, target: target.mkdir())
    monkeypatch.setattr(restore.time, "sleep", lambda _: None)
    monkeypatch.setattr(restore,"enroll_clone_dataset",lambda *_:{"dataset_id":"0123456789abcdef0123456789abcdef","production_guard_required":True})
    monkeypatch.setattr(restore,"run_clone_identity_guards",lambda *_:{"clone_files_and_markers_unchanged":True})
    def docker(*args, **_kwargs):
        calls.append(tuple(args))
        if args[0] == "rename" and failure == "rename":
            raise RuntimeError("rename failure")
        if args[:2] == ("image", "inspect"):
            return b'[{"Id":"sha256:candidate-pinned"}]'
        if args[:3] == ("network", "ls", "-q"):
            return b"existing-network"
        if args[:2] == ("network", "inspect"):
            return b'[{"IPAM":{"Config":[{"Subnet":"172.24.0.0/16"}]}}]'
        return b"isolated-resource"
    def command(args, **_kwargs):
        nonlocal candidate_started
        calls.append(tuple(args))
        if args[:3] == ["docker", "run", "-d"] and args[-1] == "candidate-image:local":
            assert _kwargs["env"]["APP_ENV"] == "production"
            assert _kwargs["env"]["DATASET_ENROLL_NEW"] == "false"
            assert _kwargs["env"]["DATASET_ID"] == "0123456789abcdef0123456789abcdef"
            candidate_started = True
            if failure == "creation":
                raise subprocess.CalledProcessError(1, args, stderr=b"private diagnostic")
        if candidate_started and "curl" in args and failure == "readiness":
            return subprocess.CompletedProcess(args, 1)
        return subprocess.CompletedProcess(args, 0, stdout=b"isolated-container")
    monkeypatch.setattr(restore, "docker", docker)
    monkeypatch.setattr(restore.subprocess, "run", command)
    def summary(_container, _database, columns=None):
        if columns is None:
            return expected
        return {"users": "1:changed-row" if failure == "rows" else expected["users"]}
    monkeypatch.setattr(restore, "database_summary", summary)
    history = [{"installed_rank": 1, "version": "1", "success": True}]
    def sql(_container, _database, query):
        if query.startswith("SELECT dataset_id"):
            return "0123456789abcdef0123456789abcdef"
        if query.startswith("SELECT version"):
            return "2"
        if "installed_rank<=" in query and failure == "history":
            return json.dumps([{**history[0], "version": "changed"}])
        return json.dumps(history)
    monkeypatch.setattr(restore, "sql", sql)
    return backup, tmp_path / "output", calls


def assert_no_cleanup(calls):
    forbidden = {"rm", "rmi", "prune", "down"}
    for command in calls:
        assert not forbidden.intersection(command)
        assert "--rm" not in command
    assert not any(command[:2] == ("network", "rm") for command in calls)


def test_candidate_upgrade_stops_renames_and_retains_baseline_without_removal(tmp_path, monkeypatch):
    backup, output, calls = fixture(tmp_path, monkeypatch)
    report = restore.restore_v2(backup, output, candidate_image="candidate-image:local")
    baseline = next(resource for resource in report["resources"] if resource["role"] == "baseline_backend")
    candidate = next(resource for resource in report["resources"] if resource["role"] == "candidate_backend")
    assert baseline["name"] != candidate["name"]
    assert baseline["state"] == "stopped" and baseline["created"] is True
    assert candidate["state"] == "running" and candidate["created"] is True
    assert ("stop", "--time", "30", baseline["original_name"]) in calls
    assert ("rename", baseline["original_name"], baseline["name"]) in calls
    assert report["containers"] == [report["resources"][0]["name"], baseline["name"], candidate["name"]]
    assert report["passed"] is True and report["automatic_cleanup"] is False
    assert report["network_created"] is True
    assert report["rollback"]["old_image_on_upgraded_schema"] == "not_tested"
    assert report["rollback"]["data_rollback_verified"] is False
    assert report["dataset_identity_guards"]["flyway_history_unchanged"] is True
    assert_no_cleanup(calls)
    text = (output / "restore-report.json").read_text(encoding="utf-8")
    assert "private-test-secret-never-report" not in text
    assert json.loads(text) == report


@pytest.mark.parametrize("failure,stage", [
    ("creation", "start_candidate_backend"), ("readiness", "await_candidate_backend"),
    ("rows", "verify_candidate_original_data_and_migrations"), ("history", "verify_candidate_original_data_and_migrations")])
def test_failed_candidate_preserves_exact_old_new_and_network_report(tmp_path, monkeypatch, failure, stage):
    backup, output, calls = fixture(tmp_path, monkeypatch, failure)
    with pytest.raises(restore.RestoreVerificationFailure) as failed:
        restore.restore_v2(backup, output, candidate_image="candidate-image:local")
    report = failed.value.report
    assert report["stage"] == stage and report["passed"] is False
    assert report["network_created"] is True
    baseline = next(resource for resource in report["resources"] if resource["role"] == "baseline_backend")
    candidate = next(resource for resource in report["resources"] if resource["role"] == "candidate_backend")
    assert baseline["state"] == "stopped" and baseline["renamed"] is True
    assert candidate["creation_attempted"] is True
    assert baseline["name"] in report["containers"]
    assert report["automatic_cleanup"] is False
    assert json.loads((output / "restore-report.json").read_text(encoding="utf-8")) == report
    assert_no_cleanup(calls)


def test_rename_failure_keeps_stopped_original_and_does_not_start_candidate(tmp_path, monkeypatch):
    backup, output, calls = fixture(tmp_path, monkeypatch, "rename")
    with pytest.raises(restore.RestoreVerificationFailure) as failed:
        restore.restore_v2(backup, output, candidate_image="candidate-image:local")
    report = failed.value.report
    baseline = next(resource for resource in report["resources"] if resource["role"] == "baseline_backend")
    assert baseline["name"] == baseline["original_name"] and baseline["state"] == "stopped"
    assert baseline["rename_attempted"] is True
    assert not any(command[-1] == "candidate-image:local" for command in calls)
    assert_no_cleanup(calls)


def test_rejected_identity_startup_cannot_silently_advance_flyway(tmp_path, monkeypatch):
    backup, output, calls = fixture(tmp_path, monkeypatch)
    original_sql=restore.sql
    checks_done=False
    def guards(*_):
        nonlocal checks_done
        checks_done=True
        return {"clone_files_and_markers_unchanged":True}
    def sql(container,database,query):
        result=original_sql(container,database,query)
        if checks_done and query.startswith("SELECT json_agg"):
            return json.dumps(json.loads(result)+[{"installed_rank":2,"version":"2","success":True}])
        return result
    monkeypatch.setattr(restore,"run_clone_identity_guards",guards)
    monkeypatch.setattr(restore,"sql",sql)
    with pytest.raises(restore.RestoreVerificationFailure) as failed:
        restore.restore_v2(backup,output,candidate_image="candidate-image:local")
    assert failed.value.report["stage"]=="verify_production_identity_mismatch_guards"
    assert not any(command[-1]=="candidate-image:local" for command in calls)
    assert_no_cleanup(calls)


def test_overlapping_subnet_is_reported_before_creating_docker_resources(tmp_path, monkeypatch):
    backup, output, calls = fixture(tmp_path, monkeypatch)
    with pytest.raises(restore.RestoreVerificationFailure) as failed:
        restore.restore_v2(backup, output, candidate_image="candidate-image:local", network_subnet="172.24.9.0/24")
    report = failed.value.report
    assert report["stage"] == "validate_network_subnet"
    assert report["containers"] == [] and report["network_created"] is False
    assert not any(command[:2] == ("network", "create") for command in calls)
    assert_no_cleanup(calls)
