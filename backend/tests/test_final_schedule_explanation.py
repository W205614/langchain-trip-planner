from copy import deepcopy

from app.services.planning_constraints import finalize_plan, refresh_saved_quality
from test_time_planner import Routes, scenario


def omitted_optional():
    request, plan = scenario()
    request.free_text_input = "每天两个景点"
    plan.days[0].attractions[1].optional = True
    request.planning_input.opening_windows[1]["closed"] = True
    plan.days[0].theme = "B文化之旅"
    plan.days[0].description = "游览A和B"
    plan.days[0].activities = ["参观B"]
    return request, plan


def test_optional_attraction_removal_explains_quantity_change_and_updates_summary():
    request, plan = omitted_optional()
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    assert [a.poi_id for a in plan.days[0].attractions] == ["A"]
    adjustment = quality["attraction_adjustments"][0]
    assert adjustment["day_index"] == 0
    assert (adjustment["before_count"], adjustment["after_count"]) == (2, 1)
    assert adjustment["removed_attractions"] == [{"poi_id": "B", "name": "B"}]
    issue = next(i for i in quality["issues"] if i["code"] == "ATTRACTION_COUNT_ADJUSTED")
    assert "2个" in issue["reason"] and "1个" in issue["reason"]
    assert issue["blocking"] is False
    assert quality["outcome"] == "degraded"
    assert "A" in plan.days[0].description
    assert "B" not in plan.days[0].description + plan.days[0].theme
    assert plan.days[0].activities == []


def test_query_keeps_adjustment_explanation_without_rewriting_saved_content():
    request, plan = omitted_optional()
    quality = finalize_plan(plan, request, Routes(), enhanced=True)
    saved = plan.model_dump(mode="json")
    refreshed = refresh_saved_quality(plan, request, quality)
    assert plan.model_dump(mode="json") == saved
    assert refreshed["attraction_adjustments"] == quality["attraction_adjustments"]
    assert any(i["code"] == "ATTRACTION_COUNT_ADJUSTED" for i in refreshed["issues"])


def test_schedule_change_updates_only_the_selected_day():
    request, plan = scenario([["A", "B"], ["C"]])
    plan.days[0].attractions[1].optional = True
    request.planning_input.opening_windows[1]["closed"] = True
    untouched = plan.days[1].model_dump(mode="json")
    finalize_plan(plan, request, Routes(), allowed_days={0}, enhanced=True)
    assert plan.days[1].model_dump(mode="json") == untouched


def test_unchanged_attractions_keep_existing_narrative():
    request, plan = scenario([["A"]])
    plan.days[0].theme = "原主题"
    plan.days[0].description = "原描述"
    plan.days[0].activities = ["摄影"]
    finalize_plan(plan, request, Routes(), enhanced=True)
    assert plan.days[0].theme == "原主题"
    assert plan.days[0].description == "原描述"
    assert plan.days[0].activities == ["摄影"]


def test_cross_day_optimization_synchronizes_both_days_narratives():
    request, plan = scenario([["A", "B"], ["C", "D"]])
    request.planning_input.opening_windows = []
    request.planning_input.reservation_facts = []
    positions = {"A": 116.101, "B": 116.201, "C": 116.102, "D": 116.202}
    original = [{a.poi_id for a in day.attractions} for day in plan.days]
    for day in plan.days:
        day.description = "原景点：" + "、".join(a.name for a in day.attractions)
        day.theme = day.description
        for attraction in day.attractions:
            attraction.optional = True
            attraction.location.longitude = positions[attraction.poi_id]

    class Geography:
        def plan_route_by_locations(self, left, right, **kwargs):
            delta = abs(left.longitude - right.longitude)
            return {"duration": 60 + round(delta * 60000), "distance": round(delta * 100000),
                    "walking_distance": round(delta * 1000)}

    finalize_plan(plan, request, Geography(), enhanced=True)
    assert any({a.poi_id for a in day.attractions} != original[i] for i, day in enumerate(plan.days))
    for day in plan.days:
        assert all(a.name in day.description for a in day.attractions)
        assert all(name not in day.description for name in positions if name not in {a.name for a in day.attractions})


def test_explicit_revision_reduction_refreshes_text_without_false_quantity_warning():
    from app.services.planning_constraints import enforce_revision_reduction
    request, original = scenario()
    for attraction in original.days[0].attractions:
        attraction.optional = True
    request.free_text_input = "每天两个景点"
    changed = deepcopy(original)
    changed.days[0].theme = "A和B"
    changed.days[0].description = "A和B"
    enforce_revision_reduction(changed, original, request, "减少一个景点", {0})
    quality = finalize_plan(changed, request, Routes(), allowed_days={0}, enhanced=True)
    assert len(changed.days[0].attractions) == 1
    removed = ({"A", "B"} - {a.name for a in changed.days[0].attractions}).pop()
    assert removed not in changed.days[0].theme + changed.days[0].description
    assert not quality.get("attraction_adjustments")
