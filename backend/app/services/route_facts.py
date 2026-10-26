"""Route observation time survives memory caches and durable checkpoint reuse."""
from datetime import datetime, timezone, timedelta
from .planning_semantics import FACT_TTLS

EPOCH = "1970-01-01T00:00:00+00:00"


def instant(value):
    stamp=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Route observation requires timezone")
    return stamp


def observation(value, *, fresh=False, now=None):
    """Only a newly invoked provider/fixture may omit observation metadata."""
    now=now or datetime.now(timezone.utc)
    try:
        observed=instant(value["observed_at"]) if value.get("observed_at") else (now if fresh else None)
        if observed is None or observed>now:
            raise ValueError("Unobserved or future route fact")
        expires=observed+timedelta(seconds=FACT_TTLS["route"])
        if value.get("expires_at"):
            expires=min(expires,instant(value["expires_at"]))
        return {"observed_at":observed.isoformat(),"expires_at":expires.isoformat()}
    except (ValueError,TypeError,KeyError):
        return {"observed_at":value.get("observed_at"),"expires_at":EPOCH}


def current(value, now=None):
    now=now or datetime.now(timezone.utc)
    try:
        return instant(value["observed_at"])<=now<instant(value["expires_at"])
    except (ValueError,TypeError,KeyError):
        return False


def cache_expiry(cache):
    values=[fact.get("expires_at",EPOCH) for fact in cache.get("_route_facts",{}).values()]
    return min(values,key=instant) if values else None


def bind_day(day,cache):
    expiry=cache_expiry(cache)
    if expiry:
        day.__pydantic_extra__["route_fact_expires_at"]=expiry
    else:
        day.__pydantic_extra__.pop("route_fact_expires_at",None)
