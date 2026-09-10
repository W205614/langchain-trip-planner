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


def test_benchmark_gate():
    report = run()
    assert report["current_passed"] == report["total"]
    assert report["current_passed"] > report["baseline_passed"]


def test_saved_constraints_survive_reload_and_cannot_be_replaced_by_edit():
    from app.db.models import TripRecord
    from app.services.history_service import trip_record_to_request
    request, plan, routes = fixture({"days": [["A", "B"]], "constraints": {"must_visit": ["B"]}})
    finalize_plan(plan, request, routes)
    record = TripRecord(city=request.city, start_date=request.start_date, end_date=request.end_date,
        travel_days=1, transportation=request.transportation, accommodation=request.accommodation,
        preferences="[]", free_text_input="", plan_json=plan.model_dump_json())
    restored = trip_record_to_request(record)
    plan.constraints = PlanningConstraints()
    plan.days[0].attractions.pop()
    report = finalize_plan(plan, restored, repair=False)
    assert not report["rules_passed"]
    assert plan.constraints.must_visit == ["B"]
