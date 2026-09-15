from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from app.services.call_budget import reserve, CallBudgetExceeded


def test_durable_atomic_per_category_stop(tmp_path, monkeypatch):
    ledger = tmp_path / "budget.sqlite3"
    monkeypatch.setenv("ACCEPTANCE_BUDGET_FILE", str(ledger))
    def call(_):
        try:
            reserve("text")
            return True
        except CallBudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(call, range(30))) == 12
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT used FROM calls WHERE kind='text'").fetchone()[0] == 12
    with pytest.raises(CallBudgetExceeded):
        reserve("text")
    reserve("amap")
    reserve("vision")
    with pytest.raises(CallBudgetExceeded):
        reserve("vision")
    reserve("embedding")


def test_corrupt_ledger_fails_closed(tmp_path, monkeypatch):
    ledger = tmp_path / "budget.sqlite3"
    ledger.write_bytes(b"not a database")
    monkeypatch.setenv("ACCEPTANCE_BUDGET_FILE", str(ledger))
    with pytest.raises(sqlite3.DatabaseError):
        reserve("vision")


def test_disabled_without_acceptance_setting(monkeypatch):
    monkeypatch.delenv("ACCEPTANCE_BUDGET_FILE", raising=False)
    reserve("text")
