"""Frozen targeted scheduling comparison. Synthetic inputs; never calls a provider.

Use --split development for iteration. Run holdout only after production is frozen.
The independent validator and baselines below do not call production quality logic.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
from itertools import permutations
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
DATASET = ROOT / "backend/evals/improvement_cases_20261025.json"
SOURCES = ["backend/app/services/time_planner.py", "backend/app/services/planning_constraints.py",
           "backend/app/services/result_policy.py"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def minute(value):
    return int(value[:2]) * 60 + int(value[3:])


def local_minute(value):
    value = datetime.fromisoformat(value)
    return value.hour * 60 + value.minute


def location_key(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return round(value["longitude"], 6), round(value["latitude"], 6)


class FrozenRoutes:
    """Complete local directed fixture, no HTTP/SDK/client is instantiated."""
    def __init__(self, rules):
        self.rules, self.calls = rules, 0

    def minutes(self, left, right):
        if location_key(left) == location_key(right) and left is not None:
            return 0, True
        for row in self.rules["overrides"]:
            if (("left" not in row or location_key(row["left"]) == location_key(left))
                    and ("right" not in row or location_key(row["right"]) == location_key(right))):
                return (30, False) if row.get("missing") else (row["minutes"], True)
        return (30, False) if self.rules["missing"] else (self.rules["default_minutes"], True)

    def plan_route_by_locations(self, left, right, **kwargs):
        self.calls += 1
        minutes, known = self.minutes(left, right)
        if not known:
            return {}
        return {"duration": minutes * 60, "distance": minutes * 100,
                "walking_distance": 0, "route_type": kwargs.get("route_type", "transit")}


def fact_valid(fact, clock):
    """Independent implementation of the frozen fixture's evidence validity."""
    if not fact.get("confirmed") or not fact.get("source"):
        return False
    observed = datetime.fromisoformat(fact["observed_at"])
    expiry = min(observed + timedelta(days=1), datetime.fromisoformat(fact["expires_at"]))
    return observed <= clock < expiry


def nodes_for(case, day, clock):
    inp = case["request"]["planning_input"]
    bounds = next((w for w in inp.get("day_windows", []) if w["day_index"] == day["day_index"]), {})
    low, high = minute(bounds.get("start", inp["day_start"])), minute(bounds.get("end", inp["day_end"]))
    high = min(high, low + case["request"]["constraints"]["daily_minutes"])
    nodes = []
    for kind, values in (("attraction", day["attractions"]), ("meal", day["meals"])):
        for value in values:
            duration = value.get("visit_duration", {"breakfast": 30, "lunch": 60, "dinner": 60}.get(value.get("type"), 30))
            windows = [(low, high)]
            facts = [f for f in inp["opening_windows"] if f["poi_id"] == value["poi_id"] and f["date"] == day["date"] and fact_valid(f, clock)]
            if len(facts) == 1:
                windows = [] if facts[0].get("closed") else [(max(low, minute(w["start"])), min(high, minute(w["end"]))) for w in facts[0]["windows"]]
            if kind == "meal":
                defaults = {"breakfast": ("08:00", "10:00"), "lunch": ("11:30", "14:00"), "dinner": ("17:30", "21:00")}
                start, end = defaults.get(value["type"], ("08:00", "21:00"))
                explicit = next((w for w in inp.get("meal_windows", []) if w["type"] == value["type"]), {})
                start, end = explicit.get("start", start), explicit.get("end", end)
                duration = explicit.get("duration_minutes", duration)
                windows = [(max(a, minute(start)), min(b, minute(end))) for a, b in windows]
            fixed = next((l for l in inp.get("locks", []) if l["activity_id"] == value["activity_id"] and l.get("mode") == "time"), None)
            if value["activity_id"] in inp.get("completed_activity_ids", []):
                fixed = next((r for r in day["timeline"] if r["activity_id"] == value["activity_id"]), fixed)
            if fixed:
                start, end = local_minute(fixed["start_at"]), local_minute(fixed["end_at"])
                windows = [(start, end)] if any(a <= start and end <= b for a, b in windows) else []
            nodes.append({**value, "kind": kind, "duration": duration, "windows": windows,
                          "fixed": fixed, "optional": value.get("optional", kind == "meal")})
    return nodes, low, high


def baseline(case, method, clock):
    if not case["baseline_applicable"]:
        return {"applicable": False, "reason": case["baseline_not_applicable_reason"]}
    route = FrozenRoutes(case["routes"])
    from app.models.schemas import TripPlan
    result = TripPlan.model_validate(case["plan"]).model_dump(mode="json")
    for day in result["days"]:
        if day["day_index"] not in case["allowed_days"]:
            continue
        original = case["plan"]["days"][day["day_index"]]
        nodes, cursor, high = nodes_for(case, original, clock)
        hotel = original["hotel"]["location"]
        previous, rows = hotel, []
        def start_for(node, at):
            if node["fixed"]:
                fixed_start = local_minute(node["fixed"]["start_at"])
                return fixed_start if fixed_start >= at and node["windows"] else None
            return next((max(a, at) for a, b in node["windows"] if max(a, at) + node["duration"] <= b), None)
        if method == "travel_order":
            orders = permutations(nodes)
            def cost(order):
                points = [hotel, *[n["location"] for n in order], hotel]
                return sum(route.minutes(a, b)[0] for a, b in zip(points, points[1:]))
            ordered = list(min(orders, key=cost))
        else:
            ordered = list(nodes)
        omitted = []
        while ordered:
            if method == "time_window_greedy":
                def urgency(node):
                    at = cursor + route.minutes(previous, node["location"])[0]
                    start = start_for(node, at)
                    return (start is None, min((b for a, b in node["windows"]), default=9999), at, node["activity_id"])
                ordered.sort(key=urgency)
            node = ordered.pop(0)
            travel, known = route.minutes(previous, node["location"])
            start = start_for(node, cursor + travel)
            if start is None and node["optional"]:
                omitted.append(node["activity_id"])
                continue
            # Preserve an infeasible attempt for the independent checker; do not
            # silently drop a mandatory visit to make a baseline look feasible.
            start = cursor + travel if start is None else start
            instant = lambda m: (datetime.fromisoformat(day["date"] + "T00:00:00+08:00") + timedelta(minutes=m)).isoformat()
            if travel:
                rows.append(dict(activity_id="baseline-route:" + node["activity_id"], kind="travel", start_at=instant(cursor), end_at=instant(cursor + travel), status="confirmed" if known else "estimated", notes=[] if known else ["unknown route: 30 minute allowance"]))
            rows.append(dict(activity_id=node["activity_id"], title=node["name"], kind=node["kind"], start_at=instant(start), end_at=instant(start + node["duration"])))
            cursor, previous = start + node["duration"], node["location"]
        home, known = route.minutes(previous, hotel)
        if home:
            rows.append(dict(activity_id="hotel:return", kind="travel", start_at=instant(cursor), end_at=instant(cursor + home), status="confirmed" if known else "estimated", notes=[] if known else ["unknown route: 30 minute allowance"]))
        day["timeline"] = rows
        day["feasibility"] = {"omitted_activity_ids": omitted}
    check = independent_check(case, result, {}, clock, expected=False)
    travel_minutes = sum((datetime.fromisoformat(r["end_at"]) - datetime.fromisoformat(r["start_at"])).total_seconds() / 60
                         for d in result["days"] for r in d["timeline"] if r["kind"] == "travel")
    return {"applicable": True, "checks": check, "travel_minutes": travel_minutes,
            "scheduled_attractions": sum(r["kind"] == "attraction" for d in result["days"] for r in d["timeline"]),
            "timeline": [d["timeline"] for d in result["days"]],
            "scope": "Same candidates, directed route fixture, day bounds and opening facts. No intercity or prior-state aggregation model."}


def independent_check(case, result, quality, clock, expected=True):
    checks = []
    def add(name, ok, detail=None):
        checks.append({"name": name, "passed": bool(ok), **({"detail": detail} if detail else {})})
    exp = case["expected"]
    target = case["allowed_days"][-1]
    if expected:
        if "overall_status" in exp:
            add("overall_status_matches_frozen_semantics", quality.get("feasibility_status") == exp["overall_status"], {"expected": exp["overall_status"], "actual": quality.get("feasibility_status")})
        if "outcome" in exp:
            add("outcome_matches_frozen_semantics", quality.get("outcome") == exp["outcome"], {"expected": exp["outcome"], "actual": quality.get("outcome")})
        if "day_status" in exp:
            indices = case["allowed_days"] if exp.get("all_days_checked") else [target]
            for index in indices:
                actual = result["days"][index]["feasibility"].get("status")
                add(f"day_{index}_status_matches_frozen_semantics", actual == exp["day_status"], {"expected": exp["day_status"], "actual": actual})
        if exp.get("empty_timeline"):
            add("proven_conflict_has_no_executable_timeline", not result["days"][target]["timeline"])
    for index, day in enumerate(result["days"]):
        original = case["plan"]["days"][index]
        if index not in case["allowed_days"]:
            # Compare normalized model defaults on both sides; never normalize
            # only the output. Parent integration tests protect raw sparse JSON.
            from app.models.schemas import DayPlan
            add(f"day_{index}_outside_scope_unchanged", canonical(day) == canonical(DayPlan.model_validate(original).model_dump(mode="json")))
            continue
        nodes, low, high = nodes_for(case, original, clock)
        rows = day["timeline"]
        if not rows:
            if not exp.get("empty_timeline"):
                add(f"day_{index}_required_timeline_present", False)
            continue
        intervals = sorted((datetime.fromisoformat(r["start_at"]), datetime.fromisoformat(r["end_at"])) for r in rows)
        add(f"day_{index}_positive_nonoverlapping", all(a < b for a, b in intervals) and all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:])))
        add(f"day_{index}_daily_hard_bounds", all(low <= local_minute(r["start_at"]) < local_minute(r["end_at"]) <= high for r in rows))
        activity_rows = {r["activity_id"]: r for r in rows if r["kind"] in {"attraction", "meal"}}
        for node in nodes:
            row = activity_rows.get(node["activity_id"])
            if row is None:
                if not node["optional"]:
                    add("mandatory_present:" + node["activity_id"], False)
                continue
            start, end = local_minute(row["start_at"]), local_minute(row["end_at"])
            add("duration:" + node["activity_id"], end - start == node["duration"])
            add("opening_and_day_window:" + node["activity_id"], any(a <= start and end <= b for a, b in node["windows"]))
            if node["fixed"]:
                add("lock_or_completed_unchanged:" + node["activity_id"], [row["start_at"], row["end_at"]] == [node["fixed"]["start_at"], node["fixed"]["end_at"]])
        if expected and exp.get("estimated_routes_disclosed"):
            estimated = [r for r in rows if r["kind"] == "travel" and r.get("status") == "estimated"]
            # Disclosure can be on the whole-day report and the estimated leg;
            # the product does not promise a duplicate prose note on every leg.
            route_unknown = any("route" in gap or "connection" in gap for gap in day["feasibility"].get("unknown", []))
            add(f"day_{index}_missing_route_disclosed", bool(estimated) and route_unknown and any(r.get("notes") for r in estimated) and day["feasibility"].get("status") == "unverified")
        if expected and exp.get("meal_gap_disclosed"):
            for identity in exp["omitted_ids"]:
                add("omission_visible:" + identity, identity in day["feasibility"].get("omitted_activity_ids", []) and identity not in activity_rows and any(m["activity_id"] == identity and m.get("action") for m in quality.get("unmet_meals", [])) and any(identity in gap for gap in quality.get("data_gaps", [])))
    return {"passed": all(c["passed"] for c in checks), "checks": checks,
            "checked": len(checks), "failed": sum(not c["passed"] for c in checks)}


def load_production(ref):
    import app.services.time_planner as planner
    import app.services.planning_constraints as constraints
    import app.services.result_policy as policy
    hashes = {}
    for path, module in zip(SOURCES, [planner, constraints, policy]):
        source = subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT) if ref else (ROOT / path).read_bytes()
        hashes[path] = digest(source)
        if ref:
            exec(compile(source, str(ROOT / path), "exec"), module.__dict__)
    return planner, constraints, hashes


def run(args):
    raw = DATASET.read_bytes()
    data = json.loads(raw)
    freeze = json.loads((ROOT / "docs/evidence/improvement-20261025/dataset-freeze.json").read_text(encoding="utf-8"))
    if digest(raw) != freeze["sha256"]:
        raise RuntimeError("Frozen dataset hash changed; refusing evaluation")
    if args.split == "holdout" and not args.production_frozen:
        raise RuntimeError("Holdout requires explicit --production-frozen acknowledgment")
    from app.models.schemas import TripPlan, TripRequest
    from app.services import durable_execution
    planner, constraints, hashes = load_production(args.source_ref)
    fixed_clock = datetime.fromisoformat(data["clock"])
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_clock.astimezone(tz) if tz else fixed_clock.replace(tzinfo=None)
    cases = []
    # The route fixture is entirely local. Checkpoint reads/writes are disabled;
    # socket prohibition fails immediately if an accidental outbound path appears.
    with patch.object(planner, "datetime", FrozenDatetime), patch.object(durable_execution, "read_checkpoint", return_value=None), patch.object(durable_execution, "save_checkpoint", return_value=None), patch("socket.socket.connect", side_effect=AssertionError("Benchmark forbids network")), patch("socket.create_connection", side_effect=AssertionError("Benchmark forbids network")):
        for case in data["cases"]:
            if case["split"] != args.split:
                continue
            request, plan = TripRequest.model_validate(case["request"]), TripPlan.model_validate(case["plan"])
            routes = FrozenRoutes(case["routes"])
            started = time.perf_counter()
            try:
                if case["revision"]:
                    planner.protect_revision(plan, plan.model_copy(deep=True), request, case["allowed_days"][0])
                quality = constraints.finalize_plan(plan, request, routes, allowed_days=set(case["allowed_days"]), enhanced=True)
                result = plan.model_dump(mode="json")
                checks = independent_check(case, result, quality, fixed_clock)
                actual = {"checks": checks, "quality": quality, "days": result["days"], "fixture_route_lookups": routes.calls}
            except Exception as exc:
                actual = {"checks": {"passed": False, "checked": 1, "failed": 1, "checks": [{"name": "execution", "passed": False}]}, "error": f"{type(exc).__name__}: {exc}"}
            actual["elapsed_seconds"] = round(time.perf_counter() - started, 4)
            cases.append({"id": case["id"], "production": actual,
                          "baselines": {method: baseline(case, method, fixed_clock) for method in ("travel_order", "time_window_greedy")}})
    return {"schema_version": 1, "split": args.split, "source_ref": args.source_ref or "working-tree",
            "evaluator_sha256": digest(Path(__file__).read_bytes()),
            "environment": {"python": sys.version, **{package: version(package) for package in ("pydantic", "ortools", "numpy")}},
            "source_sha": subprocess.check_output(["git", "rev-parse", args.source_ref or "HEAD"], cwd=ROOT, text=True).strip(),
            "source_fingerprints": hashes, "dataset_sha256": digest(raw), "fixed_clock": data["clock"],
            "real_provider_calls": 0, "model_calls": 0, "network_policy": "socket connects forbidden",
            "case_count": len(cases), "passed": all(c["production"]["checks"]["passed"] for c in cases),
            "production_cases_passed": sum(c["production"]["checks"]["passed"] for c in cases), "cases": cases,
            "limits": ["Twelve targeted synthetic cases, not independent real-world itinerary accuracy.",
                       "Baselines apply only to local scheduling; prior-state aggregation and intercity access are explicitly not applicable.",
                       "Travel-order baseline chooses shortest directed order, then attempts earliest legal windows; time-window greedy chooses earliest closing feasible candidate.",
                       "Provider latency, model grounding, Java authorization and browser experience are not evaluated here.",
                       "Independent checker reports named constraints, not a general completeness or factual-accuracy score."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "holdout"], required=True)
    parser.add_argument("--source-ref", help="Load the three scheduling modules from this Git revision into memory")
    parser.add_argument("--production-frozen", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("split", "case_count", "production_cases_passed", "passed", "dataset_sha256")}))
    sys.exit(0 if report["passed"] else 1)
