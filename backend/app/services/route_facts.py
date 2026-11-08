"""Route observation time survives memory caches and durable checkpoint reuse."""
from datetime import datetime, timezone, timedelta
from math import isfinite
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
        fact = {"observed_at":observed.isoformat(),"expires_at":expires.isoformat()}
        for field in ("distance", "walking_distance"):
            try:
                quantity = float(value[field])
                if type(value[field]) is not bool and isfinite(quantity) and quantity >= 0:
                    fact[field] = quantity
            except (KeyError, TypeError, ValueError):
                pass
        if value.get("route_type") in {"walking", "driving", "transit"}:
            fact["route_type"] = value["route_type"]
        if fact.get("route_type") == "walking" and "distance" in fact:
            fact["walking_distance"] = fact["distance"]
        return fact
    except (ValueError,TypeError,KeyError):
        return {"observed_at":value.get("observed_at"),"expires_at":EPOCH}


def current(value, now=None):
    now=now or datetime.now(timezone.utc)
    try:
        return instant(value["observed_at"])<=now<instant(value["expires_at"])
    except (ValueError,TypeError,KeyError):
        return False


def bind_day(day):
    for name in ("route_fact_expires_at", "route_fact_observed_at"):
        value = day.feasibility.get(name)
        if value:
            day.__pydantic_extra__[name] = value
        else:
            day.__pydantic_extra__.pop(name, None)
