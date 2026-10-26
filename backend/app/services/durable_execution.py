"""Java-owned execution journal and fail-closed request reservations."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from uuid import uuid4
from functools import wraps
from threading import RLock

execution_id_var = ContextVar("durable_execution_id", default=None)
journal_cache_var = ContextVar("durable_journal_cache", default=None)


def new_journal_cache():
    return {"lock":RLock(),"rows":None}


@contextmanager
def durable_execution(identity):
    token = execution_id_var.set(str(identity))
    cache_token = journal_cache_var.set(new_journal_cache())
    try:
        yield
    finally:
        execution_id_var.reset(token)
        journal_cache_var.reset(cache_token)


def _call(method, path, payload=None):
    import httpx
    from .execution import remaining
    base = os.environ.get("BUSINESS_URL", "").rstrip("/")
    key = os.environ.get("INTERNAL_SERVICE_KEY", "")
    if not base or len(key.encode()) < 32:
        raise RuntimeError("Durable execution requires Java journal and service authentication")
    # Neither SDK retry nor a redirect can send a paid call without another
    # reservation; this internal request is deliberately not hooked as outbound.
    with httpx.Client(timeout=min(10, remaining()), follow_redirects=False) as client:
        response = client.request(method, base+path, headers={"X-Service-Key":key}, json=payload)
        response.raise_for_status()
        return response.json() if response.content else {}


def reserve_outbound(kind, *, reserved_tokens=None,map_protocol="rest"):
    identity = execution_id_var.get()
    # Capabilities and indexing also require the central acceptance ledger when a
    # run is pinned. Outside controlled runs, old v1 deployments remain compatible.
    required = os.getenv("OUTBOUND_LEDGER_REQUIRED", "false").lower() in {"1","true","yes"}
    if identity is None and not os.getenv("FINAL_RUN_ID") and not os.getenv("FINAL_ACCEPTANCE_RUN_ID") and not required:
        return
    payload={"execution_id":identity,"kind":"text" if kind=="vision" else kind,"request_key":str(uuid4())}
    if reserved_tokens is not None:
        payload["reserved_tokens"]=reserved_tokens
    if kind=="amap":payload["map_protocol"]=map_protocol
    # The business service pins the campaign. Caller-provided run IDs never select a budget.
    _call("POST", "/internal/v2/outbound/reserve",payload)


def input_hash(request, original=None, instruction="", day_index=None):
    value = {"request":request.model_dump(mode="json"), "original":original.model_dump(mode="json") if original else None,
        "instruction":instruction, "day_index":day_index}
    # Additive empty fact collections have the same meaning as absent collections
    # in saved v1 inputs. Nonempty declarations always change the stage digest.
    for item in (value["request"],value["original"]):
        if isinstance(item,dict) and isinstance(item.get("planning_input"),dict):
            for field in ("reservation_facts","transport_buffers","poi_relations","meal_resolutions"):
                if item["planning_input"].get(field)==[]:
                    item["planning_input"].pop(field)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def compatibility():
    from pathlib import Path
    from ..config import get_settings
    from .time_planner import RULE_VERSION
    cfg=get_settings()
    prompt_hash=hashlib.sha256((Path(__file__).parents[1]/"agents"/"prompts.py").read_bytes()).hexdigest()
    return {"protocol":2, "rules":RULE_VERSION, "model":cfg.llm_model,"temperature":cfg.llm_temperature,
        "max_tokens":cfg.llm_day_max_tokens,"embedding_model":cfg.embedding_model,"prompt":prompt_hash,"schema":"planning-v1"}


def read_checkpoint(stage, digest):
    identity = execution_id_var.get()
    if not identity:
        return None
    cache = journal_cache_var.get()
    if cache is None:
        rows = _call("GET",f"/internal/v2/executions/{identity}/checkpoints").get("checkpoints",[])
    else:
        with cache["lock"]:
            if cache["rows"] is None:
                cache["rows"] = _call("GET",f"/internal/v2/executions/{identity}/checkpoints").get("checkpoints",[])
            rows = list(cache["rows"])
    now = datetime.now(timezone.utc)
    for row in reversed(rows):
        try:
            expiry = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if row.get("stage") == stage and row.get("input_hash") == digest and checkpoint_compatible(stage,row.get("compatibility"),compatibility()) and expiry > now:
                return row.get("payload")
        except (KeyError, ValueError, TypeError):
            continue
    return None


def checkpoint_compatible(stage, saved, current):
    if saved == current:
        return True
    if not isinstance(saved,dict) or not isinstance(current,dict):
        return False
    # v4-v7 change only deterministic scheduling and classification.
    # Provider facts and drafts retain the same models, prompts, schema and input
    # hashes; reusing them must not trigger another paid generation. A solved
    # schedule is deliberately excluded and has to run under the new rules.
    unaffected = stage in {"draft","routes"} or stage.startswith(("day_draft:","attractions:","hotels:","restaurants:","weather:"))
    allowed_upgrade=(saved.get("rules"),current.get("rules")) in {
        ("time-windows-v3-hotel-nights","time-windows-v4-meal-candidates"),
        ("time-windows-v3-hotel-nights","time-windows-v5-hotel-departure"),
        ("time-windows-v4-meal-candidates","time-windows-v5-hotel-departure"),
        ("time-windows-v3-hotel-nights","time-windows-v6-conflict-classification"),
        ("time-windows-v4-meal-candidates","time-windows-v6-conflict-classification"),
        ("time-windows-v5-hotel-departure","time-windows-v6-conflict-classification"),
        ("time-windows-v3-hotel-nights","time-windows-v7-whole-day-budget"),
        ("time-windows-v4-meal-candidates","time-windows-v7-whole-day-budget"),
        ("time-windows-v5-hotel-departure","time-windows-v7-whole-day-budget"),
        ("time-windows-v6-conflict-classification","time-windows-v7-whole-day-budget")}
    if current.get("rules")=="time-windows-v8-fact-completion" and saved.get("rules") in {
        "time-windows-v3-hotel-nights","time-windows-v4-meal-candidates","time-windows-v5-hotel-departure",
        "time-windows-v6-conflict-classification","time-windows-v7-whole-day-budget"}:
        allowed_upgrade=True
    return bool(unaffected and isinstance(saved,dict) and allowed_upgrade
        and {key:value for key,value in saved.items() if key!="rules"}=={key:value for key,value in current.items() if key!="rules"})


def save_checkpoint(stage, digest, payload, ttl=1800):
    identity = execution_id_var.get()
    if identity:
        row = {"stage":stage,"input_hash":digest,"payload":payload,"expires_at":(datetime.now(timezone.utc)+timedelta(seconds=ttl)).isoformat(),"compatibility":compatibility()}
        _call("POST", f"/internal/v2/executions/{identity}/checkpoints",row)
        cache=journal_cache_var.get()
        if cache is not None:
            with cache["lock"]:
                if cache["rows"] is not None:
                    cache["rows"] = [old for old in cache["rows"] if old.get("stage") != stage] + [row]


def checkpoint_node(stage, ttl=1800):
    """Cache trusted provider results, not permission-sensitive research context."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, state):
            from ..models.schemas import POIInfo, WeatherInfo
            digest = input_hash(state["request"])
            key = stage+":"+digest[:16]
            value = read_checkpoint(key,digest)
            if value is not None:
                for name,model in (("attraction_pois",POIInfo),("restaurant_pois",POIInfo),("hotel_pois",POIInfo),("weather_info",WeatherInfo)):
                    if name in value:
                        value[name] = [model.model_validate(row) for row in value[name]]
                return value
            value = fn(self,state)
            payload = {key:[row.model_dump(mode="json") if hasattr(row,"model_dump") else row for row in item] if isinstance(item,list) else item for key,item in value.items()}
            save_checkpoint(key,digest,payload,ttl)
            return value
        return wrapped
    return decorate
