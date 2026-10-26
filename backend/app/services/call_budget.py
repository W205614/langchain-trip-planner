"""Optional durable acceptance budget. Reserve before sending; never refund failures.

This ledger contains only counters, not business data or credentials. A crashed
pre-send reservation may overcount, which is safer than exceeding the limit.
"""
import os
import json
import sqlite3
from pathlib import Path

LIMITS = {"text": 12, "vision": 1, "embedding": 40, "amap": 120}


class CallBudgetExceeded(RuntimeError):
    pass


def reserve(kind: str, *, reserved_tokens: int | None = None, map_protocol: str = "rest") -> None:
    if kind not in LIMITS:
        raise ValueError("Unknown outbound call category")
    if kind=="amap" and map_protocol!="rest" and (os.getenv("FINAL_RUN_ID") or os.getenv("FINAL_ACCEPTANCE_RUN_ID")):
        raise CallBudgetExceeded("Fixed campaign requires AMAP_TRANSPORT=rest; MCP tool fan-out cannot be bounded as REST requests")
    from .durable_execution import reserve_outbound
    reserve_outbound(kind, reserved_tokens=reserved_tokens,map_protocol=map_protocol)
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


def text_token_reservation(texts) -> int:
    """UTF-8 bytes overcount ordinary BPE input; reserve message overhead too."""
    if not isinstance(texts,(str,list,tuple)):
        raise CallBudgetExceeded("Cannot bound embedding input before sending")
    return len(json.dumps(texts,ensure_ascii=False,separators=(",", ":")).encode("utf-8")) + 4096


def request_token_reservation(request, kind: str) -> int:
    """Reserve actual serialized input plus a declared output bound, never actual billing."""
    if kind=="amap":
        return 0
    try:
        content=request.content
        body=json.loads(content)
        if not isinstance(body,dict):raise ValueError()
        if kind=="embedding":return len(content)+4096
        maximum=body.get("max_completion_tokens",body.get("max_tokens",body.get("max_output_tokens")))
        count=body.get("n",1)
        if type(maximum) is not int or not 1<=maximum<=200000 or type(count) is not int or not 1<=count<=16:
            raise ValueError()
        return len(content)+4096+maximum*count
    except Exception as exc:
        raise CallBudgetExceeded("Model request has no safe input/output token bound; send stopped") from exc


def hooks(kind: str):
    return {"request": [lambda request: _reserve_request(request,kind)]}


def _reserve_request(request,kind):
    try:
        tokens=request_token_reservation(request,kind)
    except CallBudgetExceeded:
        if os.getenv("FINAL_RUN_ID") or os.getenv("FINAL_ACCEPTANCE_RUN_ID") or os.getenv("ACCEPTANCE_BUDGET_FILE"):
            raise
        # Preserve ordinary compatibility. A campaign known only to Java still
        # rejects this missing bound before any provider send.
        tokens=None
    reserve(kind,reserved_tokens=tokens)


def async_hooks(kind: str):
    async def before_send(request):
        await request.aread()
        _reserve_request(request,kind)
    return {"request": [before_send]}
