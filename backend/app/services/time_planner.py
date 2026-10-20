"""Bounded CP-SAT scheduling over trusted places, never over LLM invented facts.

Required and locked visits are mandatory; omitted optional visits are reported.
An infeasibility result is only about this
finite candidate model; incomplete travel/opening facts produce unverified plans.
"""
from datetime import datetime, timedelta, timezone
from math import ceil, isfinite, cos, radians
import os
import time
import json
from zoneinfo import ZoneInfo

from ..models.schemas import TripPlan, TripRequest
from ..core.exceptions import BizException

RULE_VERSION = "time-windows-v5-hotel-departure"
FACT_TTLS = {"opening": 86400, "price": 86400, "route": 1800, "weather": 3600, "verification": 21600}


def confirmed(commitment):
    return commitment.get("status") in {None,"confirmed"}


def effective_locks(inputs):
    return [*inputs.locks,*inputs._execution_locks]


def fact_current(fact, kind="opening", now=None):
    """A claimed confirmation cannot override missing provenance/date/freshness."""
    now = now or datetime.now(timezone.utc)
    try:
        observed = datetime.fromisoformat(fact["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None or not fact.get("source") or not fact.get("confirmed"):
            return False
        limit = observed + timedelta(seconds=FACT_TTLS[kind])
        if fact.get("expires_at"):
            expiry = datetime.fromisoformat(fact["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                return False
            limit = min(limit, expiry)
        return observed <= now < limit
    except (KeyError, ValueError, TypeError):
        return False


def ensure_activity_ids(plan):
    seen = set()
    for day in plan.days:
        for attraction in day.attractions:
            if not attraction.activity_id:
                attraction.activity_id = f"poi:{day.day_index}:{attraction.poi_id}"
        for meal in day.meals:
            if not meal.activity_id:
                meal.activity_id = f"meal:{day.day_index}:{meal.type}:{meal.poi_id or 'unknown'}"
        for item in [*day.attractions, *day.meals]:
            if item.activity_id in seen:
                raise BizException("活动标识重复，无法安全规划", status_code=422, code="DUPLICATE_ACTIVITY_ID")
            seen.add(item.activity_id)


def city_for_day(request, index):
    return next((s.city for s in request.planning_input.city_segments if s.start_day <= index <= s.end_day), request.city)


def scope_days(request, fallback=None):
    scope = request.planning_input.revision_scope
    return set(scope.day_indices or ([fallback] if fallback is not None else [])) if scope else (
        {fallback} if fallback is not None else set(range(request.travel_days)))


def _eligible(original_day, scope):
    ids = set(scope.activity_ids) if scope else set()
    if scope and scope.start_at:
        timed_ids=set()
        intervals = {row.get("activity_id"): row for row in original_day.timeline}
        for item in [*original_day.attractions, *original_day.meals]:
            row = intervals.get(item.activity_id)
            if not row:
                continue
            try:
                start, end = (datetime.fromisoformat(row[key]) for key in ("start_at", "end_at"))
                if start >= scope.start_at and end <= scope.end_at:
                    timed_ids.add(item.activity_id)
            except (KeyError, ValueError, TypeError):
                pass
        ids = ids & timed_ids if scope.activity_ids else timed_ids
        if not ids:
            raise BizException("所选时段没有可安全定位的活动", status_code=409, code="SCOPE_UNRESOLVED")
    return ids


def protect_revision(plan, original, request, fallback):
    """Restore content outside scope before any deterministic postprocessing.

    Missing legacy IDs are assigned on a detached original copy, then copied back
    only to touched days, preserving all other day serializations byte-for-byte.
    """
    original = original.model_copy(deep=True)
    allowed = scope_days(request, fallback)
    if not allowed or any(i < 0 or i >= len(original.days) for i in allowed):
        raise BizException("改排范围无效", status_code=422, code="INVALID_REVISION_SCOPE")
    preserved = {d.day_index: d.model_copy(deep=True) for d in original.days if d.day_index not in allowed}
    ensure_activity_ids(original)
    protected = set(request.planning_input.completed_activity_ids) | {
        str(lock.get("activity_id")) for lock in request.planning_input.locks}
    reserved = [c for c in request.planning_input.commitments if confirmed(c)]
    protected |= {c.get("activity_id") for c in reserved if c.get("activity_id")}
    reserved_pois = {c.get("poi_id") for c in reserved if c.get("poi_id")}
    protected |= {a.activity_id for d in original.days for a in [*d.attractions,*d.meals] if a.poi_id in reserved_pois}
    scope = request.planning_input.revision_scope
    for index, old_day in enumerate(original.days):
        if old_day.day_index not in allowed:
            plan.days[index] = preserved[old_day.day_index]
            continue
        day = plan.days[index]
        eligible = _eligible(old_day, scope)
        partial = bool(scope and (scope.activity_ids or scope.start_at))
        original_ids = {a.poi_id: a.activity_id for a in old_day.attractions}
        for attraction in day.attractions:
            attraction.activity_id = original_ids.get(attraction.poi_id, attraction.activity_id) or f"poi:{index}:{attraction.poi_id}"
        for meal in day.meals:
            old = next((m for m in old_day.meals if m.type == meal.type and m.poi_id == meal.poi_id), None)
            meal.activity_id = old.activity_id if old else (meal.activity_id or f"meal:{index}:{meal.type}:{meal.poi_id or 'unknown'}")
        for field in ("attractions", "meals"):
            old_items, new_items = getattr(old_day, field), getattr(day, field)
            fixed = {a.activity_id: a for a in old_items if a.activity_id in protected or (partial and a.activity_id not in eligible)}
            # Retain the original slots for fixed content; only consume generated
            # replacements for mutable slots. Extra generated items require a full-day scope.
            mutable = [a for a in new_items if a.activity_id not in fixed]
            merged = []
            for old in old_items:
                if old.activity_id in fixed:
                    merged.append(fixed[old.activity_id].model_copy(deep=True))
                elif mutable:
                    merged.append(mutable.pop(0))
            if not partial:
                merged.extend(mutable)
            setattr(day, field, merged)
        if partial:
            for field in ("description", "theme", "activities", "hotel"):
                if hasattr(old_day, field):
                    setattr(day, field, getattr(old_day, field))
        # Existing timeline fixes immutable activities at their original times.
        fixed_ids = protected | ({a.activity_id for a in [*old_day.attractions, *old_day.meals]} - eligible if partial else set())
        outside_ids={a.activity_id for a in [*old_day.attractions,*old_day.meals]}-eligible if partial else set()
        request.planning_input._scope_mutable_ids.update(a.activity_id for a in [*day.attractions,*day.meals] if a.activity_id not in outside_ids)
        user_time_ids = {lock.get("activity_id") for lock in request.planning_input.locks if lock.get("mode") == "time"}
        time_fixed_ids = set(request.planning_input.completed_activity_ids) | user_time_ids
        if partial:
            time_fixed_ids |= {a.activity_id for a in [*old_day.attractions,*old_day.meals]} - eligible
        unknown_reservations = [c for c in reserved if not c.get("start_at") or not c.get("end_at")]
        time_fixed_ids |= {a.activity_id for a in [*old_day.attractions,*old_day.meals] if any(a.activity_id == c.get("activity_id") or a.poi_id == c.get("poi_id") for c in unknown_reservations)}
        extra_locks = list(request.planning_input._execution_locks)
        existing_locks = {lock.get("activity_id") for lock in [*request.planning_input.locks,*extra_locks]}
        extra_locks.extend({"activity_id":identity,"mode":"content"} for identity in fixed_ids if identity not in existing_locks)
        timeline_ids = {row.get("activity_id") for row in old_day.timeline}
        if (set(request.planning_input.completed_activity_ids) & {a.activity_id for a in [*old_day.attractions,*old_day.meals]}) - timeline_ids:
            raise BizException("已完成活动缺少原始时间轴，无法安全调整剩余时段",status_code=409,code="COMPLETED_TIME_UNKNOWN")
        for row in old_day.timeline:
            if row.get("activity_id") and row.get("kind") in {"attraction","meal"} and row.get("start_at"):
                request.planning_input._baseline_times[row["activity_id"]] = row["start_at"]
            if row.get("activity_id") in time_fixed_ids and row.get("activity_id") not in user_time_ids:
                extra_locks.append({"activity_id": row["activity_id"], "mode": "time", "start_at": row.get("start_at"), "end_at": row.get("end_at")})
        request.planning_input._execution_locks = extra_locks
    return allowed


def _minutes(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _local_minute(value, day, zone):
    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Timezone required")
    midnight = datetime.fromisoformat(day.date).replace(tzinfo=zone)
    return int((instant.astimezone(zone) - midnight).total_seconds() // 60)


def _location(value):
    from ..models.schemas import Location
    if value is None:
        return None
    try:
        return value if isinstance(value, Location) else Location.model_validate(value)
    except Exception:
        return None


def _route(left, right, planner, city, route_type, cache, unknown):
    if not left or not right:
        unknown.add("route_endpoint_unknown")
        return 30, False
    if left == right:
        return 0, True
    key = (left.longitude, left.latitude, right.longitude, right.latitude, city, route_type)
    if key not in cache:
        try:
            from .execution import remaining
            remaining()
            if cache.get("_lookups",0) >= max(1,int(os.getenv("PLANNER_ROUTE_LOOKUPS","120"))) or time.monotonic() >= cache.get("_deadline",float("inf")):
                raise ValueError("Route lookup budget exhausted")
            cache["_lookups"] = cache.get("_lookups",0) + 1
            result = planner.plan_route_by_locations(left, right, route_type=route_type, city=city) if planner else {}
            seconds = float(result["duration"])
            if not isfinite(seconds) or seconds < 0 or (seconds == 0 and left != right):
                raise ValueError("Invalid route duration")
            cache[key] = (ceil(seconds / 60), True)
        except Exception:
            cache[key] = (30, False)
    if not cache[key][1]:
        unknown.add("route_duration_estimated_30_minutes")
        if key in cache.get("_restored_unverified",set()):
            unknown.add("route_failure_not_retried_on_resume")
    return cache[key]


def _nodes(day, inputs, low, high, zone, unknown):
    nodes, conflicts = [], []
    locks = {lock.get("activity_id"): lock for lock in effective_locks(inputs)}
    for attraction in day.attractions:
        duration = max(1, attraction.visit_duration)
        intervals = [(low, high - duration)]
        matching = [fact for fact in inputs.opening_windows if fact.get("poi_id") == attraction.poi_id and fact.get("date") == day.date]
        valid = [fact for fact in matching if fact_current(fact)]
        if len(valid) == 1:
            fact = valid[0]
            if fact.get("closed"):
                intervals = []
            else:
                intervals = [(max(low, _minutes(w["start"])), min(high - duration, _minutes(w["end"]) - duration,
                    _minutes(fact["last_entry"]) if fact.get("last_entry") else high)) for w in fact.get("windows", [])]
        else:
            unknown.add(f"opening_{'conflict' if len(valid) > 1 else 'unknown_or_stale'}:{attraction.activity_id}")
        nodes.append(dict(activity_id=attraction.activity_id, title=attraction.name, kind="attraction", duration=duration,
            intervals=intervals, location=attraction.location, source=valid[0].get("source", "") if len(valid) == 1 else "", evidence_refs=[attraction.poi_id],
            optional=attraction.optional, priority=attraction.priority))
    defaults = {"breakfast": ("08:00", "10:00", 30), "lunch": ("11:30", "14:00", 60), "dinner": ("17:30", "21:00", 60)}
    explicit_meal_types={window.get("type") for window in inputs.meal_windows}
    for meal_type in explicit_meal_types-{meal.type for meal in day.meals}:
        conflicts.append(f"required_meal_candidate_missing:{meal_type}")
    for meal in day.meals:
        default = defaults.get(meal.type, ("08:00", "21:00", 30))
        window = next((w for w in inputs.meal_windows if w.get("type") == meal.type), {})
        duration = int(window.get("duration_minutes", default[2]))
        meal_start,meal_end = _minutes(window.get("start",default[0])),_minutes(window.get("end",default[1]))
        intervals = [(max(low,meal_start),min(high,meal_end)-duration)]
        facts = [fact for fact in inputs.opening_windows if fact.get("poi_id") == meal.poi_id and fact.get("date") == day.date and fact_current(fact)]
        if len(facts) == 1:
            if facts[0].get("closed"):
                intervals=[]
            else:
                intervals=[(max(low,meal_start,_minutes(w["start"])),min(high,meal_end,_minutes(w["end"]))-duration) for w in facts[0].get("windows",[])]
        else:
            unknown.add(f"meal_opening_{'conflict' if len(facts)>1 else 'unverified'}:{meal.activity_id}")
        nodes.append(dict(activity_id=meal.activity_id, title=meal.name, kind="meal", duration=duration,
            # AI restaurant selections are candidates, not user reservations.
            # Explicit meal windows and the protected IDs below remain hard.
            intervals=intervals,optional=meal.type not in explicit_meal_types,priority=10,
            location=meal.location, source=meal.fact_source, evidence_refs=[meal.poi_id] if meal.poi_id else []))
        if not meal.poi_id or not meal.location:
            unknown.add(f"meal_location_unknown:{meal.activity_id}")
    for commitment in inputs.commitments:
        if not confirmed(commitment):
            continue
        if commitment.get("day_index") == day.day_index and (not commitment.get("start_at") or not commitment.get("end_at")):
            unknown.add(f"commitment_time_unknown:{commitment.get('id','unknown')}")
            continue
        if commitment.get("category") in {"transport", "train", "flight"} and commitment.get("origin_city") and commitment.get("destination_city") and commitment["origin_city"] != commitment["destination_city"]:
            # Confirmed intercity transport is an external fixed bridge, not a
            # city-map edge and not a visit that the local solver may reorder.
            continue
        if commitment.get("day_index") != day.day_index or commitment.get("status") in {"cancelled", "canceled"}:
            continue
        if not commitment.get("start_at") or not commitment.get("end_at"):
            unknown.add(f"commitment_time_unknown:{commitment.get('id','unknown')}")
            continue
        try:
            start = _local_minute(commitment["start_at"], day, zone)
            end = _local_minute(commitment["end_at"], day, zone)
            if end <= start:
                raise ValueError("Invalid commitment duration")
            match = next((n for n in nodes if n["activity_id"] == commitment.get("activity_id") or (commitment.get("poi_id") and commitment.get("poi_id") in n["evidence_refs"])), None)
            if match:
                if commitment.get("time_mode") == "entry_window":
                    match["intervals"] = [(max(start,a),min(end,b)) for a,b in match["intervals"]]
                else:
                    # Fixed occupancy and allowed admission windows have distinct
                    # semantics; an entry deadline is not an exit deadline.
                    extension = max(0,end-start-match["duration"])
                    match.update(intervals=[(max(start,a),min(start,b-extension)) for a,b in match["intervals"]], duration=end-start)
            else:
                duration = int(commitment.get("duration_minutes",60)) if commitment.get("time_mode") == "entry_window" else end-start
                nodes.append(dict(activity_id=str(commitment.get("activity_id") or f"commitment:{commitment['id']}"), title=commitment.get("title", "固定安排"),
                    kind="commitment", duration=duration, intervals=[(start,end if commitment.get("time_mode") == "entry_window" else start)], location=_location(commitment.get("origin_location") or commitment.get("location")),
                    departure_location=_location(commitment.get("destination_location")), source="user_confirmed", evidence_refs=[str(commitment.get("id", ""))]))
        except (KeyError, ValueError, TypeError):
            conflicts.append(f"invalid_commitment:{commitment.get('id', 'unknown')}")
    for node in nodes:
        lock = locks.get(node["activity_id"])
        if lock and lock.get("mode") == "time":
            try:
                start = _local_minute(lock["start_at"], day, zone)
                end = _local_minute(lock["end_at"], day, zone)
                node["intervals"] = [(max(start, a), min(start, b)) for a, b in node["intervals"]]
                if end - start != node["duration"]:
                    conflicts.append(f"locked_duration_changed:{node['activity_id']}")
            except (KeyError, ValueError, TypeError):
                conflicts.append(f"invalid_time_lock:{node['activity_id']}")
        if inputs.revision_scope and inputs.revision_scope.start_at and node["activity_id"] in inputs._scope_mutable_ids:
            scope_start=_local_minute(inputs.revision_scope.start_at,day,zone)
            scope_end=_local_minute(inputs.revision_scope.end_at,day,zone)
            node["intervals"]=[(max(scope_start,a),min(scope_end-node["duration"],b)) for a,b in node["intervals"]]
        node["intervals"] = [(max(low, a), min(high-node["duration"], b)) for a, b in node["intervals"] if max(low, a) <= min(high-node["duration"], b)]
        if not node["intervals"]:
            conflicts.append(f"no_time_window:{node['activity_id']}")
    return nodes, conflicts


def _hotel_anchor(inputs, request, index, day=None):
    """hotel_stays[i] denotes the night after day i, not that day's origin."""
    hotel = next((h for h in inputs.hotel_stays if h.get("day_index") == index), None)
    if hotel:
        return _location(hotel.get("location")), hotel.get("city") or city_for_day(request,index)
    if day is not None and day.hotel:
        return day.hotel.location, city_for_day(request,index)
    return None, city_for_day(request,index)


def _transport_buffer_gaps(timeline,inputs):
    """A confirmed departure time does not confirm boarding lead time."""
    gaps=set()
    note="接驳仅按用户输入的班次起止计算；提前到站、安检和检票要求尚未确认，请向承运方核实并预留时间，当前不能保证赶上班次。"
    for trip in inputs.commitments:
        if not confirmed(trip) or trip.get("category") not in {"transport","train","flight"}:
            continue
        identity=str(trip.get("activity_id") or f"commitment:{trip.get('id','unknown')}")
        for row in timeline:
            if row.get("activity_id")==identity and row.get("kind")=="commitment":
                gaps.add(f"transport_predeparture_buffer_unverified:{trip.get('id','unknown')}")
                row["notes"]=list(dict.fromkeys([*row.get("notes",[]),note]))
    return gaps


def _hotel_candidate_request(request,index):
    """Project the same city request used by the existing candidate search."""
    cities=[city_for_day(request,i) for i in range(request.travel_days)]
    if all(city==request.city for city in cities):
        return request
    start=end=index
    while start and cities[start-1]==cities[index]: start-=1
    while end+1<len(cities) and cities[end+1]==cities[index]: end+=1
    local=request.model_copy(deep=True)
    local.city=cities[index]
    local.start_date=(datetime.fromisoformat(request.start_date)+timedelta(days=start)).date().isoformat()
    local.end_date=(datetime.fromisoformat(request.start_date)+timedelta(days=end)).date().isoformat()
    local.travel_days=end-start+1
    local.planning_input.city_segments=[]
    local._include_last_night=end+1<request.travel_days
    local.planning_input.commitments=[{**c,"day_index":c["day_index"]-start} for c in request.planning_input.commitments if isinstance(c.get("day_index"),int) and start<=c["day_index"]<=end]
    local.planning_input.day_windows=[w.model_copy(update={"day_index":w.day_index-start}) for w in request.planning_input.day_windows if start<=w.day_index<=end]
    return local


def _fit_recommended_hotels(plan,request,planner,cache,allowed_days):
    """Only mutable recommendations may adapt to tomorrow's fixed departure."""
    from .durable_execution import input_hash,read_checkpoint
    from .plan_quality import transport_to_route_type
    from ..models.schemas import Hotel
    inputs=request.planning_input
    options={}
    for arrival_day in plan.days:
        if arrival_day.day_index not in allowed_days or not arrival_day.day_index:
            continue
        night=next((day for day in plan.days if day.day_index==arrival_day.day_index-1),None)
        if night is None:
            continue
        origin_city=city_for_day(request,night.day_index)
        window=next((w for w in inputs.day_windows if w.day_index==arrival_day.day_index),None)
        earliest=_minutes(window.start if window else inputs.day_start)
        for trip in inputs.commitments:
            if not confirmed(trip) or trip.get("category") not in {"transport","train","flight"} or trip.get("origin_city")!=origin_city or trip.get("destination_city")!=city_for_day(request,arrival_day.day_index) or trip.get("origin_city")==trip.get("destination_city"):
                continue
            try:
                departure=_local_minute(trip["start_at"],arrival_day,ZoneInfo(inputs.timezone))
            except (KeyError,TypeError,ValueError):
                continue
            if not earliest<=departure<1440:
                continue  # A hotel cannot repair a departure outside the hard window.
            station=_location(trip.get("origin_location") or trip.get("location"))
            current,current_city=_hotel_anchor(inputs,request,night.day_index,night)
            if not station or not current or current_city!=origin_city:
                continue
            unknown=set()
            duration,known=_route(current,station,planner,origin_city,transport_to_route_type(request.transportation),cache,unknown)
            if not known or duration<=departure-earliest:
                continue
            leave=(datetime.fromisoformat(trip["start_at"])-timedelta(minutes=duration)).isoformat()
            options[arrival_day.day_index]=[
                {"action":"allow_earlier_departure","start_at":leave,"message":"当前住宿接驳超出最早出发边界；需用户明确允许更早出发"},
                {"action":"choose_other_hotel","message":"选择并重新核验能赶上固定班次的前晚住宿；未自动放宽约束"}]
            protected_ids=set(inputs.completed_activity_ids)|{str(lock.get("activity_id","")) for lock in effective_locks(inputs)}
            fixed=(any(h.get("day_index")==night.day_index for h in inputs.hotel_stays)
                or any(identity==f"hotel:{night.day_index}" or identity.startswith(f"hotel:{night.day_index}:") for identity in protected_ids)
                or any(confirmed(c) and c.get("category") in {"hotel","accommodation"} and c.get("day_index")==night.day_index for c in inputs.commitments))
            if fixed or night.day_index not in allowed_days:
                continue
            local=_hotel_candidate_request(request,night.day_index)
            digest=input_hash(local)
            saved=read_checkpoint("hotels:"+digest[:16],digest)
            if not saved:
                continue
            candidates=[row for row in saved.get("hotel_pois",[]) if row.get("id") and _location(row.get("location")) and _location(row.get("location"))!=current]
            distance=lambda row:((row["location"]["longitude"]-station.longitude)*cos(radians(station.latitude)))**2+(row["location"]["latitude"]-station.latitude)**2
            for candidate in sorted(candidates,key=lambda row:(distance(row),row["id"]))[:3]:
                access,verified=_route(_location(candidate["location"]),station,planner,origin_city,transport_to_route_type(request.transportation),cache,set())
                if not verified or access>departure-earliest:
                    continue
                old_name=night.hotel.name if night.hotel else ""
                night.hotel=Hotel(name=candidate["name"],address=candidate.get("address") or "",location=candidate["location"],type=request.accommodation)
                night.__pydantic_extra__["hotel_selection"]={"candidate_poi_id":candidate["id"],"previous_name":old_name,
                    "source":"trusted_hotel_candidates","status":"recommended_not_booked","reason":"next_day_fixed_departure","access_minutes":access}
                options.pop(arrival_day.day_index,None)
                break
    return options


def solve_day(day, request, route_planner=None, cache=None, seconds=None, previous_day=None):
    from ortools.sat.python import cp_model
    from .plan_quality import transport_to_route_type
    from .execution import remaining
    inputs, zone = request.planning_input, ZoneInfo(request.planning_input.timezone)
    low, high = _minutes(inputs.day_start), _minutes(inputs.day_end)
    window = next((w for w in inputs.day_windows if w.day_index == day.day_index), None)
    if window:
        low, high = _minutes(window.start), _minutes(window.end)
    hard_start,hard_end = low,high
    if inputs.arrival_at:
        low = max(low, _local_minute(inputs.arrival_at, day, zone))
    if inputs.departure_at:
        high = min(high, _local_minute(inputs.departure_at, day, zone))
    city = city_for_day(request,day.day_index)
    unknown, cache = set(), cache if cache is not None else {}
    route_type = transport_to_route_type(request.transportation)
    night_anchor,night_city = _hotel_anchor(inputs,request,day.day_index,day)
    prior_anchor,prior_city = _hotel_anchor(inputs,request,day.day_index-1,previous_day) if day.day_index else (night_anchor,night_city)
    bridges, connections, connection_conflicts, inbound_anchor, outbound_anchor = [], [], [], None, None
    connection_minutes = 0
    for commitment in inputs.commitments:
        if not confirmed(commitment) or commitment.get("category") not in {"transport","train","flight"} or not commitment.get("origin_city") or not commitment.get("destination_city") or commitment["origin_city"] == commitment["destination_city"]:
            continue
        try:
            departure = _local_minute(commitment["start_at"],day,zone)
            arrival = _local_minute(commitment["end_at"],day,zone)
            if city in {commitment["origin_city"],commitment["destination_city"]} and departure < 1440 and arrival > 0 and (departure < hard_start or arrival > hard_end):
                connection_conflicts.append(f"fixed_transport_outside_day_window:{commitment['id']}")
            if city == commitment["origin_city"] and 0 <= departure < 1440:
                high = min(high,departure)
                outbound_anchor = _location(commitment.get("origin_location") or commitment.get("location"))
            if city == commitment["destination_city"] and 0 <= arrival < 1440:
                low = max(low,arrival)
                inbound_anchor = _location(commitment.get("destination_location"))
                # The arrival-day solver starts at the arrival station. Account
                # separately for leaving the previous night's origin-city hotel,
                # even when the departure precedes the local sightseeing window.
                origin = _location(commitment.get("origin_location") or commitment.get("location"))
                if prior_city != commitment["origin_city"]:
                    unknown.add("origin_hotel_city_unverified")
                elif departure >= 0:
                    if prior_anchor is None:
                        unknown.add("origin_hotel_endpoint_unknown")
                    if origin is None:
                        unknown.add("origin_station_endpoint_unknown")
                    access,known = _route(prior_anchor,origin,route_planner,commitment["origin_city"],route_type,cache,unknown)
                    midnight = datetime.fromisoformat(day.date).replace(tzinfo=zone)
                    access_start = midnight+timedelta(minutes=departure-access)
                    if departure-access < hard_start or departure > hard_end:
                        connection_conflicts.append(f"origin_connection_outside_day_window:{commitment['id']}")
                    if previous_day and previous_day.timeline:
                        try:
                            prior_finish=max(datetime.fromisoformat(r["end_at"]) for r in previous_day.timeline)
                            if prior_finish > access_start:
                                connection_conflicts.append("origin_hotel_connection_overlaps_previous_day")
                        except (KeyError,ValueError,TypeError):
                            unknown.add("previous_day_finish_unverified")
                    connections.append(dict(activity_id=f"connection:{commitment['id']}:origin",title="住宿前往跨城出发站",kind="travel",
                        start_at=access_start.isoformat(),end_at=commitment["start_at"],status="confirmed" if known else "estimated",
                        source="map" if known else "conservative_allowance",evidence_refs=[str(commitment["id"])],
                        notes=[] if known else ["住宿或站点资料不足，接驳仅为30分钟占位，尚未核验"]))
                    connection_minutes += access
                else:
                    unknown.add("origin_hotel_connection_before_current_day_unverified")
            if city in {commitment["origin_city"],commitment["destination_city"]} and departure < 1440 and arrival > 0:
                bridges.append(dict(activity_id=str(commitment.get("activity_id") or f"commitment:{commitment['id']}"),title=commitment.get("title","跨城交通"),kind="commitment",start_at=commitment["start_at"],end_at=commitment["end_at"],status="confirmed",source="user_confirmed",evidence_refs=[str(commitment["id"])],notes=[]))
        except (KeyError,ValueError,TypeError):
            pass  # Input model rejects malformed times before this point.
    high = min(high, low+request.constraints.daily_minutes)
    if day.day_index > 0 and city_for_day(request,day.day_index-1) != city and not any(c.get("destination_city") == city and confirmed(c) and c.get("end_at") and 0 <= _local_minute(c["end_at"],day,zone) < 1440 for c in inputs.commitments):
        unknown.add("cross_city_confirmed_transport_missing")
    nodes, conflicts = _nodes(day, inputs, low, high, zone, unknown)
    conflicts.extend(connection_conflicts)
    unknown.update(_transport_buffer_gaps(bridges,inputs))
    required_names = {n.casefold() for n in request.constraints.must_visit}
    required_ids = {a.activity_id for a in day.attractions if {a.name.casefold(), *(n.casefold() for n in a.requested_names)} & required_names}
    required_ids |= set(inputs.completed_activity_ids) | {lock.get("activity_id") for lock in effective_locks(inputs)}
    required_ids |= {c.get("activity_id") for c in inputs.commitments if confirmed(c)}
    required_pois = {c.get("poi_id") for c in inputs.commitments if confirmed(c)}
    avoided_names = {name.casefold() for name in request.constraints.avoid}
    excluded_ids = {a.activity_id for a in day.attractions if {a.name.casefold(),*(n.casefold() for n in a.requested_names)} & avoided_names}
    for node in nodes:
        node["optional"] = bool(node.get("optional")) and node["activity_id"] not in required_ids and not (set(node["evidence_refs"]) & required_pois)
        if node["activity_id"] in excluded_ids:
            if node["activity_id"] in required_ids or set(node["evidence_refs"]) & required_pois:
                conflicts.append(f"protected_activity_excluded:{node['activity_id']}")
            else:
                node["optional"],node["intervals"] = True,[]
    # Empty windows can only remove an explicitly optional stop. Hard activities
    # keep their conflict and cannot disappear to improve the objective.
    forced_omissions = [n["activity_id"] for n in nodes if not n["intervals"] and n["optional"]]
    conflicts = [c for c in conflicts if not any(c == f"no_time_window:{identity}" for identity in forced_omissions)]
    nodes = [n for n in nodes if n["activity_id"] not in forced_omissions]
    report = {"status": "unverified", "solver_status": "NOT_RUN", "unknown": [], "conflicts": conflicts, "omitted_activity_ids": []}
    if conflicts or low >= high or not nodes:
        report.update(status="infeasible" if not unknown else "unverified", solver_status="INVALID_WINDOWS", unknown=sorted(unknown))
        return [], report, None
    cap = max(1, min(40, int(os.getenv("PLANNER_MAX_DAILY_ACTIVITIES", "20"))))
    if len(nodes) > cap:
        report.update(status="not_found", solver_status="CANDIDATE_LIMIT", conflicts=["daily_candidate_limit_exceeded"], unknown=sorted(unknown))
        return [], report, None
    # A hotel change affects both directed endpoints. Never ask a city route
    # provider to connect a previous hotel in a different city as a local edge.
    start_anchor = inbound_anchor or (prior_anchor if prior_city == city else None)
    end_anchor = outbound_anchor or (night_anchor if night_city == city else None)
    if not inbound_anchor and prior_city != city:
        unknown.add("previous_hotel_in_other_city")
    if night_city != city:
        unknown.add("tonight_hotel_city_unverified")
    if start_anchor is None or end_anchor is None:
        unknown.add("hotel_endpoint_unknown")
    model = cp_model.CpModel()
    starts = [model.new_int_var_from_domain(cp_model.Domain.from_intervals(n["intervals"]), f"start_{i}") for i, n in enumerate(nodes)]
    presence = [model.new_bool_var(f"present_{i}") for i in range(len(nodes))]
    arcs, literals, travels = [], {}, {}
    for index,node in enumerate(nodes):
        if not node["optional"]:
            model.add(presence[index] == 1)
        arcs.append((index+1,index+1,presence[index].Not()))
    attraction_presence = [presence[i] for i,n in enumerate(nodes) if n["kind"] == "attraction"]
    if attraction_presence:
        model.add(sum(attraction_presence) >= 1)
    for left in range(len(nodes)+1):
        for right in range(len(nodes)+1):
            if left == right:
                continue
            source = (nodes[left-1].get("departure_location") or nodes[left-1]["location"]) if left else start_anchor
            destination = nodes[right-1]["location"] if right else end_anchor
            travel, known = _route(source, destination, route_planner, city, route_type, cache, unknown)
            literal = model.new_bool_var(f"edge_{left}_{right}")
            arcs.append((left, right, literal))
            literals[left, right], travels[left, right] = literal, (travel, known)
            if left and right:
                model.add(starts[right-1] >= starts[left-1] + nodes[left-1]["duration"] + travel).only_enforce_if(literal)
            elif right:
                model.add(starts[right-1] >= low + travel).only_enforce_if(literal)
            else:
                model.add(starts[left-1] + nodes[left-1]["duration"] + travel <= high).only_enforce_if(literal)
    model.add_circuit(arcs)
    # Preserve relative order of immutable content. Explicit time locks above
    # additionally preserve arrival and finish times when a prior timeline exists.
    protected = set(inputs.completed_activity_ids) | {l.get("activity_id") for l in effective_locks(inputs)}
    # The content lists are grouped by kind, not chronological: attractions are
    # constructed before breakfast/lunch/dinner. Only attraction relative order
    # is a content invariant; meals and commitments already have time windows.
    fixed_indices = [i for i, n in enumerate(nodes) if n["kind"] == "attraction" and n["activity_id"] in protected]
    for left, right in zip(fixed_indices, fixed_indices[1:]):
        model.add(starts[right] >= starts[left] + nodes[left]["duration"])
    # Only optional visits carry omission penalties; hard constraints remain
    # logical constraints and can never be traded against a shorter route.
    changed,drift = [],[]
    for index,node in enumerate(nodes):
        if node["activity_id"] in inputs._baseline_times:
            previous = _local_minute(inputs._baseline_times[node["activity_id"]],day,zone)
            moved = model.new_bool_var(f"moved_{index}")
            deviation = model.new_int_var(0,2880,f"deviation_{index}")
            model.add(starts[index] == previous).only_enforce_if(moved.Not())
            model.add_abs_equality(deviation,starts[index]-previous)
            changed.append(moved)
            drift.append(deviation)
    model.minimize(sum((1-presence[i]) * n.get("priority",50) * 100_000_000_000_000 for i,n in enumerate(nodes) if n["optional"])
        + sum(changed)*10_000_000_000 + sum(drift)*10000
        + sum(travels[key][0] * literal for key, literal in literals.items()) * 100 + sum(starts))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = min(remaining(), seconds if seconds is not None else float(os.getenv("PLANNER_DAY_SECONDS", "2")))
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    report.update(solver_status=solver.status_name(status), unknown=sorted(unknown), omitted_activity_ids=forced_omissions)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        report["status"] = "infeasible" if status == cp_model.INFEASIBLE and not unknown else "not_found"
        report["conflicts"] = ["candidate_time_model_has_no_solution" if status == cp_model.INFEASIBLE else "solver_budget_exhausted"]
        return [], report, None
    midnight = datetime.fromisoformat(day.date).replace(tzinfo=zone)
    instant = lambda value: (midnight + timedelta(minutes=value)).isoformat()
    timeline, order, total_travel = [], [], 0
    previous, cursor = 0, low
    omitted = forced_omissions + [node["activity_id"] for i,node in enumerate(nodes) if not solver.value(presence[i])]
    report["omitted_activity_ids"] = omitted
    report["unmet_meals"]=[{"activity_id":meal.activity_id,"type":meal.type,"title":meal.name,
        "reason":"restaurant_candidate_does_not_fit_time_and_routes","action":"该餐厅未安排，需就近用餐并重新核验"}
        for meal in day.meals if meal.activity_id in omitted]
    unknown.update(f"meal_candidate_unassigned:{meal['type']}:{meal['activity_id']}" for meal in report["unmet_meals"])
    report["unknown"]=sorted(unknown)
    for _ in nodes:
        following = next(right for (left, right), literal in literals.items() if left == previous and solver.value(literal))
        if following == 0:
            break
        node = nodes[following-1]
        travel, known = travels[previous, following]
        if travel:
            timeline.append(dict(activity_id=f"route:{previous}:{node['activity_id']}", title=f"前往{node['title']}", kind="travel",
                start_at=instant(cursor), end_at=instant(cursor+travel), status="confirmed" if known else "estimated", source="map" if known else "conservative_allowance", evidence_refs=[], notes=[] if known else ["路线缺失，暂以30分钟占位；未验证可行性"]))
        start = solver.value(starts[following-1])
        if start > cursor+travel:
            timeline.append(dict(activity_id=f"wait:{node['activity_id']}", title="等待", kind="waiting", start_at=instant(cursor+travel), end_at=instant(start), status="estimated", source="solver", evidence_refs=[], notes=[]))
        timeline.append(dict(activity_id=node["activity_id"], title=node["title"], kind=node["kind"], start_at=instant(start), end_at=instant(start+node["duration"]),
            status="unverified" if unknown else "confirmed", source=node["source"], evidence_refs=node["evidence_refs"], notes=[], location=node["location"].model_dump() if node["location"] else None))
        order.append(node["activity_id"])
        total_travel += travel
        cursor, previous = start+node["duration"], following
    travel, known = travels[previous, 0]
    if travel:
        timeline.append(dict(activity_id="hotel:return", title="前往出发站" if outbound_anchor else "返回住宿", kind="travel", start_at=instant(cursor), end_at=instant(cursor+travel), status="confirmed" if known else "estimated", source="map" if known else "conservative_allowance", evidence_refs=[], notes=[]))
    timeline.extend(connections)
    timeline.extend(bridges)
    timeline.sort(key=lambda item:item["start_at"])
    unknown.update(_transport_buffer_gaps(timeline,inputs))
    report["unknown"]=sorted(unknown)
    report.update(status="unverified" if unknown else "feasible", route_minutes=total_travel+travel+connection_minutes, finish_at=instant(cursor+travel))
    return timeline, report, order


def plan_time_windows(plan: TripPlan, request: TripRequest, route_planner=None, allowed_days=None, route_evidence=None):
    from .durable_execution import input_hash, read_checkpoint, save_checkpoint
    started = time.monotonic()
    limit = max(1, min(120, float(os.getenv("PLANNER_TOTAL_SECONDS", "30"))))
    cache, reports = {"_deadline":started+limit}, []
    digest = input_hash(request,plan,instruction=json.dumps({"baseline_times":request.planning_input._baseline_times,
        "scope_mutable_ids":sorted(request.planning_input._scope_mutable_ids),"execution_locks":request.planning_input._execution_locks},sort_keys=True))
    saved_routes = read_checkpoint("routes",digest)
    if saved_routes:
        for row in saved_routes.get("routes",[]):
            key=tuple(row["key"])
            cache[key] = (row["minutes"],bool(row["verified"]))
            if not row["verified"]:
                cache.setdefault("_restored_unverified",set()).add(key)
    allowed_days = set(range(len(plan.days))) if allowed_days is None else allowed_days
    hotel_options=_fit_recommended_hotels(plan,request,route_planner,cache,allowed_days)
    for day in plan.days:
        if day.day_index not in allowed_days:
            continue
        checkpoint_stage=f"schedule_day:{day.day_index}:{digest[:16]}"
        cached_day=read_checkpoint(checkpoint_stage,digest)
        dated_facts=[fact for fact in request.planning_input.opening_windows if fact.get("date")==day.date and fact.get("confirmed")]
        if cached_day and all(fact_current(fact) for fact in dated_facts):
            from ..models.schemas import DayPlan
            restored=DayPlan.model_validate(cached_day["day"])
            if restored.feasibility.get("status") in {"feasible","unverified"} and restored.timeline and not any(gap.startswith("route_") for gap in restored.feasibility.get("unknown",[])):
                buffer_gaps=_transport_buffer_gaps(restored.timeline,request.planning_input)
                if buffer_gaps:
                    restored.feasibility["unknown"]=sorted(set(restored.feasibility.get("unknown",[]))|buffer_gaps)
                    restored.feasibility["status"]="unverified"
                plan.days[plan.days.index(day)]=restored
                continue
        day.city = city_for_day(request, day.day_index)
        # Identity creation is scoped; untouched days are not serialized differently.
        for attraction in day.attractions:
            if not attraction.activity_id:
                attraction.activity_id = f"poi:{day.day_index}:{attraction.poi_id}"
        for meal in day.meals:
            if not meal.activity_id:
                meal.activity_id = f"meal:{day.day_index}:{meal.type}:{meal.poi_id or 'unknown'}"
        if time.monotonic()-started >= limit:
            day.feasibility = {"status":"not_found", "solver_status":"TOTAL_BUDGET_EXHAUSTED", "unknown":[], "conflicts":["Resume to continue scheduling remaining days"], "omitted_activity_ids":[]}
        else:
            previous_day=next((d for d in plan.days if d.day_index==day.day_index-1),None)
            timeline, report, order = solve_day(day, request, route_planner, cache, min(2, limit-(time.monotonic()-started)),previous_day)
            day.timeline, day.feasibility = timeline, report
            if day.day_index in hotel_options:
                day.feasibility["resolution_options"]=hotel_options[day.day_index]
            if timeline and report["status"] in {"feasible","unverified"}:
                day.__pydantic_extra__["schedule_stale"] = False
            if order:
                priority = {identity:i for i,identity in enumerate(order)}
                day.attractions[:] = [a for a in day.attractions if a.activity_id in priority]
                day.attractions.sort(key=lambda a:priority[a.activity_id])
                day.meals[:] = [m for m in day.meals if m.activity_id in priority]
                day.meals.sort(key=lambda a:priority[a.activity_id])
        reports.append({"day_index":day.day_index, **day.feasibility})
        if day.timeline and day.feasibility.get("status") in {"feasible","unverified"}:
            save_checkpoint(checkpoint_stage,digest,{"day":day.model_dump(mode="json")})
        save_checkpoint("routes",digest,{"routes":[{"key":list(key),"minutes":value[0],"verified":value[1]} for key,value in cache.items() if isinstance(key,tuple)]})
    # Two bounded neighboring-day exchanges improve balance without moving any
    # fixed date, appointment or protected activity. Revision scope forbids this
    # global improvement pass even when several days were explicitly selected.
    fixed = set(request.planning_input.completed_activity_ids) | {l.get("activity_id") for l in request.planning_input.locks} | {c.get("activity_id") for c in request.planning_input.commitments if confirmed(c)}
    reserved_pois = {c.get("poi_id") for c in request.planning_input.commitments if confirmed(c)}
    def objective(days):
        return (sum(d.feasibility.get("status") in {"infeasible","not_found"} for d in days),
            sum(len(d.feasibility.get("omitted_activity_ids",[])) for d in days),
            sum(len(d.feasibility.get("unknown",[])) for d in days),
            sum(d.feasibility.get("route_minutes",10000) for d in days))
    # A resumed route stage reuses the paid matrix for the already selected
    # days. Optional cross-day exchanges would ask for new matrix edges; leave
    # that improvement to a fresh planning request rather than repeat spending.
    if not saved_routes and request.planning_input.revision_scope is None and len(allowed_days) == len(plan.days):
        for _ in range(2):
            improved = False
            for index in range(len(plan.days)-1):
                if time.monotonic()-started >= limit:
                    break
                left,right = plan.days[index:index+2]
                if left.city != right.city:
                    continue
                movable = lambda d:[a for a in d.attractions if a.optional and a.activity_id not in fixed and a.poi_id not in reserved_pois and a.name not in request.constraints.must_visit][:2]
                pairs = [(a,b) for a in movable(left) for b in movable(right)][:2]
                for a,b in pairs:
                    if time.monotonic()-started >= limit:
                        break
                    trial = [left.model_copy(deep=True),right.model_copy(deep=True)]
                    trial[0].attractions[trial[0].attractions.index(a)] = b.model_copy(deep=True)
                    trial[1].attractions[trial[1].attractions.index(b)] = a.model_copy(deep=True)
                    for candidate in trial:
                        previous_day=next((d for d in [*trial,*plan.days] if d.day_index==candidate.day_index-1),None)
                        timeline,report,order = solve_day(candidate,request,route_planner,cache,min(.3,max(.01,limit-(time.monotonic()-started))),previous_day)
                        candidate.timeline,candidate.feasibility = timeline,report
                        if timeline and report["status"] in {"feasible","unverified"}:
                            candidate.__pydantic_extra__["schedule_stale"] = False
                        if order:
                            rank = {identity:i for i,identity in enumerate(order)}
                            candidate.attractions[:] = sorted([item for item in candidate.attractions if item.activity_id in rank], key=lambda item:rank[item.activity_id])
                            candidate.meals[:] = sorted([item for item in candidate.meals if item.activity_id in rank], key=lambda item:rank[item.activity_id])
                    if objective(trial) < objective([left,right]):
                        plan.days[index:index+2] = trial
                        left,right = trial
                        improved = True
                        break
            if not improved:
                break
    reports = [{"day_index":d.day_index,**d.feasibility} for d in plan.days if d.day_index in allowed_days]
    plan.planning_input = request.planning_input.model_copy(deep=True)
    if route_evidence is not None:
        route_evidence.update({key:value for key,value in cache.items() if isinstance(key,tuple)})
    return reports
