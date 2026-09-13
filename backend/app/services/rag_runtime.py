"""Bound optional enrichment; never hold the index lock over network I/O."""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import BoundedSemaphore
from time import monotonic
from datetime import datetime, timedelta, timezone
from prometheus_client import Counter
from .execution import remaining, deadline_var

deadline = ContextVar("rag_deadline", default=None)
slots = BoundedSemaphore(2)
EVENTS = Counter("trip_rag_events_total", "RAG bounded outcomes", ["reason"])


def budget(default=3.0):
    seconds = remaining(default)
    if deadline.get() is not None:
        seconds = min(seconds, deadline.get() - monotonic())
    if seconds <= 0:
        EVENTS.labels("timeout").inc()
        raise TimeoutError("RAG enhancement budget exhausted")
    return seconds


@contextmanager
def operation():
    if deadline.get() is not None:
        budget()
        yield
        return
    if not slots.acquire(timeout=min(0.1, remaining())):
        EVENTS.labels("busy").inc()
        raise TimeoutError("RAG enhancement busy")
    token = execution_token = None
    try:
        seconds = remaining(8)
        token = deadline.set(monotonic() + seconds)
        execution_token = deadline_var.set(datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds))
        yield
    finally:
        if token is not None:
            deadline.reset(token)
        if execution_token is not None:
            deadline_var.reset(execution_token)
        slots.release()


@contextmanager
def index_lock():
    from .coordination import rag_lock
    if not rag_lock.acquire(timeout=budget(0.2)):
        EVENTS.labels("lock_timeout").inc()
        raise TimeoutError("RAG index busy")
    try:
        yield
    finally:
        rag_lock.release()
