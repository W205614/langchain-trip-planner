"""Historical sample regressions across revision execution and fact reporting."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.services.planning_constraints import finalize_plan
from test_time_planner import Routes, scenario


def test_explicit_reduction_is_enforced_after_revision_protection(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.agent_api import main
    from app.agents import trip_planner_agent
    from app.services import durable_execution as durable

    request, original = scenario()
    request.constraints.must_visit = ["A"]
    original.days[0].attractions[1].optional = True
    untouched = original.model_dump(mode="json")
    planner = SimpleNamespace(
        revise_trip_day=lambda _request, plan, *_args, **_kwargs: plan,
        amap_service=Routes(),
    )
    monkeypatch.setattr(trip_planner_agent, "get_trip_planner_agent", lambda: planner)
    monkeypatch.setattr(durable, "_call", lambda method, path, payload=None: {"checkpoints": []})
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setenv("INTERNAL_SERVICE_KEY", "0123456789abcdefghijklmnopqrstuv")
    body = {
        "protocol_version": 2, "task_id": str(uuid4()), "execution_id": str(uuid4()),
        "request_id": "remove-one-attraction-regression", "user_id": 1,
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
        "request": request.model_dump(mode="json"), "original_plan": untouched,
        "original_version": 1, "day_index": 0, "instruction": "当天轻松一些，减少一个景点",
    }
    with TestClient(main.app) as client:
        response = client.post("/internal/v2/executions", json=body,
            headers={"X-Service-Key": "0123456789abcdefghijklmnopqrstuv"})
    assert response.status_code == 200 and "event: result" in response.text
    result = next(json.loads(event.split("data: ", 1)[1])
        for event in response.text.split("\n\n") if event.startswith("event: result"))
    assert [a["poi_id"] for a in result["plan"]["days"][0]["attractions"]] == ["A"]
    assert any(r["reason"] == "user_requested_attraction_reduction" for r in result["quality"]["repairs"])
    assert original.model_dump(mode="json") == untouched


def test_solver_route_distances_and_current_openings_reach_quality_without_new_lookups():
    request, plan = scenario()
    routes = Routes()
    quality = finalize_plan(plan, request, routes, enhanced=True)
    check = quality["day_checks"][0]
    assert check["route_distance_km"] == 1
    assert check["inter_stop_walking_km"] == 1
    assert "route_distance_and_walking_unverified" not in quality["data_gaps"]
    assert "opening_hours_travel_date_unverified" not in quality["data_gaps"]
    assert "opening_hours_unavailable" not in quality["data_gaps"]
    assert "hotel_and_meal_routes_unverified" not in quality["data_gaps"]
    assert "in_attraction_walking_unknown" in quality["data_gaps"]
    assert routes.calls == 6  # Directed hotel/A/B matrix; quality sends nothing extra.


def test_route_metrics_survive_checkpoint_resume_without_renewing_observation(monkeypatch):
    from app.services import durable_execution as durable

    request, plan = scenario()
    original = deepcopy(plan)
    saved = {}
    monkeypatch.setattr(durable, "read_checkpoint", lambda stage, digest: saved.get((stage, digest)))
    monkeypatch.setattr(durable, "save_checkpoint", lambda stage, digest, payload, **_: saved.__setitem__((stage, digest), deepcopy(payload)))
    routes = Routes()
    first = finalize_plan(plan, request, routes, enhanced=True)
    sends = routes.calls
    second = finalize_plan(original, request, routes, enhanced=True)
    assert routes.calls == sends
    assert first["day_checks"][0]["route_distance_km"] == second["day_checks"][0]["route_distance_km"] == 1
    assert plan.days[0].route_fact_expires_at == original.days[0].route_fact_expires_at


@pytest.mark.parametrize("instruction,expected", [
    ("减少一个景点", 1), ("第2天少安排2个景点", 2), ("去掉两处景点", 2),
    ("减少步行", None), ("不要减少一个景点", None), ("不用再去掉1个景点", None),
    ("至少一个景点", None), ("最少安排两个景点", None), ("不能减少1个景点", None),
])
def test_only_explicit_affirmative_attraction_counts_are_constraints(instruction, expected):
    from app.services.assistant_intent import attraction_reduction, classify_assistant_intent
    assert attraction_reduction(instruction) == expected
    if expected is not None:
        assert classify_assistant_intent(instruction, 2)["intent"] == "revise_day"


@pytest.mark.parametrize("protection", ["must_visit", "lock", "reservation", "mandatory"])
def test_quantity_request_cannot_delete_protected_activities(protection):
    from app.core.exceptions import BizException
    from app.services.planning_constraints import enforce_revision_reduction
    from app.services.time_planner import protect_revision
    request, original = scenario()
    for attraction in original.days[0].attractions:
        attraction.optional = protection != "mandatory"
    if protection == "must_visit":
        request.constraints.must_visit = ["A", "B"]
    elif protection == "lock":
        request.planning_input.locks = [{"activity_id": a.activity_id, "mode": "content"} for a in original.days[0].attractions]
    elif protection == "reservation":
        request.planning_input.commitments = [{"id": a.poi_id, "day_index": 0, "poi_id": a.poi_id,
            "status": "confirmed"} for a in original.days[0].attractions]
    changed = deepcopy(original)
    allowed = protect_revision(changed, original, request, 0)
    with pytest.raises(BizException) as error:
        enforce_revision_reduction(changed, original, request, "减少一个景点", allowed)
    assert error.value.code == "REVISION_CONSTRAINT_CONFLICT"
    assert len(changed.days[0].attractions) == 2


def test_reduction_changes_only_mutable_activities_in_selected_day():
    from app.models.planning import RevisionScope
    from app.services.planning_constraints import enforce_revision_reduction
    from app.services.time_planner import protect_revision
    request, original = scenario([["A", "B"], ["C"]])
    for attraction in original.days[0].attractions:
        attraction.optional = True
    request.planning_input.revision_scope = RevisionScope(day_indices=[0],
        activity_ids=[original.days[0].attractions[0].activity_id])
    changed = deepcopy(original)
    allowed = protect_revision(changed, original, request, 0)
    enforce_revision_reduction(changed, original, request, "减少一个景点", allowed)
    assert [a.poi_id for a in changed.days[0].attractions] == ["B"]
    assert changed.days[1].model_dump(mode="json") == original.days[1].model_dump(mode="json")


def test_expired_route_evidence_on_untouched_day_remains_unverified():
    request, plan = scenario([["A", "B"], ["C"]])
    finalize_plan(plan, request, Routes(), enhanced=True)
    plan.days[0].__pydantic_extra__["route_fact_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    untouched = plan.days[0].model_dump(mode="json")
    quality = finalize_plan(plan, request, Routes(), allowed_days={1}, enhanced=True)
    assert plan.days[0].model_dump(mode="json") == untouched
    assert quality["feasibility_status"] == "unverified"
    assert "route_fact_expired_or_unobserved" in quality["schedule_checks"][0]["unknown"]
    assert "hotel_and_meal_routes_unverified" in quality["data_gaps"]


def test_stale_opening_declarations_are_not_cleared_as_verified():
    request, plan = scenario()
    request.planning_input.opening_windows[0]["observed_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    assert "opening_hours_unavailable" in quality["data_gaps"]
    assert any(gap.startswith("opening_unknown_or_stale:") for gap in quality["data_gaps"])
    assert quality["facts_complete"] is False
