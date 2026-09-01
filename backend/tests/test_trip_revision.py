"""历史行程增量改排的候选隔离与预算回算测试。"""

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import Attraction, DayPlan, Location, Meal, POIInfo, TripPlan, TripRequest


def _request() -> TripRequest:
    return TripRequest(
        city="北京", start_date="2026-08-01", end_date="2026-08-02", travel_days=2,
        transportation="公共交通", accommodation="经济型酒店", preferences=["历史文化"],
    )


def _attraction(poi_id: str, name: str) -> Attraction:
    return Attraction(
        poi_id=poi_id, name=name, address="北京", location=Location(longitude=116.3, latitude=39.9),
        visit_duration=120, description="测试", ticket_price=10,
    )


def _plan() -> TripPlan:
    return TripPlan(
        city="北京", start_date="2026-08-01", end_date="2026-08-02", overall_suggestions="测试",
        days=[
            DayPlan(date="2026-08-01", day_index=0, description="第一天", transportation="公共交通", accommodation="酒店", attractions=[_attraction("old", "旧景点")], meals=[]),
            DayPlan(date="2026-08-02", day_index=1, description="第二天", transportation="公共交通", accommodation="酒店", attractions=[_attraction("other", "其它日期景点")], meals=[]),
        ],
    )


def test_revise_trip_day_excludes_other_days_and_recalculates_budget(monkeypatch):
    """改排候选不能复用其它日期的 POI，且写回后预算不保留旧总额。"""
    agent = object.__new__(MultiAgentTripPlanner)
    fresh = [
        POIInfo(id="other", name="其它日期景点", type="景点", address="北京", location=Location(longitude=116.31, latitude=39.91)),
        POIInfo(id="new", name="新博物馆", type="景点", address="北京", location=Location(longitude=116.32, latitude=39.92)),
    ]
    monkeypatch.setattr(agent, "_search_attractions", lambda _state: {"attraction_pois": fresh})
    captured = {}

    def _generate(_query, day_index, current_date, request, state):
        captured["candidate_ids"] = [poi.id for poi in state["attraction_pois"]]
        return DayPlan(
            date=current_date, day_index=day_index, description="室内改排", transportation=request.transportation,
            accommodation=request.accommodation, attractions=[_attraction("new", "新博物馆")],
            meals=[Meal(type="breakfast", name="早", estimated_cost=20), Meal(type="lunch", name="午", estimated_cost=40), Meal(type="dinner", name="晚", estimated_cost=60)],
        )

    monkeypatch.setattr(agent, "_generate_one_day", _generate)
    plan = _plan()
    plan.budget = None
    revised = agent.revise_trip_day(_request(), plan, 0, "下雨改室内", user_id=7)

    assert captured["candidate_ids"] == ["old", "new"]
    assert revised.days[0].attractions[0].poi_id == "new"
    assert revised.days[1].attractions[0].poi_id == "other"
    assert revised.budget is not None
    assert revised.budget.total_meals == 120
