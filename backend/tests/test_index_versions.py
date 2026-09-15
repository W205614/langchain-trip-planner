import pytest
from app.agent_api import index_versions as ledger


def test_delete_tombstone_rejects_old_and_duplicate_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ledger_path", lambda: tmp_path / "index.sqlite3")
    assert ledger.begin("history:1:5", 8)
    ledger.complete("history:1:5", 8)
    assert not ledger.begin("history:1:5", 7)
    assert not ledger.begin("history:1:5", 8)
    assert ledger.begin("history:1:5", 9)


def test_uncertain_write_can_repeat_but_older_write_cannot(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ledger_path", lambda: tmp_path / "index.sqlite3")
    assert ledger.begin("document:9", 4)
    assert ledger.begin("document:9", 4)
    assert not ledger.begin("document:9", 3)
    ledger.complete("document:9", 3)
    assert ledger.begin("document:9", 4)
    ledger.complete("document:9", 4)
    assert not ledger.begin("document:9", 4)


def test_positive_version_required(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "ledger_path", lambda: tmp_path / "index.sqlite3")
    with pytest.raises(ValueError):
        ledger.begin("document:9", 0)
