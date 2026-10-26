"""Exact-image export prerequisites must fail before maintenance mutations."""
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/"scripts"))
import automated_maintenance as maintenance


NAMES=("backend","agent","postgres","frontend")


def setup(monkeypatch,tmp_path,inspect):
    monkeypatch.setenv("TRIP_MAINTENANCE_LOCK_DIR",str(tmp_path/"locks"))
    selected={name:{"id":name+"-container","image":"sha256:"+str(index)*64,
                    "running":True,"tag":"mutable-candidate:new-image"}
              for index,name in enumerate(NAMES,1)}
    monkeypatch.setattr(maintenance,"snapshot",lambda _:selected)
    calls=[]
    def docker(*args):
        calls.append(args)
        assert args[:2]==("image","inspect"),"No stop/start/exec/save permitted after failed preflight"
        return inspect(args[2],selected)
    monkeypatch.setattr(maintenance,"docker",docker)
    monkeypatch.setattr(maintenance,"backup",Mock(side_effect=AssertionError("No backup directory or dump before preflight")))
    return selected,calls


def test_missing_selected_images_rejected_before_stop_dump_or_backup_directory(tmp_path,monkeypatch):
    missing={"sha256:"+"2"*64,"sha256:"+"4"*64}
    def inspect(identity,_):
        if identity in missing:raise RuntimeError("Docker operation failed: Error response from daemon: No such image: "+identity)
        return json.dumps([{"Id":identity}]).encode()
    selected,calls=setup(monkeypatch,tmp_path,inspect)
    root=tmp_path/"backups"
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("selected",root,execute=True)
    report=failed.value.report
    assert calls==[("image","inspect",selected[name]["image"]) for name in NAMES]
    assert set(report["image_preflight"]["missing_image_ids"])==missing
    assert {error["service"] for error in report["errors"]}=={"agent","frontend"}
    assert all(error["step"]=="image_preflight" for error in report["errors"])
    assert report["passed"] is False and report["execution_started"] is False
    assert report["no_container_mutations_attempted"] is True
    maintenance.backup.assert_not_called()
    assert not root.exists()


def test_new_mutable_tag_image_cannot_substitute_for_selected_image(tmp_path,monkeypatch):
    def inspect(identity,_):return json.dumps([{"Id":"sha256:"+"9"*64,"RepoTags":["mutable-candidate:new-image"]}]).encode()
    selected,calls=setup(monkeypatch,tmp_path,inspect)
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("selected",tmp_path/"backups",execute=True)
    assert all(call[2]==selected[name]["image"] for call,name in zip(calls,NAMES))
    assert all(item["status"]=="identity_mismatch" for item in failed.value.report["image_preflight"]["images"])
    assert failed.value.report["image_preflight"]["missing_image_ids"]==[]
    maintenance.backup.assert_not_called()


@pytest.mark.parametrize("result,error_type",[(RuntimeError("daemon unavailable private-credential-value"),"RuntimeError"),
                                               (b"not-json","JSONDecodeError"),(b"[]","ValueError")])
def test_inspector_failure_is_not_missing_or_success_and_keeps_old_heartbeat(tmp_path,monkeypatch,result,error_type):
    def inspect(identity,_):
        if isinstance(result,Exception):raise result
        return result
    _,calls=setup(monkeypatch,tmp_path,inspect)
    root=tmp_path/"backups";root.mkdir()
    marker=root/"last-success.json";marker.write_text('{"observed_at":"previous-success"}',encoding="utf-8")
    original=marker.read_bytes()
    with pytest.raises(maintenance.MaintenanceFailure) as failed:
        maintenance.run_backup("selected",root,execute=True)
    report=failed.value.report
    assert len(calls)==4 and report["passed"] is False
    assert report["image_preflight"]["missing_image_ids"]==[]
    assert all(item["status"]=="inspect_failed" and item["error_type"]==error_type
               for item in report["image_preflight"]["images"])
    assert marker.read_bytes()==original and list(root.iterdir())==[marker]
    assert report["last_success_updated"] is False
    assert "private-credential-value" not in json.dumps(report)
    maintenance.backup.assert_not_called()
