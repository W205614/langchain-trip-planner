"""Command/report gates for real snapshot rollback; no Docker or restore executed."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import existing_data_rehearsal as rehearsal

CANDIDATE = "sha256:" + "a" * 64


def scenario(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    (backup / "runtime").mkdir(parents=True)
    summary = {"users": "2:userhash", "trip_records": "3:triphash", "flyway_schema_history": "10:flywayhash"}
    columns = {"users": ["id"], "trip_records": ["id", "plan_json"], "flyway_schema_history": ["installed_rank"]}
    (backup / "database-summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (backup / "database-columns.json").write_text(json.dumps(columns), encoding="utf-8")
    manifest = {"schema": "Java/Flyway-v2", "project": "isolated", "created_at": "2026-10-25T12:00:00Z",
                "images": {"postgres": "sha256:" + "b" * 64, "backend": "sha256:" + "c" * 64},
                "sha256": {name: "fixture" for name in ("database.dump", "database-summary.json", "database-columns.json", "runtime/backend.json")},
                "mounts": {name: {"archive": name + ".tar"} for name in ("knowledge_uploads", "chroma", "agent-runtime")}}
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path / "separate-workspace")
    monkeypatch.setattr(rehearsal, "validate_manifest", Mock(return_value=manifest))
    before = {"backend": {"id": "daily-do-not-mutate", "image": "original-daily", "running": True, "mounts": []}}
    monkeypatch.setattr(rehearsal, "daily_snapshot", Mock(return_value=before))
    def docker(*args):
        assert args[:2] == ("image", "inspect"), "Runner itself only inspects exact local image IDs"
        return json.dumps([{"Id": args[-1], "Config": {"Labels": {"org.opencontainers.image.revision": "frozen-source"}}}]).encode()
    monkeypatch.setattr(rehearsal, "docker", Mock(side_effect=docker))
    calls = []
    def restore(_backup, output, candidate_image=None, network_subnet=None):
        calls.append({"output": output, "candidate_image": candidate_image, "subnet": network_subnet})
        output.mkdir()
        name = "upgrade" if candidate_image else "rollback"
        return {"passed": True, "containers": [name + "-pg", name + "-backend"], "network": name + "-net",
                "resources": [{"role": "isolated_postgres", "name": name + "-pg"}], "automatic_cleanup": False}
    monkeypatch.setattr(rehearsal, "restore", Mock(side_effect=restore))
    monkeypatch.setattr(rehearsal, "database_summary", Mock(return_value=summary))
    return backup, tmp_path / "new-rehearsal", manifest, summary, calls


def test_default_plan_reads_backup_without_docker_or_creating_rehearsal(tmp_path, monkeypatch):
    backup, output, _, _, _ = scenario(tmp_path, monkeypatch)
    report = rehearsal.run(backup, output)
    assert report["execute"] is False and report["ready_to_execute"] is False
    assert report["table_counts"]["trip_records"] == 3
    assert not output.exists()
    rehearsal.docker.assert_not_called()
    rehearsal.restore.assert_not_called()


def test_refuses_legacy_or_empty_database_and_unpinned_candidate_before_docker(tmp_path, monkeypatch):
    backup, output, manifest, _, _ = scenario(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="fixed local sha256"):
        rehearsal.run(backup, output, candidate_image="candidate:latest", execute=True)
    manifest["schema"] = "Java/Flyway"
    with pytest.raises(ValueError, match="refuses the V1"):
        rehearsal.run(backup, output, candidate_image=CANDIDATE, execute=True)
    manifest["schema"] = "Java/Flyway-v2"
    summary = json.loads((backup / "database-summary.json").read_text())
    summary["trip_records"] = "0:empty"
    (backup / "database-summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="empty itinerary"):
        rehearsal.run(backup, output, candidate_image=CANDIDATE, execute=True)
    rehearsal.docker.assert_not_called()


def test_upgrade_and_actual_rollback_use_different_database_resources_and_backup(tmp_path, monkeypatch):
    backup, output, _, _, calls = scenario(tmp_path, monkeypatch)
    report = rehearsal.run(backup, output, candidate_image=CANDIDATE, execute=True)
    assert report["passed"] is True and report["true_snapshot_data_rollback_verified"] is True
    assert [call["candidate_image"] for call in calls] == [CANDIDATE, None]
    assert calls[0]["output"] != calls[1]["output"]
    assert report["containers"] == ["upgrade-pg", "upgrade-backend", "rollback-pg", "rollback-backend"]
    assert report["networks"] == ["upgrade-net", "rollback-net"]
    assert report["steps"][1]["original_snapshot_rechecked_after_old_backend_ready"] is True
    rehearsal.database_summary.assert_called_once_with("rollback-pg", "trip_restore")
    assert report["daily_before_after_equal"] is True
    assert all(step["elapsed_seconds"] >= 0 for step in report["steps"])
    assert json.loads((output / "rehearsal-report.json").read_text()) == report


def test_old_backend_startup_mutation_cannot_pass_snapshot_data_rollback(tmp_path, monkeypatch):
    backup, output, _, summary, _ = scenario(tmp_path, monkeypatch)
    rehearsal.database_summary.return_value = {**summary, "trip_records": "3:changed-after-startup"}
    report = rehearsal.run(backup, output, candidate_image=CANDIDATE, execute=True)
    assert report["steps"][0]["passed"] is True
    assert report["steps"][1]["passed"] is False
    assert report["true_snapshot_data_rollback_verified"] is False and report["passed"] is False
    assert "rollback-pg" in report["containers"]


def test_daily_snapshot_change_cannot_be_hidden_by_successful_clones(tmp_path, monkeypatch):
    backup, output, _, _, _ = scenario(tmp_path, monkeypatch)
    original = rehearsal.daily_snapshot.return_value
    changed = deepcopy(original)
    changed["backend"]["image"] = "unexpected-daily-change"
    rehearsal.daily_snapshot.side_effect = [original, changed]
    report = rehearsal.run(backup, output, candidate_image=CANDIDATE, execute=True)
    assert report["true_snapshot_data_rollback_verified"] is True
    assert report["daily_before_after_equal"] is False and report["passed"] is False


def test_overlapping_clone_subnets_are_rejected_before_docker(tmp_path, monkeypatch):
    backup, output, _, _, _ = scenario(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="nonoverlapping"):
        rehearsal.run(backup, output, candidate_image=CANDIDATE, execute=True,
                      upgrade_subnet="172.28.0.0/16", rollback_subnet="172.28.1.0/24")
    rehearsal.docker.assert_not_called()
