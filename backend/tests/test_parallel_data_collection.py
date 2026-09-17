"""LangGraph 数据节点并行回归：避免景点、天气、酒店查询退回串行。"""

import time
from types import SimpleNamespace

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import TripPlan, TripRequest


def test_data_nodes_run_in_parallel_before_generation():
    planner = object.__new__(MultiAgentTripPlanner)
    completed = []

    def delayed_node(name, payload):
        def node(_state):
            time.sleep(0.12)
            completed.append(name)
            return payload
        return node

    planner._search_attractions = delayed_node("attractions", {"attraction_pois": []})
    planner._get_weather = delayed_node("weather", {"weather_info": [], "weather_notice": ""})
    planner._search_hotels = delayed_node("hotels", {"hotel_pois": []})
    planner._build_rag_context = delayed_node("rag", {"rag_context": ""})
    planner._generate_trip_plan = lambda state: {
        "trip_plan": TripPlan(
            city=state["request"].city,
            start_date=state["request"].start_date,
            end_date=state["request"].end_date,
            days=[],
            overall_suggestions="测试",
        ),
        "error": False,
    }
    planner._fallback_plan = lambda _state: {"error": False}
    planner._should_fallback = lambda state: "fallback_plan" if state.get("error") else "end"
    graph = planner._build_graph()
    request = TripRequest(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店",
    )

    started = time.perf_counter()
    result = graph.invoke({"request": request})
    elapsed = time.perf_counter() - started

    assert set(completed) == {"attractions", "weather", "hotels", "rag"}
    assert result["trip_plan"].city == "北京"
    # 四个各 0.12s 的节点，串行需约 0.48s；给慢机器留出合理余量。
    assert elapsed < 0.30


def test_attraction_fallback_queries_run_in_parallel_and_do_not_rebuild_city_index(monkeypatch):
    planner = object.__new__(MultiAgentTripPlanner)
    calls = []

    class Amap:
        def search_poi(self, keyword, _city):
            calls.append(keyword)
            time.sleep(0.10)
            return []

    rag = SimpleNamespace(get_knowledge_attractions=lambda city, ensure_city: [])
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: rag)
    planner.amap_service = Amap()
    planner._emit_progress = lambda *args: None
    request = TripRequest(
        city="北京", start_date="2026-08-01", end_date="2026-08-01", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店", preferences=["历史文化"],
    )

    started = time.perf_counter()
    result = planner._search_attractions({"request": request})
    elapsed = time.perf_counter() - started

    assert result == {"attraction_pois": []}
    assert set(calls) == {"历史文化", "景点", "博物馆", "公园"}
    # 首次查询约 0.10s，三个补充查询并行约 0.10s；串行约 0.40s。
    assert elapsed < 0.32
