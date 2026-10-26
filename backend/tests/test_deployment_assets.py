"""Backup restoration must reject corruption and archive escapes before Docker writes."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment_assets import extract_safe, validate_manifest


def test_service_inventory_ignores_inherited_image_labels_but_refuses_real_ambiguity(monkeypatch):
    import deployment_assets as assets
    records={name:{"Id":name,"Config":{"Labels":{"com.docker.compose.service":name,
        "com.docker.compose.oneoff":"False","com.docker.compose.project.config_files":"fixture.compose.yml"}}}
        for name in ("agent","backend","frontend","postgres")}
    records["ad-hoc-test"]={"Id":"ad-hoc-test","Config":{"Labels":{"com.docker.compose.project":"daily","com.docker.compose.service":"agent"}}}
    monkeypatch.setattr(assets,"docker",lambda *args:" ".join(records).encode())
    monkeypatch.setattr(assets,"inspect",lambda identity:records[identity])
    assert set(assets.services("daily"))=={"agent","backend","frontend","postgres"}
    records["second-managed-agent"]={"Id":"second-managed-agent","Config":records["agent"]["Config"]}
    with pytest.raises(RuntimeError,match="Multiple agent"):
        assets.services("daily")


def test_corrupt_dump_is_rejected_before_restoration(tmp_path):
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"original database")
    (tmp_path / "manifest.json").write_text(json.dumps({"sha256": {"database.dump": hashlib.sha256(dump.read_bytes()).hexdigest()}}))
    assert validate_manifest(tmp_path)["sha256"]
    dump.write_bytes(b"truncated")
    with pytest.raises(ValueError, match="checksum"):
        validate_manifest(tmp_path)


@pytest.mark.parametrize("name,link", [("../escape", False), ("absolute-link", True)])
def test_archive_escape_and_symlink_are_rejected(tmp_path, name, link):
    archive = tmp_path / "input.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo(name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
            stream.addfile(member)
        else:
            member.size = 1
            stream.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe"):
        extract_safe(archive, tmp_path / "restore")
    assert not (tmp_path / "escape").exists()


def test_regular_archive_restores_exact_bytes(tmp_path):
    archive = tmp_path / "input.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("nested/original.bin")
        data = b"\x00original\xff"
        member.size = len(data)
        stream.addfile(member, io.BytesIO(data))
    extract_safe(archive, tmp_path / "restore")
    assert (tmp_path / "restore/nested/original.bin").read_bytes() == data
