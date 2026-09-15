"""Durable tombstones and command acknowledgements, independent of business DB."""
import sqlite3
from ..services.agent_paths import DATA_DIR


def ledger_path():
    return DATA_DIR / "index-commands.sqlite3"


def begin(key: str, version: int) -> bool:
    if version < 1:
        raise ValueError("Index command version must be positive")
    with sqlite3.connect(ledger_path(), timeout=10) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS commands (identity TEXT PRIMARY KEY, version INTEGER NOT NULL, complete INTEGER NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT version,complete FROM commands WHERE identity=?", (key,)).fetchone()
        if row and (row[0] > version or row == (version, 1)):
            return False
        db.execute("INSERT INTO commands VALUES (?,?,0) ON CONFLICT(identity) DO UPDATE SET version=excluded.version,complete=0", (key, version))
    return True


def complete(key: str, version: int) -> None:
    with sqlite3.connect(ledger_path(), timeout=10) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("UPDATE commands SET complete=1 WHERE identity=? AND version=?", (key, version))
