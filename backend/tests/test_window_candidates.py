from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.agents.candidates import (
    independent_candidate_count, restaurants_for_day, select_day_candidates, split_pois_for_days,
)
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import Attraction, DayPlan, Meal, POIInfo, TripRequest
from app.services.assistant_intent import daily_attraction_target
from app.services.time_planner import day_time_bounds


def request():
    return TripRequest(city="西安", start_date="2026-11-18", end_date="2026-11-19", travel_days=2,
        transportation="公共交通", accommodation="经济型酒店", preferences=["历史文化"],
        free_text_input="首日13点到达，次日16点离开；每天两个景点。",
        planning_input={"arrival_at": "2026-11-18T13:00:00+08:00",
                        "departure_at": "2026-11-19T16:00:00+08:00", "day_end": "19:00"})


def poi(identity, name, longitude=108.94, latitude=34.26):
    return POIInfo(id=identity, name=name, city="西安", type="风景名胜", address="测试地址",
                   location={"longitude": longitude, "latitude": latitude})


def pool():
    return [poi("remote", "终南山古楼观历史文化景区", 108.328999, 34.069096),
            poi("child", "终南山古楼观历史文化景区岳元帅", 108.328446, 34.068584),
            poi("urban-a", "甲博物馆", 108.941, 34.261), poi("urban-b", "乙历史馆", 108.943, 34.260),
            poi("urban-c", "丙文化园", 108.945, 34.259), poi("urban-d", "丁展览馆", 108.947, 34.258)]


def relation(kind="separate", expired=False):
    now = datetime.now(timezone.utc)
    return {"parent_poi_id": "remote", "child_poi_id": "child", "relation": kind,
            "source": "用户提供的场馆说明", "confirmed": True,
            "observed_at": (now - timedelta(hours=1)).isoformat(),
            "expires_at": (now + timedelta(hours=-.5 if expired else 1)).isoformat()}


@pytest.mark.parametrize(("content", "expected"), [
    (None, None), ("", None), ("每天两个景点", 2), ("每天安排2个独立景点", 2),
    ("当天恰好安排三处景点", 3), ("每日二十个景点", 20), ("每天两个景点，每日二个景点", 2),
    ("每天两个景点，不要太累", 2), ("不要每天两个景点", None), ("每天不要安排两个景点", None),
    ("不需要每天两个景点", None), ("无需每天两个景点", None), ("每天两个景点以上", None),
    ("每天最多三个景点", None), ("每天至少两个景点", None), ("每天两到三个景点", None),
    ("每天约两个景点", None), ("每天两个景点或三个景点", None),
    ("每天两个景点，每日三个景点", None), ("每天两个景点，第1天三个景点", None),
    ("每天十十个景点", None), ("每天0个景点", None), ("每天21个景点", None),
    ("有两个景点比较好", None), ("每天安排博物馆", None),
])
def test_daily_target_only_accepts_clear_bounded_exact_counts(content, expected):
    assert daily_attraction_target(content) == expected


def test_independent_counts_and_optional_selection_do_not_count_child_as_another_visit():
    req = request()
    assert independent_candidate_count(pool(), req) == 5
    chosen = select_day_candidates(pool(), req, 0)
    assert len(chosen) == 4
    assert "remote" in {item.id for item in chosen}
    assert "child" not in {item.id for item in chosen}


def test_current_separate_relation_is_honored_but_expired_relation_is_not_certified():
    req = request()
    req.planning_input.poi_relations = [relation()]
    assert independent_candidate_count(pool()[:2], req) == 2
    assert {item.id for item in select_day_candidates(pool()[:2], req, 0)} == {"remote", "child"}
    req.planning_input.poi_relations = [relation(expired=True)]
    assert independent_candidate_count(pool()[:2], req) == 1


def test_required_reserved_and_protected_members_are_never_removed_by_grouping():
    req = request()
    req.constraints.must_visit = [pool()[0].name]
    req.planning_input.commitments = [{"day_index": 0, "poi_id": "child", "status": "confirmed"}]
    chosen = select_day_candidates(pool(), req, 0, {"urban-d"})
    assert {"remote", "child", "urban-d"} <= {item.id for item in chosen}


def test_short_arrival_day_gets_compact_city_choices_before_remote_spares():
    req = request()
    hotel = poi("hotel", "已搜索住宿", 108.932761, 34.240378)
    allocated = split_pois_for_days(pool(), 2, request=req, hotel_pois=[hotel])
    assert all(len(items) >= 2 for items in allocated)
    assert all(item.id.startswith("urban-") for item in allocated[0])
    assert all(item.id.startswith("urban-") for items in allocated for item in items[:2])
    identities = [item.id for items in allocated for item in items]
    assert len(identities) == len(set(identities))
    assert "child" not in identities
    assert "remote" in {item.id for item in allocated[1]}


def test_remote_explicit_booking_keeps_its_day_and_no_independent_choices_are_invented():
    req = request()
    req.planning_input.commitments = [{"day_index": 0, "poi_id": "remote", "status": "confirmed"}]
    allocated = split_pois_for_days(pool(), 2, request=req)
    assert "remote" in {item.id for item in allocated[0]}
    scarce = split_pois_for_days(pool()[:2], 2, request=request())
    assert sum(map(len, scarce)) == 1
    assert len({item.id for items in scarce for item in items}) == 1


def test_shared_window_preserves_raw_hard_bounds_and_clips_arrival_departure():
    req = request()
    assert day_time_bounds(req, 0, "2026-11-18") == (13 * 60, 19 * 60, 9 * 60, 19 * 60)
    assert day_time_bounds(req, 1, "2026-11-19") == (9 * 60, 16 * 60, 9 * 60, 19 * 60)


def test_restaurant_ranking_uses_current_day_before_truncating_the_pool():
    restaurants = [poi(f"far-{i}", f"远处餐厅{i}", 108.3 + i / 1000, 34.06) for i in range(10)]
    nearby = poi("near", "就近餐厅", 108.9411, 34.2611)
    ranked = restaurants_for_day([*restaurants, nearby], pool()[2:4], request(), 0)
    assert len(ranked) == 8
    assert ranked[0].id == "near"


def test_restaurant_ranking_does_not_let_one_remote_spare_dominate_city_choices():
    far, _, *urban = pool()
    remote_restaurant = poi("far-food", "远郊餐厅", far.location.longitude, far.location.latitude)
    urban_restaurant = poi("city-food", "市区餐厅", 108.944, 34.260)
    ranked = restaurants_for_day([remote_restaurant, urban_restaurant], [*urban, far], request(), 1)
    assert ranked[0].id == "city-food"


def test_day_completion_keeps_explicit_meals_and_omits_nonintersecting_defaults():
    def day():
        return DayPlan(day_index=0, date="2026-11-18", description="测试", transportation="公共交通",
            accommodation="经济型酒店", attractions=[], meals=[Meal(type=kind, name=kind)
                for kind in ("breakfast", "lunch", "dinner")])
    req = request()
    plan = day()
    MultiAgentTripPlanner._complete_day(plan, req, {})
    assert {meal.type for meal in plan.meals} == {"lunch", "dinner"}
    req.planning_input.meal_windows = [{"type": "breakfast", "start": "08:00", "end": "09:00", "duration_minutes": 30}]
    plan = day()
    MultiAgentTripPlanner._complete_day(plan, req, {})
    assert {meal.type for meal in plan.meals} == {"breakfast", "lunch", "dinner"}


def test_generation_prompt_whitelist_and_restaurants_use_effective_daily_context():
    planner = object.__new__(MultiAgentTripPlanner)
    seen = {}

    def generate(query, index, current_date, req, state):
        seen[index] = (query, state)
        return DayPlan(day_index=index, date=current_date, description="测试", transportation=req.transportation,
            accommodation=req.accommodation, attractions=[Attraction(poi_id=p.id, name=p.name, address=p.address,
                location=p.location, visit_duration=60, description="测试") for p in state["attraction_pois"][:2]], meals=[])

    planner._generate_one_day = generate
    hotel = poi("hotel", "候选住宿", 108.932761, 34.240378)
    planner._generate_trip_plan({"request": request(), "attraction_pois": pool(), "hotel_pois": [hotel],
                                "restaurant_pois": [poi("restaurant", "市内餐厅")]})
    assert "13:00-19:00" in seen[0][0]
    assert "09:00-16:00" in seen[1][0]
    assert all("本日明确数量目标为2个独立景点" in query for query, _ in seen.values())
    assert "安排3餐" not in seen[0][0]
    assert "餐次：dinner,lunch" in seen[0][0]
    assert "餐次：breakfast,lunch" in seen[1][0]
    assert all(item.id.startswith("urban-") for item in seen[0][1]["attraction_pois"])


def test_real_daily_chain_receives_context_policy_without_extra_model_retry(monkeypatch):
    from langchain_core.runnables import RunnableLambda
    seen = []

    class LLM:
        def bind(self, **kwargs):
            def respond(prompt):
                seen.append(prompt.to_messages())
                raise TimeoutError("isolated provider timeout")
            return RunnableLambda(respond)

    monkeypatch.setattr("app.agents.trip_planner_agent.get_llm", lambda **kwargs: LLM())
    planner = object.__new__(MultiAgentTripPlanner)
    day = planner._generate_one_day_uncached("本日明确数量目标为2个独立景点", 0, "2026-11-18",
                                             request(), {"attraction_pois": pool()[2:]})
    assert len(seen) == 1
    assert "未指定时默认2-3个" in seen[0][0].content
    assert "本日可排餐次" in seen[0][0].content
    assert "必须包含早中晚三餐" not in seen[0][0].content
    assert "本日明确数量目标为2个独立景点" in seen[0][1].content
    assert day.generation_mode == "fallback"


def test_search_broadens_a_raw_sufficient_but_nonindependent_pool_once(monkeypatch):
    planner = object.__new__(MultiAgentTripPlanner)
    planner.amap_service = MagicMock()
    planner.amap_service.search_poi.side_effect = lambda keyword, city: pool()[:4] if keyword == "历史文化" else pool()[2:]
    rag = MagicMock()
    rag.get_knowledge_attractions.return_value = []
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag)
    result = planner._search_attractions({"request": request()})
    keywords = [call.args[0] for call in planner.amap_service.search_poi.call_args_list]
    assert sorted(keywords) == sorted(["历史文化", "景点", "博物馆", "公园"])
    assert len(result["attraction_pois"]) == 6
