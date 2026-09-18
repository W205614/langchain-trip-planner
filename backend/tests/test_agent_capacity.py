import threading
import time

import pytest
from fastapi import HTTPException

from app.agent_api import main


def test_slow_capability_bulkhead_rejects_without_waiting():
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    started = time.perf_counter()
    with pytest.raises(HTTPException) as rejected:
        with main.capacity("fixture", slots):
            raise AssertionError("must not enter saturated capacity")
    assert rejected.value.status_code == 429
    assert time.perf_counter() - started < 0.1
    slots.release()


def test_completed_execution_registrations_expire(monkeypatch):
    old = main.ExecutionRegistration("old", threading.Event(), completed_at=time.monotonic() - 10)
    active = main.ExecutionRegistration("active", threading.Event())
    monkeypatch.setattr(main, "_executions", {"old": old, "active": active})
    monkeypatch.setattr(main, "_execution_retention_seconds", 1)
    monkeypatch.setattr(main, "_execution_registry_max", 10)
    main._purge_executions(time.monotonic())
    assert "old" not in main._executions
    assert main._executions["active"] is active
