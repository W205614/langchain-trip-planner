"""Prevent old business Python sources from returning to the Agent package."""
from pathlib import Path


def test_agent_has_no_business_backend_sources():
    root = Path(__file__).resolve().parents[1]
    for path in ("app/api", "app/db", "alembic"):
        assert not list((root / path).rglob("*.py")), path
    for path in ("app/core/security.py", "app/core/rate_limit.py", "app/services/trip_tasks.py",
                 "app/services/history_service.py", "app/services/knowledge_ingest.py", "alembic.ini"):
        assert not (root / path).exists(), path
