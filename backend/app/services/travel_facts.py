"""Explicit user declarations, never inferred official reservations or venue hierarchy."""
from datetime import datetime, timezone, timedelta
from math import isfinite


def validate_fact_inputs(inputs):
    def identifier(value, max_length):
        return isinstance(value,str) and bool(value.strip()) and len(value)<=max_length
    identities = set()
    for group in ("reservation_facts", "transport_buffers", "poi_relations", "meal_resolutions"):
        for fact in getattr(inputs, group):
            if not isinstance(fact, dict) or not isinstance(fact.get("confirmed"), bool) or not isinstance(fact.get("source"), str) or not fact["source"].strip() or len(fact["source"]) > 1000:
                raise ValueError("Supplementary facts require source and explicit confirmation")
            try:
                observed, expires = (datetime.fromisoformat(str(fact[key]).replace("Z", "+00:00")) for key in ("observed_at", "expires_at"))
                if not observed.tzinfo or not expires.tzinfo or expires <= observed or observed > datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise ValueError("Supplementary facts require ordered timezone-aware freshness") from None
            fact["authority"] = "user_declared"
            if group == "reservation_facts":
                if not identifier(fact.get("poi_id"),64) or not isinstance(fact.get("status"),str) or fact.get("status") not in {"confirmed", "not_required", "unavailable"}:
                    raise ValueError("Invalid reservation fact")
                datetime.strptime(str(fact.get("date", "")), "%Y-%m-%d")
                identity = (group, fact["poi_id"], fact["date"])
            elif group == "transport_buffers":
                if not identifier(fact.get("commitment_id"),160) or type(fact.get("minutes")) is not int or not 0 <= fact["minutes"] <= 360:
                    raise ValueError("Invalid boarding buffer")
                identity = (group, str(fact["commitment_id"]))
            elif group == "poi_relations":
                parent, child = fact.get("parent_poi_id"), fact.get("child_poi_id")
                if not identifier(parent,64) or not identifier(child,64) or parent == child or not isinstance(fact.get("relation"),str) or fact.get("relation") not in {"included", "separate"}:
                    raise ValueError("Invalid parent-child relation")
                identity = (group, *sorted([parent, child]))
            else:
                loc = fact.get("location", {})
                if type(fact.get("day_index")) is not int or not 0 <= fact["day_index"] <= 29 or not isinstance(fact.get("type"),str) or fact.get("type") not in {"breakfast", "lunch", "dinner", "snack"} or fact.get("mode") != "self_arranged":
                    raise ValueError("Invalid self-arranged meal")
                if not isinstance(loc,dict) or any(type(loc.get(k)) not in (int, float) or not isfinite(loc[k]) or abs(loc[k]) > limit for k, limit in (("longitude", 180), ("latitude", 90))):
                    raise ValueError("Self-arranged meal requires a real endpoint")
                identity = (group, fact["day_index"], fact["type"])
            if identity in identities:
                raise ValueError("Conflicting or duplicate supplementary facts must be resolved")
            identities.add(identity)


def boarding_minutes(inputs, commitment, current):
    # Inline IDs become ledger IDs after persistence. The original ID remains in details.
    identities = {str(commitment.get("id", "")), str(commitment.get("input_commitment_id", ""))}
    matches = [f for f in inputs.transport_buffers if str(f.get("commitment_id", "")) in identities and current(f, "verification")]
    return matches[0]["minutes"] if len(matches) == 1 else None


def relation_pairs(attractions, inputs, current):
    """Known included visits share one occupancy; plausible nesting remains an explicit unknown."""
    from .plan_quality import haversine_km
    pairs, unknown = [], set()
    for i, left in enumerate(attractions):
        for right in attractions[i+1:]:
            ids = {left.poi_id, right.poi_id}
            declared = [f for f in inputs.poi_relations if {f.get("parent_poi_id"), f.get("child_poi_id")} == ids]
            facts = [f for f in declared if current(f, "verification")]
            if len(facts) == 1:
                if facts[0]["relation"] == "included":
                    pairs.append((left.activity_id, right.activity_id))
                continue
            names = [a.name.strip().replace(" ", "") for a in (left, right)]
            if declared or left.poi_id == right.poi_id or (min(map(len, names)) >= 2 and (names[0] in names[1] or names[1] in names[0]) and haversine_km(left, right) < 1):
                pairs.append((left.activity_id, right.activity_id))
                unknown.add(f"poi_relationship_unverified:{left.poi_id}:{right.poi_id}")
    return pairs, unknown
