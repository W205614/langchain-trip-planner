"""Optional durable acceptance budget. Reserve before sending; never refund failures.

This ledger contains only counters, not business data or credentials. A crashed
pre-send reservation may overcount, which is safer than exceeding the limit.
"""
import os
import sqlite3
from pathlib import Path

LIMITS = {"text": 12, "vision": 1, "embedding": 50, "amap": 100}


class CallBudgetExceeded(RuntimeError):
    pass


def reserve(kind: str) -> None:
    if kind not in LIMITS:
        raise ValueError("Unknown outbound call category")
    filename = os.environ.get("ACCEPTANCE_BUDGET_FILE")
    if not filename:
        return
    path = Path(filename)
    if not path.is_absolute():
        raise RuntimeError("Budget ledger must use an absolute persistent path")
    # Do not silently relocate/reset an unreadable or corrupt ledger.
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS calls (kind TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used>=0))")
        db.execute("BEGIN IMMEDIATE")
        counts = dict(db.execute("SELECT kind, used FROM calls"))
        if counts.get(kind, 0) >= LIMITS[kind]:
            raise CallBudgetExceeded(f"Acceptance {kind} budget exhausted; this category is stopped")
        db.execute("INSERT INTO calls VALUES (?,1) ON CONFLICT(kind) DO UPDATE SET used=used+1", (kind,))


def hooks(kind: str):
    return {"request": [lambda request: reserve(kind)]}


def async_hooks(kind: str):
    async def before_send(request):
        reserve(kind)
    return {"request": [before_send]}
