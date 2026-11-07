"""Optional integration against the pinned real restic executable."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from uuid import uuid4
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from restic_archive import run


@pytest.mark.skipif(not os.environ.get("TEST_RESTIC_BINARY"),reason="Pinned restic binary not configured")
def test_encrypted_snapshot_restore_and_checksum_verification(tmp_path):
    source=tmp_path/"coherent"
    source.mkdir()
    payload=b"isolated coherent database snapshot"
    (source/"database.dump").write_bytes(payload)
    (source/"manifest.json").write_text(json.dumps({"schema":"Java/Flyway-v2","sha256":{"database.dump":hashlib.sha256(payload).hexdigest()}}))
    config=tmp_path/"private.env"
    config.write_text(f"RESTIC_REPOSITORY='{tmp_path/'repository'}'\nRESTIC_PASSWORD='isolated-fixture-password'\nRESTIC_CACHE_DIR='{tmp_path/'cache'}'\n")
    args=SimpleNamespace(binary=Path(os.environ["TEST_RESTIC_BINARY"]),env_file=config,command="init",execute=True,
        source=source,target=Path(tempfile.gettempdir())/("trip-public-restore-test-"+uuid4().hex),snapshot="latest")
    assert run(args)["initialized"]
    args.command="archive"
    archived=run(args)
    assert archived["snapshot_id"] and not archived["off_host_verified"]
    args.command="check"
    assert run(args)["repository_metadata_checked"]
    args.command="restore"
    result=run(args)
    assert result["checksums_verified"]
    assert (Path(result["backup_directory"])/"database.dump").read_bytes()==payload
    assert not result["application_restore_completed"]


def test_corrupt_coherent_backup_is_not_archived(tmp_path,monkeypatch):
    import restic_archive
    monkeypatch.setattr(restic_archive,"environment",lambda _: {})
    calls=[]
    monkeypatch.setattr(restic_archive,"call",lambda binary,args,env:calls.append(args) or "restic 0.19.1 compiled for fixture")
    (tmp_path/"database.dump").write_bytes(b"corrupt")
    (tmp_path/"manifest.json").write_text(json.dumps({"schema":"Java/Flyway-v2","sha256":{"database.dump":"0"*64}}))
    with pytest.raises(ValueError,match="checksum"):
        run(SimpleNamespace(binary="restic",env_file=None,command="archive",source=tmp_path,execute=True))
    assert calls==[["version"]]
