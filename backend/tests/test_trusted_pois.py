"""旅行规划的真实 POI 边界测试，不依赖真实高德或 LLM。"""

from unittest.mock import MagicMock, patch
from threading import Barrier

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import Attraction, DayPlan, Location, POIInfo, TripPlan, TripRequest
from app.services.plan_quality import evaluate_plan


def _request() -> TripRequest:
    return TripRequest(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店",
    )


def _candidate() -> POIInfo:
    return POIInfo(
        id="B000A8UIN9", name="故宫博物院", address="景山前街4号", type="风景名胜",
        location=Location(longitude=116.397, latitude=39.918),
    )


def _day(poi_id: str) -> DayPlan:
    return DayPlan(
        date="2026-08-01", day_index=0, description="测试", transportation="公共交通", accommodation="经济型酒店",
        attractions=[Attraction(
            poi_id=poi_id, name="模型改写的名称", address="模型地址",
            location=Location(longitude=0, latitude=0), visit_duration=120, description="测试",
        )],
        meals=[],
    )


def test_valid_poi_id_overwrites_model_fact_fields():
    planner = object.__new__(MultiAgentTripPlanner)
    day = _day("B000A8UIN9")
    MultiAgentTripPlanner._validate_attractions_against_pois(
        day, {"attraction_pois": [_candidate()]}
    )
    attraction = day.attractions[0]
    assert attraction.name == "故宫博物院"
    assert attraction.address == "景山前街4号"
    assert attraction.location.longitude == 116.397
    assert attraction.poi_id == "B000A8UIN9"


def test_unknown_poi_id_is_removed():
    day = _day("unknown")
    MultiAgentTripPlanner._validate_attractions_against_pois(
        day, {"attraction_pois": [_candidate()]}
    )
    assert day.attractions == []


def test_fallback_day_never_invents_attractions_without_candidates():
    planner = object.__new__(MultiAgentTripPlanner)
    day = planner._fallback_day(_request(), 0, "2026-08-01", {"attraction_pois": []})
    assert day.attractions == []


def test_daily_timeout_uses_real_poi_fallback_without_retry(monkeypatch):
    from langchain_core.runnables import RunnableLambda

    planner = object.__new__(MultiAgentTripPlanner)

    class _TimeoutLLM:
        def bind(self, **_kwargs):
            def _raise_timeout(_input):
                raise TimeoutError("request timeout")
            return RunnableLambda(_raise_timeout)

    monkeypatch.setattr(
        "app.agents.trip_planner_agent.get_llm", lambda **_kwargs: _TimeoutLLM()
    )
    day = planner._generate_one_day(
        "测试", 0, "2026-08-01", _request(), {"attraction_pois": [_candidate()]}
    )

    assert day.generation_mode == "fallback"
    assert day.fallback_reason == "timeout"
    assert [item.poi_id for item in day.attractions] == ["B000A8UIN9"]
    plan = TripPlan(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", days=[day], overall_suggestions="测试"
    )
    assert evaluate_plan(plan, 1).degraded_days == [0]


def test_four_daily_tasks_start_concurrently(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_concurrency", 4)
    planner = object.__new__(MultiAgentTripPlanner)
    planner._build_day_base_info = lambda *_args: "基础信息"
    barrier = Barrier(4)
    started = []

    def _generate(_query, index, current_date, request, _state):
        started.append(index)
        barrier.wait(timeout=1)
        return planner._fallback_day(
            request, index, current_date, {"attraction_pois": [_candidate()]}, fallback_reason="timeout"
        )

    planner._generate_one_day = _generate
    request = TripRequest(
        city="北京", start_date="2026-08-01", end_date="2026-08-04", travel_days=4,
        transportation="公共交通", accommodation="经济型酒店",
    )

    result = planner._generate_trip_plan({"request": request, "attraction_pois": [_candidate()]})

    assert sorted(started) == [0, 1, 2, 3]
    assert len(result["trip_plan"].days) == 4


def test_daily_rag_context_receives_current_user_id():
    planner = object.__new__(MultiAgentTripPlanner)
    rag = MagicMock()
    rag.build_rag_context.return_value = ""
    with patch("app.services.rag_service.get_rag_service", return_value=rag):
        planner._build_day_base_info(
            _request(),
            {"user_id": 42, "attraction_pois": [], "hotel_pois": [], "weather_info": []},
        )
    rag.build_rag_context.assert_called_once_with(_request(), user_id=42)


def test_day_base_info_does_not_repeat_all_attraction_candidates():
    """每日候选已单独注入，公共 prompt 不应重复携带所有景点。"""
    planner = object.__new__(MultiAgentTripPlanner)
    with patch("app.services.rag_service.get_rag_service") as get_rag:
        get_rag.return_value.build_rag_context.return_value = ""
        base_info = planner._build_day_base_info(
            _request(),
            {
                "user_id": 42,
                "attraction_pois": [_candidate()],
                "hotel_pois": [],
                "weather_info": [],
            },
        )
    assert "故宫博物院" not in base_info
