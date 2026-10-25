"""Bounded deterministic repair and an auditable, versioned quality report.

Only inter-attraction routes are measured. Meals and buffer are allowances,
not verified appointments. Missing upstream facts never become zero distances.
"""
from itertools import permutations
from math import isfinite

from ..models.schemas import TripPlan, TripRequest
from .plan_quality import REQUIRED_MEALS, evaluate_plan, recalculate_budget, transport_to_route_type

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


def finalize_plan(plan: TripPlan, request: TripRequest, route_planner=None, *, repair=True, allowed_days=None, enhanced=False):
    plan.departure_city = request.departure_city
    plan.traveler_count = request.traveler_count
    plan.room_count = request.room_count
    plan.constraints = request.constraints.model_copy(deep=True)
    constraints = request.constraints
    allowed_days = set(range(len(plan.days))) if allowed_days is None else set(allowed_days)
    protected_ids = set(request.planning_input.completed_activity_ids) | {lock.get("activity_id") for lock in request.planning_input.locks}
    required = {name_key(n) for n in constraints.must_visit}
    avoided = {name_key(n) for n in constraints.avoid}
    repairs, violations, day_reports = [], [], []
    gaps = {"opening_hours_unavailable", "reservation_unverified", "in_attraction_walking_unknown",
            "hotel_and_meal_routes_unverified"}
    if any(
        REQUIRED_MEALS - {meal.type for meal in day.meals}
        or any(not meal.poi_id for meal in day.meals)
        for day in plan.days
    ):
        gaps.add("meal_pois_unavailable")
    seen = set()
    route_type = transport_to_route_type(request.transportation)
    cache = {}
    day_routes = {}
    from .attraction_names import resolve_name
    attractions = [a for day in plan.days for a in day.attractions]
    for name in constraints.must_visit:
        matched = resolve_name(name, attractions, plan.city)
        if matched and any(matched is a for d in plan.days if d.day_index in allowed_days for a in d.attractions):
            matched.requested_names = list(dict.fromkeys([*matched.requested_names, name]))
    avoided_ids = {matched.poi_id for name in constraints.avoid
                   if (matched := resolve_name(name, attractions, plan.city)) is not None}
    schedules = None
    schedule_routes = {}
    if enhanced:
        from .time_planner import plan_time_windows
        schedules = plan_time_windows(plan, request, route_planner, allowed_days, schedule_routes)

    def remove(day, attraction, reason):
        day.attractions.remove(attraction)
        repairs.append({"day_index": day.day_index, "removed_poi_id": attraction.poi_id, "reason": reason})

    def route_for(left, right, city=None):
        city = city or plan.city
        key = (left.poi_id, right.poi_id, left.location.longitude, left.location.latitude,
               right.location.longitude, right.location.latitude, route_type, city)
        if key not in cache:
            if enhanced:
                evidence=schedule_routes.get((left.location.longitude,left.location.latitude,right.location.longitude,right.location.latitude,city,route_type))
                # The solver has already obtained this directed route. Its
                # persisted snapshot carries duration, not distance: do not send
                # another paid lookup merely for the legacy quality summary.
                cache[key]=(evidence[0],None,None,route_type) if evidence and evidence[1] else None
                if cache[key] is not None:
                    gaps.add("route_distance_and_walking_unverified")
                return cache[key]
            try:
                route = route_planner.plan_route_by_locations(left.location, right.location,
                    route_type=route_type, city=city) if route_planner else {}
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
        return cache[key]

    def optimize_route_order(day):
        """Use the real directed route matrix for small daily candidate sets."""
        if not repair or enhanced or day.day_index not in allowed_days or protected_ids or route_planner is None or not 3 <= len(day.attractions) <= 6:
            return
        original = list(day.attractions)
        best = None
        for order in permutations(original):
            routes = [route_for(left, right, day.city or plan.city) for left, right in zip(order, order[1:])]
            if any(route is None for route in routes):
                continue
            cost = (sum(route[0] for route in routes), sum(route[1] for route in routes))
            if best is None or cost < best[0]:
                best = (cost, order)
        if best is not None and [a.poi_id for a in best[1]] != [a.poi_id for a in original]:
            day.attractions[:] = best[1]
            repairs.append({"day_index": day.day_index, "reason": "route_matrix_reordered",
                            "route_minutes": round(best[0][0], 1)})

    def measure(day):
        legs = []
        day_routes[day.day_index] = legs
        minutes, walking, distance, complete = 0.0, 0.0, 0.0, True
        for left, right in zip(day.attractions, day.attractions[1:]):
            route = route_for(left, right, day.city or plan.city)
            legs.append({"from": left.name, "to": right.name, "route_type": route[3] if route else route_type,
                         "minutes": round(route[0], 1) if route else None,
                         "distance_km": round(route[1] / 1000, 2) if route and route[1] is not None else None,
                         "walking_km": round(route[2] / 1000, 2) if route and route[2] is not None else None})
            if route is None:
                complete = False
                gaps.add("route_duration_unavailable_fallback_to_straight_line")
                continue
            minutes += route[0]
            distance = None if route[1] is None else distance+route[1] if distance is not None else None
            if route[2] is None:
                walking = None
            elif walking is not None:
                walking += route[2]
        if not complete:
            return None, None, None
        return minutes, None if walking is None else walking / 1000, None if distance is None else distance / 1000

    for day in plan.days:
        day_repair = repair and day.day_index in allowed_days and not enhanced
        for attraction in list(day.attractions):
            reason = "excluded_attraction" if name_key(attraction.name) in avoided or attraction.poi_id in avoided_ids else (
                "duplicate_poi" if attraction.poi_id in seen else None)
            if reason and day_repair and attraction.activity_id not in protected_ids:
                remove(day, attraction, reason)
            else:
                if reason:
                    violations.append(f"第{day.day_index + 1}天：{reason} ({attraction.name})")
                seen.add(attraction.poi_id)

        optimize_route_order(day)

        while True:
            route_minutes, walking, distance = measure(day)
            visit = sum(max(0, a.visit_duration) for a in day.attractions)
            known_minutes = visit + MEAL_MINUTES + BUFFER_MINUTES
            total = None if route_minutes is None else known_minutes + route_minutes
            over_time = not enhanced and (total if total is not None else known_minutes) > constraints.daily_minutes
            over_walk = (walking is not None and constraints.max_inter_stop_walking_km is not None
                         and walking > constraints.max_inter_stop_walking_km)
            over_route = not enhanced and route_minutes is not None and route_minutes > 120
            removable = [a for a in day.attractions if not (
                {name_key(a.name), *(name_key(n) for n in a.requested_names)} & required) and a.activity_id not in protected_ids]
            if day_repair and (over_time or over_walk or over_route) and len(day.attractions) > 1 and removable:
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
    def total_if_known(field, decimals=0):
        values=[day[field] for day in day_reports]
        return round(sum(values),decimals) if values and all(value is not None for value in values) else None
    report["warnings"] += violations
    report.update(policy_version=POLICY_VERSION, passed=not report["warnings"],
        rules_passed=not report["warnings"], facts_complete=False,
        feasibility_status="violated" if report["warnings"] else "needs_verification",
        score=max(0, 100 - 15 * len(report["warnings"])), repairs=repairs,
        data_gaps=sorted(gaps), day_checks=day_reports,
        route_checked=all(d["route_minutes"] is not None for d in day_reports),
        actual_route_minutes=total_if_known("route_minutes"),
        actual_route_distance_km=total_if_known("route_distance_km",2))
    recalculate_budget(plan, estimate_missing=True, budget_limit=request.budget_total)
    if enhanced:
        from .time_planner import RULE_VERSION, confirmed
        # The solver deliberately touches only allowed_days. Whole-trip quality
        # must also retain the evidence of every untouched day without rewriting
        # its persisted JSON. Legacy/stale schedules cannot imply feasibility.
        schedules = []
        for day in plan.days:
            saved = dict(day.feasibility)
            if saved.get("status") not in {"feasible", "unverified", "not_found", "infeasible"} or getattr(day, "schedule_stale", False):
                saved["status"] = "unverified"
                saved["unknown"] = sorted(set(saved.get("unknown", [])) | {"schedule_unknown_or_stale"})
            for field in ("unknown", "conflicts", "omitted_activity_ids"):
                saved.setdefault(field, [])
            schedules.append({**saved, "day_index": day.day_index})
        report["schedule_checks"] = schedules
        report["unmet_meals"]=[{"day_index":day.day_index,**meal} for day in plan.days for meal in day.feasibility.get("unmet_meals",[])]
        report["data_gaps"]=sorted(set(report["data_gaps"])|{f"meal_candidate_unassigned:{meal['type']}:{meal['activity_id']}" for meal in report["unmet_meals"]})
        report["time_policy_version"] = RULE_VERSION
        statuses = {s["status"] for s in schedules}
        report["feasibility_status"] = next((s for s in ("infeasible", "not_found", "unverified") if s in statuses), "feasible")
        report["facts_complete"] = bool(schedules) and all(not s["unknown"] for s in schedules)
        report["data_gaps"] = sorted(set(report["data_gaps"]) | {gap for s in schedules for gap in s["unknown"]})
        report["facts_complete"] = report["facts_complete"] and not report["data_gaps"]
        report["fact_status"] = "conflict" if any("conflict" in gap for gap in report["data_gaps"]) else "complete" if report["facts_complete"] else "unknown"
        for schedule in schedules:
            repairs.extend({"day_index":schedule["day_index"],"removed_activity_id":identity,"reason":"optional_activity_omitted_by_time_constraints"} for identity in schedule["omitted_activity_ids"])
            for conflict in schedule["conflicts"]:
                report["warnings"].append(f"第{schedule['day_index']+1}天：{conflict}")
            if schedule["status"] in {"infeasible", "not_found"} and not schedule["conflicts"]:
                reason = "当前候选的时间约束存在冲突" if schedule["status"] == "infeasible" else "计算预算内尚未找到可行安排，请检查草稿后恢复或调整要求"
                report["warnings"].append(f"第{schedule['day_index']+1}天：{reason}")
        report["passed"] = report["rules_passed"] = not report["warnings"]
        if report["warnings"] and report["feasibility_status"] == "feasible":
            report["feasibility_status"] = "not_found"
        confirmed_amount = sum(int(c.get("amount_cents") or 0) for c in request.planning_input.commitments if confirmed(c))
        unknown_costs = [str(c.get("id", "unknown")) for c in request.planning_input.commitments if confirmed(c) and c.get("amount_cents") is None]
        estimates = {a.activity_id:a.ticket_price*100*request.traveler_count for day in plan.days for a in day.attractions}
        estimates.update({a.activity_id:a.estimated_cost*100*request.traveler_count for day in plan.days for a in day.meals})
        replaced = sum(estimates.get(c.get("activity_id"),0) for c in request.planning_input.commitments if c.get("amount_cents") is not None and confirmed(c))
        estimated = max(0,(plan.budget.total*100 if plan.budget else 0)-replaced)
        report["cost_summary"] = {"confirmed_cents":confirmed_amount, "estimated_cents":estimated, "known_total_cents":confirmed_amount+estimated,
            "unknown_items":list(dict.fromkeys([*(plan.budget.unknown_items if plan.budget else []), *unknown_costs])), "limit_cents":request.planning_input.budget_cents}
        if request.planning_input.budget_mode == "confirmed" and estimated:
            report["cost_summary"]["unknown_items"].append("estimated_costs_not_confirmed")
        limit = request.planning_input.budget_cents
        report["cost_summary"]["within_limit"] = False if limit is not None and confirmed_amount+estimated > limit else (
            None if report["cost_summary"]["unknown_items"] or limit is None else True)
        if report["cost_summary"]["within_limit"] is None and plan.budget:
            plan.budget.within_limit = None
        report["score"] = max(0,100-15*len(report["warnings"]))
    from .result_policy import classify
    return classify(plan, request, report)
