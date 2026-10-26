"""Real filesystem/HTTP boundaries, without touching the daily deployment or providers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
from automated_maintenance import replicate
from operational_alert_receiver import record
from app.services.dataset_identity import verify_marker

def test_dataset_marker_never_adopts_or_overwrites_unknown_files(tmp_path):
    (tmp_path/"original").write_bytes(b"business")
    with pytest.raises(RuntimeError,match="no verified"):verify_marker(tmp_path,"expected",True)
    assert (tmp_path/"original").read_bytes()==b"business"
    blank=tmp_path/"new";verify_marker(blank,"expected",True)
    with pytest.raises(RuntimeError,match="another"):verify_marker(blank,"different",True)
    verify_marker(blank,"expected",False)

def test_verified_replication_never_overwrites_a_target(tmp_path):
    source=tmp_path/"source";source.mkdir();(source/"database.dump").write_bytes(b"isolated-fixture")
    (source/"manifest.json").write_text(json.dumps({"sha256":{"database.dump":hashlib.sha256(b"isolated-fixture").hexdigest()}}))
    destination=tmp_path/"replica"
    report=replicate(source,destination)
    assert report["verified"] and not report["off_host"]
    with pytest.raises(ValueError,match="new directory"):replicate(source,destination)
    (source/"database.dump").write_bytes(b"corrupted")
    with pytest.raises(ValueError,match="checksum"):replicate(source,tmp_path/"corrupt-copy")
    assert not (tmp_path/"corrupt-copy").exists()

def test_alert_inbox_is_durable_deduplicated_and_drops_arbitrary_detail(tmp_path):
    inbox=tmp_path/"alerts.jsonl"
    body={"alerts":[{"status":"firing","startsAt":"2026-10-26T00:00:00Z","labels":{"alertname":"TripSchedulerOwnershipLost","job":"trip","private":"must-not-persist"},"annotations":{"secret":"must-not-persist"}}]}
    assert record(body,inbox)==1
    assert record(body,inbox)==0
    saved=inbox.read_text();assert "must-not-persist" not in saved
    assert json.loads(saved)["name"]=="TripSchedulerOwnershipLost"
    body["alerts"][0]["status"]="resolved"
    assert record(body,inbox)==1
    assert len(inbox.read_text().splitlines())==2
