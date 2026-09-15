"""Deadline propagation into LangGraph/thread-pool work."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock

deadline_var = ContextVar("trip_deadline", default=None)
stats_var = ContextVar("trip_usage", default=None)
usage_sink_var = ContextVar("trip_usage_sink", default=None)
cancellation_var = ContextVar("trip_cancellation", default=None)
trusted_evidence_var = ContextVar("trip_trusted_evidence", default=None)
rag_degradation_var = ContextVar("trip_rag_degradation", default=None)


def note_rag_degradation():
    notices = rag_degradation_var.get()
    if notices is not None:
        message = "资料检索或可见性校验暂不可用，受影响资料已排除"
        if message not in notices:
            notices.append(message)
_stats_lock = Lock()


@contextmanager
def execution_deadline(deadline, usage_sink=None):
    token = deadline_var.set(deadline)
    stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    stats_token = stats_var.set(stats)
    sink_token = usage_sink_var.set(usage_sink)
    try:
        yield stats
    finally:
        stats_var.reset(stats_token)
        usage_sink_var.reset(sink_token)
        deadline_var.reset(token)


def record_usage(usage=None):
    stats = stats_var.get()
    if stats is not None:
        with _stats_lock:
            if usage is None:
                remaining()
                stats["calls"] += 1
            else:
                stats["input_tokens"] += int(usage.get("input_tokens", 0))
                stats["output_tokens"] += int(usage.get("output_tokens", 0))
            sink = usage_sink_var.get()
            if sink:
                sink(dict(stats), usage is None)


def remaining(default=300.0):
    cancelled = cancellation_var.get()
    if cancelled is not None and cancelled.is_set():
        raise TimeoutError("Task cancelled")
    deadline = deadline_var.get()
    if deadline is None:
        return default
    seconds = (deadline - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
    if seconds <= 0:
        raise TimeoutError("Task deadline exceeded")
    return min(default, seconds)
