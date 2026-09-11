"""Bounded deterministic repair and an auditable, versioned quality report.

Only inter-attraction routes are measured. Meals and buffer are allowances,
not verified appointments. Missing upstream facts never become zero distances.
"""
from math import isfinite

from ..models.schemas import TripPlan, TripRequest
from .plan_quality import evaluate_plan, recalculate_budget, transport_to_route_type

POLICY_VERSION = "constraints-v2"
MEAL_MINUTES = 90
BUFFER_MINUTES = 30


def name_key(value):
    return value.strip().casefold()


def refresh_saved_quality(plan, request, previous):
    """Upgrade name/display checks using saved route evidence; never rewrite a user's trip."""
    route_evidence = {}
    checks = {d["day_index"]: d for d in previous.get("day_checks", [])}
    for day in plan.days:
        legs = checks.get(day.day_index, {}).get("routes", [])
        for (left, right), leg in zip(zip(day.attractions, day.attractions[1:]), legs):
            if leg.get("from") != left.name or leg.get("to") != right.name:
                continue
            if leg.get("minutes") is None or leg.get("distance_km") is None:
                continue
            key = (left.location.longitude, left.location.latitude, right.location.longitude, right.location.latitude)
            route_evidence[key] = {"duration": leg["minutes"] * 60, "distance": leg["distance_km"] * 1000,
                "walking_distance": None if leg.get("walking_km") is None else leg["walking_km"] * 1000,
                "route_type": leg.get("route_type", transport_to_route_type(request.transportation))}

    class SavedRoutes:
        def plan_route_by_locations(self, left, right, **kwargs):
            return route_evidence.get((left.longitude, left.latitude, right.longitude, right.latitude), {})

    report = finalize_plan(plan, request, SavedRoutes(), repair=False)
    report["repairs"] = previous.get("repairs", [])
    if "usage" in previous:
        report["usage"] = previous["usage"]
    report["route_evidence_source"] = "saved_query"
    return report


def finalize_plan(plan: TripPlan, request: TripRequest, route_planner=None, *, repair=True):
    plan.constraints = request.constraints.model_copy(deep=True)
    constraints = request.constraints
    required = {name_key(n) for n in constraints.must_visit}
    avoided = {name_key(n) for n in constraints.avoid}
    repairs, violations, day_reports = [], [], []
    gaps = {"opening_hours_unavailable", "reservation_unverified", "in_attraction_walking_unknown",
            "hotel_and_meal_routes_unverified"}
    seen = set()
    route_type = transport_to_route_type(request.transportation)
    cache = {}
    day_routes = {}
    from .attraction_names import resolve_name
    attractions = [a for day in plan.days for a in day.attractions]
    for name in constraints.must_visit:
        matched = resolve_name(name, attractions, plan.city)
        if matched:
            matched.requested_names = list(dict.fromkeys([*matched.requested_names, name]))
    avoided_ids = {matched.poi_id for name in constraints.avoid
                   if (matched := resolve_name(name, attractions, plan.city)) is not None}

    def remove(day, attraction, reason):
        day.attractions.remove(attraction)
        repairs.append({"day_index": day.day_index, "removed_poi_id": attraction.poi_id, "reason": reason})

    def measure(day):
        legs = []
        day_routes[day.day_index] = legs
        minutes, walking, distance, complete = 0.0, 0.0, 0.0, True
        for left, right in zip(day.attractions, day.attractions[1:]):
            key = (left.poi_id, right.poi_id, left.location.longitude, left.location.latitude,
                   right.location.longitude, right.location.latitude, route_type)
            if key not in cache:
                try:
                    route = route_planner.plan_route_by_locations(left.location, right.location,
                        route_type=route_type, city=plan.city) if route_planner else {}
                    duration, meters = float(route["duration"]), float(route["distance"])
                    if not all(isfinite(v) and v >= 0 for v in (duration, meters)):
                        raise ValueError("Invalid route quantities")
                    walk = meters if route_type == "walking" else route.get("walking_distance")
                    if walk is not None:
                        walk = float(walk)
                        if not isfinite(walk) or walk < 0:
                            raise ValueError("Invalid walking distance")
                    cache[key] = (duration / 60, meters, walk, route.get("route_type", route_type))
                except Exception:
                    cache[key] = None
            route = cache[key]
            legs.append({"from": left.name, "to": right.name, "route_type": route[3] if route else route_type,
                         "minutes": round(route[0], 1) if route else None,
                         "distance_km": round(route[1] / 1000, 2) if route else None,
                         "walking_km": round(route[2] / 1000, 2) if route and route[2] is not None else None})
            if route is None:
                complete = False
                gaps.add("route_duration_unavailable_fallback_to_straight_line")
                continue
            minutes += route[0]
            distance += route[1]
            if route[2] is None:
                walking = None
            elif walking is not None:
                walking += route[2]
        if not complete:
            return None, None, None
        return minutes, None if walking is None else walking / 1000, distance / 1000

    for day in plan.days:
        for attraction in list(day.attractions):
            reason = "excluded_attraction" if name_key(attraction.name) in avoided or attraction.poi_id in avoided_ids else (
                "duplicate_poi" if attraction.poi_id in seen else None)
            if reason and repair:
                remove(day, attraction, reason)
            else:
                if reason:
                    violations.append(f"第{day.day_index + 1}天：{reason} ({attraction.name})")
                seen.add(attraction.poi_id)

        while True:
            route_minutes, walking, distance = measure(day)
            visit = sum(max(0, a.visit_duration) for a in day.attractions)
            known_minutes = visit + MEAL_MINUTES + BUFFER_MINUTES
            total = None if route_minutes is None else known_minutes + route_minutes
            over_time = (total if total is not None else known_minutes) > constraints.daily_minutes
            over_walk = (walking is not None and constraints.max_inter_stop_walking_km is not None
                         and walking > constraints.max_inter_stop_walking_km)
            over_route = route_minutes is not None and route_minutes > 120
            removable = [a for a in day.attractions if not (
                {name_key(a.name), *(name_key(n) for n in a.requested_names)} & required)]
            if repair and (over_time or over_walk or over_route) and len(day.attractions) > 1 and removable:
                remove(day, removable[-1], "daily_time_exceeded" if over_time else (
                    "walking_limit_exceeded" if over_walk else "route_duration_exceeded"))
                continue
            if over_time:
                violations.append(f"第{day.day_index + 1}天：安排超过每日{constraints.daily_minutes}分钟上限")
            if over_walk:
                violations.append(f"第{day.day_index + 1}天：景点间步行超过{constraints.max_inter_stop_walking_km:g}公里上限")
            if over_route:
                violations.append(f"第{day.day_index + 1}天：景点间交通超过120分钟")
            if walking is None and constraints.max_inter_stop_walking_km is not None:
                gaps.add("inter_stop_walking_unverified")
            day_reports.append({"day_index": day.day_index, "visit_minutes": visit,
                "meal_allowance_minutes": MEAL_MINUTES, "buffer_minutes": BUFFER_MINUTES,
                "route_minutes": None if route_minutes is None else round(route_minutes, 1),
                "planned_minutes": None if total is None else round(total, 1),
                "inter_stop_walking_km": walking, "route_distance_km": distance})
            day_reports[-1]["routes"] = day_routes[day.day_index]
            day_reports[-1]["walking_status"] = "not_applicable" if route_type == "driving" else (
                "unavailable" if walking is None else "available")
            break

    names = {name_key(n) for day in plan.days for a in day.attractions for n in [a.name, *a.requested_names]}
    for name in constraints.must_visit:
        if name_key(name) not in names:
            violations.append(f"未满足必去景点：{name}（当前行程中未找到唯一对应景点，可补充所在区域或具体名称）")
    if plan.city != request.city or plan.start_date != request.start_date or plan.end_date != request.end_date:
        violations.append("行程目的地或日期与原始请求不一致")
    from datetime import date, timedelta
    for index, day in enumerate(plan.days):
        if day.day_index != index or day.date != (date.fromisoformat(request.start_date) + timedelta(days=index)).isoformat():
            violations.append(f"第{index + 1}天：日期或天序号错误")
        if any(a.visit_duration <= 0 for a in day.attractions):
            violations.append(f"第{index + 1}天：游览时长必须为正数")
    report = evaluate_plan(plan, request.travel_days).to_dict()
    if all(a.opening_hours for day in plan.days for a in day.attractions):
        gaps.discard("opening_hours_unavailable")
        gaps.add("opening_hours_travel_date_unverified")
    if all(d["route_minutes"] is not None for d in day_reports):
        gaps.discard("route_duration_unavailable_fallback_to_straight_line")
    report["warnings"] += violations
    report.update(policy_version=POLICY_VERSION, passed=not report["warnings"],
        rules_passed=not report["warnings"], facts_complete=False,
        feasibility_status="violated" if report["warnings"] else "needs_verification",
        score=max(0, 100 - 15 * len(report["warnings"])), repairs=repairs,
        data_gaps=sorted(gaps), day_checks=day_reports,
        route_checked=all(d["route_minutes"] is not None for d in day_reports),
        actual_route_minutes=round(sum(d["route_minutes"] or 0 for d in day_reports)),
        actual_route_distance_km=round(sum(d["route_distance_km"] or 0 for d in day_reports), 2))
    recalculate_budget(plan, estimate_missing=True)
    return report
