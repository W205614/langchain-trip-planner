"""Quality must describe the effective travel day and the final selected activities."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.schemas import Meal
from app.services.plan_quality import evaluate_plan
from app.services.planning_constraints import finalize_plan, refresh_saved_quality
from app.services.result_policy import classify
from test_time_planner import Routes, scenario


@pytest.mark.parametrize("arrival,departure,expected", [
    ("13:00", None, {"lunch", "dinner"}),
    (None, "16:00", {"breakfast", "lunch"}),
    ("13:00", "16:00", {"lunch"}),
    ("10:00", "17:30", {"lunch"}),
])
def test_expected_meals_follow_effective_arrival_departure_bounds(arrival, departure, expected):
    from app.services.plan_quality import expected_meal_types
    request, plan = scenario([["A"]])
    if arrival:
        request.planning_input.arrival_at = datetime.fromisoformat(f"{plan.start_date}T{arrival}:00+08:00")
    if departure:
        request.planning_input.departure_at = datetime.fromisoformat(f"{plan.end_date}T{departure}:00+08:00")
    assert expected_meal_types(request, 0, plan.days[0].date) == expected


def test_explicit_meal_window_remains_required_when_outside_arrival_bounds():
    from app.services.plan_quality import expected_meal_types
    request, plan = scenario([["A"]])
    request.planning_input.arrival_at = datetime.fromisoformat(f"{plan.start_date}T13:00:00+08:00")
    request.planning_input.meal_windows = [{"type": "breakfast", "start": "08:00", "end": "10:00", "duration_minutes": 30}]
    assert "breakfast" in expected_meal_types(request, 0, plan.days[0].date)


def self_meal(day, kind):
    return {"day_index": day, "type": kind, "mode": "self_arranged", "source": "用户确认自理", "confirmed": True,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "location": {"longitude": 116.1, "latitude": 39.9}}


def test_arrival_and_departure_day_only_requires_its_actual_lunch():
    request, plan = scenario([["A"]])
    request.planning_input.arrival_at = datetime.fromisoformat(f"{plan.start_date}T13:00:00+08:00")
    request.planning_input.departure_at = datetime.fromisoformat(f"{plan.end_date}T16:00:00+08:00")
    plan.days[0].meals = [Meal(type="lunch", name="可信午餐", poi_id="lunch", location={"longitude": 116.1, "latitude": 39.9})]
    assert "meal_pois_unavailable" not in evaluate_plan(plan, 1, request=request).data_gaps
    quality = finalize_plan(plan, request, Routes(), repair=False)
    assert "meal_pois_unavailable" not in quality["data_gaps"]


def test_current_self_meal_covers_a_missing_restaurant_but_stale_fact_does_not():
    request, plan = scenario([["A"]])
    request.planning_input.arrival_at = datetime.fromisoformat(f"{plan.start_date}T13:00:00+08:00")
    request.planning_input.departure_at = datetime.fromisoformat(f"{plan.end_date}T16:00:00+08:00")
    request.planning_input.meal_resolutions = [self_meal(0, "lunch")]
    plan.days[0].meals = [Meal(type="lunch", name="待确认候选")]
    assert "meal_pois_unavailable" not in evaluate_plan(plan, 1, request=request).data_gaps
    request.planning_input.meal_resolutions[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert "meal_pois_unavailable" in evaluate_plan(plan, 1, request=request).data_gaps


def test_meal_quality_is_rechecked_after_solver_omits_a_restaurant():
    request, plan = scenario([["A"]])
    request.constraints.daily_minutes = 720
    request.planning_input.meal_resolutions = [self_meal(0, kind) for kind in ("breakfast", "dinner")]
    meal = Meal(type="lunch", name="已闭馆餐厅", poi_id="closed-lunch", location={"longitude": 116.1, "latitude": 39.9})
    plan.days[0].meals = [meal]
    request.planning_input.opening_windows.append({"poi_id": meal.poi_id, "date": plan.start_date, "closed": True,
        "windows": [], "source": "official_fixture", "confirmed": True, "observed_at": datetime.now(timezone.utc).isoformat()})
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    assert not plan.days[0].meals
    assert "meal_pois_unavailable" in quality["data_gaps"]
    assert any(item["type"] == "lunch" for item in quality["unmet_meals"])


def test_initial_draft_below_explicit_daily_target_is_nonblocking_degraded():
    request, plan = scenario([["A"]])
    request.free_text_input = "每天两个景点"
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    assert quality["attraction_targets"] == [{"day_index": 0, "target_count": 2, "actual_count": 1, "met": False}]
    issue = next(i for i in quality["issues"] if i["code"] == "ATTRACTION_TARGET_UNMET")
    assert not issue["blocking"] and "2个" in issue["reason"] and "1个" in issue["reason"]
    assert issue["action"] and quality["outcome"] == "degraded"


def test_met_daily_target_and_explicit_revision_have_distinct_quality_contracts():
    request, plan = scenario()
    request.free_text_input = "每天两个景点"
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    assert quality["attraction_targets"] == [{"day_index": 0, "target_count": 2, "actual_count": 2, "met": True}]
    assert not any(i["code"] == "ATTRACTION_TARGET_UNMET" for i in quality["issues"])
    plan.days[0].attractions.pop()
    revised = finalize_plan(plan, request, Routes(), allowed_days={0}, enhanced=True)
    assert "attraction_targets" not in revised
    assert not any(i["code"] == "ATTRACTION_TARGET_UNMET" for i in revised["issues"])


def test_readonly_refresh_retains_target_explanation_without_inventing_legacy_targets():
    request, plan = scenario([["A"]])
    request.free_text_input = "每天两个景点"
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    saved = plan.model_dump(mode="json")
    refreshed = refresh_saved_quality(plan, request, quality)
    assert plan.model_dump(mode="json") == saved
    assert refreshed["attraction_targets"] == quality["attraction_targets"]
    assert any(i["code"] == "ATTRACTION_TARGET_UNMET" for i in refreshed["issues"])
    legacy = refresh_saved_quality(plan, request, {"day_checks": quality["day_checks"]})
    assert "attraction_targets" not in legacy
    assert plan.model_dump(mode="json") == saved


@pytest.mark.parametrize("gap,phrase", [
    ("arrival_endpoint_unknown", "抵达地点"),
    ("departure_endpoint_unknown", "离开地点"),
    ("start_endpoint_unknown", "出发地点"),
])
def test_endpoint_gaps_are_explained_without_renaming_saved_markers(gap, phrase):
    request, plan = scenario([["A"]])
    quality = classify(plan, request, {"data_gaps": [gap]})
    issue = quality["issues"][0]
    assert phrase in issue["reason"] and gap not in issue["reason"]
    assert issue["action"] and not issue["blocking"]
    assert quality["data_gaps"] == [gap]
