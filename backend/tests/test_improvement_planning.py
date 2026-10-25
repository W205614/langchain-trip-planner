"""Regressions for independently proven conflicts and whole-trip scoped quality."""
from copy import deepcopy

import pytest

from test_time_planner import Routes, scenario
from app.services.planning_constraints import finalize_plan
from app.services.time_planner import solve_day


@pytest.mark.parametrize("retained_status", ["infeasible", "not_found", "unverified"])
def test_scoped_revision_keeps_untouched_day_status_and_evidence(retained_status):
    request, plan = scenario([["A"], ["B"]])
    plan.days[0].feasibility = {
        "status": retained_status, "solver_status": "SAVED",
        "unknown": ["opening_unknown_or_stale:poi:0:A"],
        "conflicts": ["no_time_window:poi:0:A"] if retained_status == "infeasible" else [],
        "omitted_activity_ids": [],
    }
    before = deepcopy(plan.days[0].model_dump(mode="json"))
    report = finalize_plan(plan, request, Routes(), allowed_days={1}, enhanced=True)
    assert plan.days[0].model_dump(mode="json") == before
    assert report["feasibility_status"] == retained_status
    assert len(report["schedule_checks"]) == 2
    assert "opening_unknown_or_stale:poi:0:A" in report["data_gaps"]
    assert report["facts_complete"] is False
    if retained_status in {"infeasible", "not_found"}:
        assert report["outcome"] == "draft"


@pytest.mark.parametrize("conflict", ["closed", "lock", "appointment", "arrival"])
def test_proven_window_conflict_is_not_hidden_by_an_unrelated_unknown(conflict):
    request, plan = scenario()
    day = plan.days[0]
    # The second attraction's unknown opening does not invalidate the first
    # attraction's independently confirmed contradiction.
    request.planning_input.opening_windows.pop()
    identity = day.attractions[0].activity_id
    if conflict == "closed":
        request.planning_input.opening_windows[0]["closed"] = True
    elif conflict == "lock":
        request.planning_input.locks = [{"activity_id": identity, "mode": "time",
            "start_at": day.date + "T21:00:00+08:00", "end_at": day.date + "T22:00:00+08:00"}]
    elif conflict == "appointment":
        request.planning_input.commitments = [{"id": "fixed", "activity_id": identity,
            "day_index": 0, "status": "confirmed", "start_at": day.date + "T21:00:00+08:00",
            "end_at": day.date + "T22:00:00+08:00"}]
    else:
        from datetime import datetime
        request.planning_input.arrival_at = datetime.fromisoformat(day.date + "T21:00:00+08:00")
    timeline, report, _ = solve_day(day, request, Routes())
    assert timeline == []
    assert report["status"] == "infeasible"
    assert any(gap.startswith("opening_unknown_or_stale:") for gap in report["unknown"])


def test_untouched_legacy_day_without_schedule_cannot_be_reported_feasible():
    request, plan = scenario([["A"], ["B"]])
    before = deepcopy(plan.days[0].model_dump(mode="json"))
    report = finalize_plan(plan, request, Routes(), allowed_days={1}, enhanced=True)
    assert plan.days[0].model_dump(mode="json") == before
    assert report["feasibility_status"] == "unverified"
    assert report["facts_complete"] is False


@pytest.mark.parametrize("old_rule", ["time-windows-v3-hotel-nights", "time-windows-v4-meal-candidates", "time-windows-v5-hotel-departure"])
def test_classification_upgrade_reuses_provider_evidence_but_not_old_schedules(old_rule):
    from app.services.durable_execution import checkpoint_compatible
    from app.services.time_planner import RULE_VERSION
    saved = {"rules": old_rule, "model": "frozen-model", "prompt": "frozen-prompt"}
    current = {**saved, "rules": RULE_VERSION}
    for stage in ("draft", "routes", "day_draft:0", "attractions:北京", "hotels:北京", "restaurants:北京", "weather:北京"):
        assert checkpoint_compatible(stage, saved, current)
    for stage in ("schedule", "schedule_day:0:hash"):
        assert not checkpoint_compatible(stage, saved, current)
    assert not checkpoint_compatible("draft", {**saved, "model": "changed"}, current)
