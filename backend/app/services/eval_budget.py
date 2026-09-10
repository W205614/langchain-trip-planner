"""Opt-in, process-scoped conservative reservation for controlled live evaluations."""
from threading import Lock
from ..config import get_settings

_lock = Lock()
_calls = 0
_reserved = 0.0


def policy():
    cfg = get_settings()
    return {"enabled": cfg.live_eval_enabled and cfg.live_eval_max_calls > 0 and cfg.live_eval_max_cost_usd > 0
            and cfg.llm_input_price_per_million_usd > 0 and cfg.llm_output_price_per_million_usd > 0,
            "max_calls": cfg.live_eval_max_calls, "max_cost_usd": cfg.live_eval_max_cost_usd,
            "calls": _calls, "reserved_usd": _reserved}


def reserve(query, max_tokens):
    global _calls, _reserved
    cfg = get_settings()
    if not cfg.live_eval_enabled:
        return
    # UTF-8 byte length plus system/message overhead is deliberately conservative.
    cost = ((len(query.encode("utf-8")) + 10000) * cfg.llm_input_price_per_million_usd
            + max_tokens * cfg.llm_output_price_per_million_usd) / 1_000_000
    with _lock:
        if not policy()["enabled"] or _calls >= cfg.live_eval_max_calls or _reserved + cost > cfg.live_eval_max_cost_usd:
            raise RuntimeError("Live evaluation budget exhausted or unconfigured")
        _calls += 1
        _reserved += cost
