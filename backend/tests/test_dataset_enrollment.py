"""Pure counterexamples: no Docker process, production files or live database."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import enroll_dataset as enrollment

IDENTITY = "0123456789abcdef0123456789abcdef"


def archive(files, directory=None):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as stream:
        if directory:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            stream.addfile(info)
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            stream.addfile(info, io.BytesIO(payload))
    result.seek(0)
    return result


def snapshot(tmp_path, monkeypatch, mount=True):
    columns = {"users": ["id", "username"], "trip_records": ["id", "user_id", "plan_json"]}
    summary = {"users": "1:fixed-user-row-hash", "trip_records": "1:fixed-plan-row-hash"}
    for name, value in (("database-columns.json", columns), ("database-summary.json", summary)):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    postgres = {"Id": "isolated-postgres", "State": {"Running": True},
                "Config": {"Env": ["POSTGRES_USER=trip", "POSTGRES_DB=trip_java"]},
                "Mounts": [{"Type": "volume", "Name": "owned_pg", "Source": "/docker/owned_pg", "Destination": "/var/lib/postgresql/data"}]}
    manifest = {"database": "trip_java", "database_user": "trip", "sha256": {
        "database.dump": "fixture", "database-columns.json": "fixture", "database-summary.json": "fixture"}}
    if mount:
        manifest["postgres_mount"] = {"type": "volume", "source": "owned_pg", "destination": "/var/lib/postgresql/data"}
    monkeypatch.setattr(enrollment, "database_columns", Mock(return_value=deepcopy(columns)))
    monkeypatch.setattr(enrollment, "database_summary", Mock(return_value=deepcopy(summary)))
    return postgres, manifest, columns, summary


def test_inventory_streams_files_and_ignores_only_same_id_root_marker():
    payload = b"file-content" * 300_000
    stream = archive({"data.bin": payload, ".dataset-id": (IDENTITY + "\n").encode()}, directory="child")
    class BoundedReads:
        def read(self, size=-1):
            assert 0 <= size <= 1024 * 1024
            return stream.read(size)
    inventory = enrollment.tar_inventory(BoundedReads(), IDENTITY)
    assert inventory == {"child": {"kind": "directory"}, "data.bin": {
        "kind": "file", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}


def test_wrong_marker_and_link_are_not_treated_as_optional_metadata():
    with pytest.raises(ValueError, match="another dataset"):
        enrollment.tar_inventory(archive({".dataset-id": b"other-dataset"}), IDENTITY)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as output:
        item = tarfile.TarInfo("link")
        item.type, item.linkname = tarfile.SYMTYPE, "/outside"
        output.addfile(item)
    stream.seek(0)
    with pytest.raises(ValueError, match="links"):
        enrollment.tar_inventory(stream, IDENTITY)


@pytest.mark.parametrize("difference", ["added", "missing", "changed"])
def test_same_volume_name_cannot_hide_unbacked_file_changes(tmp_path, monkeypatch, difference):
    deployed, manifest = {}, {"mounts": {}, "sha256": {}}
    for name, service, target in enrollment.MOUNTS:
        container = deployed.setdefault(service, {"Id": service, "Mounts": []})
        container["Mounts"].append({"Type": "volume", "Name": name, "Destination": target})
        filename = name + ".tar"
        (tmp_path / filename).write_bytes(archive({"original": b"saved"}).getvalue())
        manifest["mounts"][name] = {"type": "volume", "source": name, "destination": target, "archive": filename}
        manifest["sha256"][filename] = "covered-by-manifest-checked-before-helper"
    expected = enrollment.tar_inventory(archive({"original": b"saved"}), IDENTITY)
    changed = deepcopy(expected)
    if difference == "added":
        changed["unbacked"] = {"kind": "file", "size": 1, "sha256": "other"}
    elif difference == "missing":
        changed.pop("original")
    else:
        changed["original"]["sha256"] = "modified-content"
    monkeypatch.setattr(enrollment, "actual_inventory", Mock(return_value=changed))
    with pytest.raises(ValueError, match="contents differ"):
        enrollment.verify_file_contents(deployed, manifest, tmp_path, IDENTITY)


def test_original_content_is_compared_not_only_row_counts_or_table_names(tmp_path, monkeypatch):
    postgres, manifest, columns, summary = snapshot(tmp_path, monkeypatch)
    summary["trip_records"] = "1:different-plan-same-row-count"
    enrollment.database_summary.return_value = summary
    with pytest.raises(ValueError, match="original business content differs"):
        enrollment.verify_database_snapshot(postgres, manifest, tmp_path)
    enrollment.database_summary.assert_called_once_with("isolated-postgres", "trip_java", "trip", columns=columns)


def test_different_postgres_mount_and_database_user_are_rejected(tmp_path, monkeypatch):
    postgres, manifest, _, _ = snapshot(tmp_path, monkeypatch)
    postgres["Mounts"][0]["Name"] = "foreign_pg"
    with pytest.raises(ValueError, match="persistent mount identity differ"):
        enrollment.verify_database_snapshot(postgres, manifest, tmp_path)
    postgres["Config"]["Env"][0] = "POSTGRES_USER=someone_else"
    with pytest.raises(ValueError, match="database or user differ"):
        enrollment.verify_database_snapshot(postgres, manifest, tmp_path)


def test_missing_original_columns_rejects_and_added_schema_does_not_hide_changed_data(tmp_path, monkeypatch):
    postgres, manifest, _, _ = snapshot(tmp_path, monkeypatch)
    enrollment.database_columns.return_value["trip_records"].remove("plan_json")
    with pytest.raises(ValueError, match="original backup tables or columns"):
        enrollment.verify_database_snapshot(postgres, manifest, tmp_path)
    enrollment.database_summary.assert_not_called()


def test_legacy_manifest_never_claims_postgres_mount_identity_was_compared(tmp_path, monkeypatch):
    postgres, manifest, _, _ = snapshot(tmp_path, monkeypatch, mount=False)
    _, _, evidence = enrollment.verify_database_snapshot(postgres, manifest, tmp_path)
    assert evidence["original_tables_and_columns_content_match"] is True
    assert evidence["postgres_mount_identity_compared"] is False
    assert "legacy_manifest_missing_mount_identity" in evidence["postgres_mount_evidence"]


def test_enrollment_preflight_failure_cannot_write_markers_database_or_environment(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ("business", "agent"):
        (runtime / (name + ".env")).write_text("DATASET_ID=" + IDENTITY + "\n", encoding="utf-8")
    before = {name: (runtime / (name + ".env")).read_bytes() for name in ("business", "agent")}
    deployed = {name: {"State": {"Running": name == "postgres"}} for name in ("backend", "agent", "postgres", "frontend")}
    monkeypatch.setattr(enrollment, "validate_manifest", lambda _: {"project": "isolated"})
    monkeypatch.setattr(enrollment, "services", lambda _: deployed)
    monkeypatch.setattr(enrollment, "verify_database_snapshot", Mock(side_effect=ValueError("foreign database content")))
    writes = Mock(side_effect=AssertionError("Preflight must perform no mutations"))
    monkeypatch.setattr(enrollment, "docker", writes)
    monkeypatch.setattr(enrollment, "sql", writes)
    monkeypatch.setattr(enrollment, "_write", writes)
    with pytest.raises(ValueError, match="foreign database content"):
        enrollment.enroll("isolated", tmp_path, runtime, execute=True)
    writes.assert_not_called()
    assert before == {name: (runtime / (name + ".env")).read_bytes() for name in ("business", "agent")}
