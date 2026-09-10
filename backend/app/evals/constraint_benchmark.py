"""Frozen scenario assertions plus baseline comparison; no credentials or network."""
import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from ..models.schemas import Attraction, DayPlan, Location, Meal, TripPlan, TripRequest
from ..services.plan_quality import evaluate_plan, normalize_day, repair_plan_routes
from ..services.planning_constraints import POLICY_VERSION, finalize_plan

CASES = Path(__file__).resolve().parents[2] / "evals/constraint_cases.json"


def fixture(case):
    end = (date(2026, 9, 11) + timedelta(days=len(case["days"]) - 1)).isoformat()
    request = TripRequest(city="合成测试城市", start_date="2026-09-11", end_date=end,
        travel_days=len(case["days"]), transportation=case.get("transportation", "步行"),
        accommodation="测试", constraints=case.get("constraints", {}))
    days = [DayPlan(date=(date(2026, 9, 11) + timedelta(days=i)).isoformat(), day_index=i,
        description="fixture", transportation=request.transportation, accommodation="测试",
        attractions=[Attraction(name=n, poi_id=n, address="fixture",
            location=Location(longitude=116 + (ord(n[0]) - 65) / 100, latitude=39),
            visit_duration=120, description="fixture") for n in names],
        meals=[Meal(type=t, name=t) for t in ("breakfast", "lunch", "dinner")])
        for i, names in enumerate(case["days"])]
    plan = TripPlan(city=request.city, start_date=request.start_date, end_date=end,
                    days=days, overall_suggestions="fixture")
    class Routes:
        def plan_route_by_locations(self, *args, **kwargs):
            if case.get("route") == "unavailable":
                return {}
            return {"duration": 1200, "distance": 2000}
    return request, plan, Routes()


def outcome(case, plan, report):
    checks = {
        "selected_pois": [[a.poi_id for a in d.attractions] for d in plan.days] == case["expected"],
        "constraint_result": report["passed"] == case["passed"],
        "facts_not_overclaimed": report.get("facts_complete") is False,
    }
    if case.get("unknown_route"):
        checks["missing_route_not_zero"] = bool(report.get("day_checks")) and report["day_checks"][0]["route_minutes"] is None
    if case.get("unknown_walking"):
        checks["missing_walk_not_zero"] = bool(report.get("day_checks")) and report["day_checks"][0]["inter_stop_walking_km"] is None
    return checks


def run():
    raw = CASES.read_bytes()
    dataset = json.loads(raw)
    rows = []
    for case in dataset["cases"]:
        request, original, routes = fixture(case)
        baseline = original.model_copy(deep=True)
        for day in baseline.days:
            normalize_day(day)
        route_report = repair_plan_routes(baseline, routes, request.transportation)
        before = evaluate_plan(baseline, request.travel_days).to_dict() | route_report
        # Old UI already disclosed missing facts; count that existing capability fairly.
        before["facts_complete"] = not bool(before.get("data_gaps"))
        current = original.model_copy(deep=True)
        after = finalize_plan(current, request, routes)
        rows.append({"id": case["id"], "baseline": outcome(case, baseline, before),
                     "current": outcome(case, current, after), "quality": after})
    return {"dataset": dataset["version"], "sha256": hashlib.sha256(raw).hexdigest(),
        "policy": POLICY_VERSION, "boundary": dataset["boundary"], "cases": rows,
        "baseline_passed": sum(all(r["baseline"].values()) for r in rows),
        "current_passed": sum(all(r["current"].values()) for r in rows), "total": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False))
    raise SystemExit(0 if report["current_passed"] == report["total"] else 1)
