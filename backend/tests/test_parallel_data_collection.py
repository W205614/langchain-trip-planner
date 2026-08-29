"""LangGraph 数据节点并行回归：避免景点、天气、酒店查询退回串行。"""

import time

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

    assert set(completed) == {"attractions", "weather", "hotels"}
    assert result["trip_plan"].city == "北京"
    # 三个各 0.12s 的节点，串行需约 0.36s；给慢机器留出合理余量。
    assert elapsed < 0.30
