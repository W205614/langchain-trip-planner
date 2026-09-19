import json
import pytest
from pydantic import ValidationError
from app.evals.constraint_benchmark import CASES, fixture, outcome, run
from app.models.schemas import PlanningConstraints
from app.services.planning_constraints import finalize_plan


@pytest.mark.parametrize("case", json.loads(CASES.read_text(encoding="utf-8"))["cases"], ids=lambda c: c["id"])
def test_frozen_business_expectations(case):
    request, plan, routes = fixture(case)
    quality = finalize_plan(plan, request, routes)
    assert all(outcome(case, plan, quality).values())
    assert quality["feasibility_status"] != "verified"


def test_conflicting_names_rejected():
    with pytest.raises(ValidationError):
        PlanningConstraints(must_visit=[" A "], avoid=["a"])


def test_manual_edit_preserves_user_choices_but_flags_violations():
    case = {"days": [["A", "B"]], "constraints": {"avoid": ["B"]}}
    request, plan, routes = fixture(case)
    report = finalize_plan(plan, request, repair=False)
    assert [a.name for a in plan.days[0].attractions] == ["A", "B"]
    assert not report["passed"]
    assert report["day_checks"][0]["route_minutes"] is None


def test_invalid_route_does_not_become_verified_zero():
    request, plan, routes = fixture({"days": [["A", "B"]]})
    routes.plan_route_by_locations = lambda *a, **kw: {"duration": float("nan"), "distance": -1}
    report = finalize_plan(plan, request, routes)
    assert report["route_checked"] is False


def test_spatial_groups_reduce_fixture_cross_city_travel():
    from app.agents.trip_planner_agent import MultiAgentTripPlanner
    from app.models.schemas import POIInfo, Location
    points = [POIInfo(id=str(i), name=str(i), type="景点", address="fixture",
        location=Location(longitude=x, latitude=39)) for i, x in enumerate([116, 116.01, 117, 117.01])]
    groups = MultiAgentTripPlanner._split_pois_for_days(points, 2)
    assert [[p.id for p in group] for group in groups] == [["0", "1"], ["2", "3"]]
    assert sum(abs(g[0].location.longitude - g[-1].location.longitude) for g in groups) < 0.03


def test_route_matrix_reorders_small_day_using_actual_directed_costs():
    request, plan, _ = fixture({"days": [["A", "B", "C"]]})

    class DirectedRoutes:
        def plan_route_by_locations(self, left, right, **_kwargs):
            names = {116.00: "A", 116.01: "B", 116.02: "C"}
            pair = (names[round(left.longitude, 2)], names[round(right.longitude, 2)])
            seconds = {("B", "C"): 60, ("C", "A"): 60}.get(pair, 6000)
            return {"duration": seconds, "distance": seconds * 10}

    report = finalize_plan(plan, request, DirectedRoutes())

    assert [item.poi_id for item in plan.days[0].attractions] == ["B", "C", "A"]
    assert any(item["reason"] == "route_matrix_reordered" for item in report["repairs"])


def test_budget_scales_per_person_costs_and_checks_user_limit():
    request, plan, routes = fixture({"days": [["A"]]})
    request.traveler_count = 2
    request.room_count = 1
    request.budget_total = 100
    plan.days[0].attractions[0].ticket_price = 10
    for meal in plan.days[0].meals:
        meal.estimated_cost = 20

    finalize_plan(plan, request, routes)

    assert plan.budget.total_attractions == 20
    assert plan.budget.total_meals == 120
    assert plan.budget.limit_total == 100
    assert plan.budget.within_limit is False


def test_no_restaurant_candidates_is_a_non_blocking_data_gap():
    request, plan, routes = fixture({"days": [["A"]]})
    plan.days[0].meals = []

    report = finalize_plan(plan, request, routes)

    assert report["passed"] is True
    assert report["outcome"] == "degraded"
    assert "meal_pois_unavailable" in report["data_gaps"]
    assert not any(issue["blocking"] for issue in report["issues"])


def test_benchmark_gate():
    report = run()
    assert report["current_passed"] == report["total"]
    assert report["current_passed"] > report["baseline_passed"]
