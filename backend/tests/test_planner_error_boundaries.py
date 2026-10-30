"""Exercise real planner error paths with only provider I/O replaced."""
from datetime import datetime, timedelta
from threading import Event

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import Location, POIInfo, TripRequest
from app.services.execution import cancellation_var, execution_deadline
from app.services.time_planner import _route
from app.services.planning_constraints import finalize_plan
from app.services.plan_quality import repair_plan_routes
from planning_fixtures import VALID_REQUEST, make_complete_trip_plan


@pytest.fixture
def daily(monkeypatch):
    planner = object.__new__(MultiAgentTripPlanner)
    request = TripRequest(city="北京", start_date="2026-11-01", end_date="2026-11-01",
        travel_days=1, transportation="步行", accommodation="经济型酒店")
    poi = POIInfo(id="trusted", name="公园", type="公园", address="北京",
        location=Location(longitude=116.3, latitude=39.9))
    replies, calls = [], []

    def provider(_prompt, **kwargs):
        calls.append(_prompt)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)

    monkeypatch.setattr("app.agents.trip_planner_agent.get_llm",
        lambda **kwargs: RunnableLambda(provider))
    return planner, request, {"request": request, "attraction_pois": [poi]}, replies, calls


@pytest.mark.parametrize("error", [AttributeError("construction bug"), ValueError("construction bug")])
def test_internal_day_construction_error_propagates_without_second_model_call(daily, monkeypatch, error):
    planner, request, state, replies, calls = daily
    replies.append('{"attractions":[{"poi_id":"trusted"}]}')

    def broken(*args):
        raise error

    monkeypatch.setattr(planner, "_build_day_plan_from_draft", broken)
    with pytest.raises(type(error), match="construction bug"):
        planner._generate_one_day_uncached("测试", 0, request.start_date, request, state)
    assert len(calls) == 1


def test_parallel_generation_does_not_replace_internal_error_with_whole_trip_fallback(daily, monkeypatch):
    planner, _, state, replies, calls = daily
    replies.append('{"attractions":[{"poi_id":"trusted"}]}')
    monkeypatch.setattr(planner, "_build_day_base_info", lambda *args: "测试")

    def broken(*args):
        raise AttributeError("construction bug")

    monkeypatch.setattr(planner, "_build_day_plan_from_draft", broken)
    with pytest.raises(AttributeError, match="construction bug"):
        planner._generate_trip_plan(state)
    assert len(calls) == 1


def test_malformed_provider_json_still_gets_one_correction(daily):
    planner, request, state, replies, calls = daily
    replies.extend(["bad JSON", '{"attractions":[{"poi_id":"trusted"}]}'])
    day = planner._generate_one_day_uncached("测试", 0, request.start_date, request, state)
    assert [a.poi_id for a in day.attractions] == ["trusted"]
    assert day.generation_mode == "llm"
    assert len(calls) == 2


def test_untrusted_provider_selection_has_bounded_visible_fallback(daily):
    planner, request, state, replies, calls = daily
    replies.extend(['{"attractions":[{"poi_id":"invented"}]}'] * 2)
    day = planner._generate_one_day_uncached("测试", 0, request.start_date, request, state)
    assert day.generation_mode == "fallback"
    assert day.fallback_reason == "invalid_response"
    assert [a.poi_id for a in day.attractions] == ["trusted"]
    assert len(calls) == 2


def test_expired_day_request_never_calls_provider(daily):
    planner, request, state, _, calls = daily
    with execution_deadline(datetime.now() - timedelta(seconds=1)):
        with pytest.raises(TimeoutError, match="deadline"):
            planner._generate_one_day_uncached("测试", 0, request.start_date, request, state)
    assert calls == []


@pytest.mark.parametrize("error", [AttributeError("adapter bug"), RuntimeError("adapter bug")])
def test_route_adapter_programming_errors_propagate(error):
    class BrokenRoute:
        def plan_route_by_locations(self, *args, **kwargs):
            raise error

    with pytest.raises(type(error), match="adapter bug"):
        _route(Location(longitude=116.3, latitude=39.9),
            Location(longitude=116.4, latitude=39.9), BrokenRoute(), "北京", "walking", {}, set())


def test_cancelled_route_never_calls_provider_even_with_cached_route():
    class NoCalls:
        def plan_route_by_locations(self, *args, **kwargs):
            pytest.fail("cancelled task must not send requests")

    flag = Event()
    flag.set()
    token = cancellation_var.set(flag)
    try:
        with pytest.raises(TimeoutError, match="cancelled"):
            _route(Location(longitude=116.3, latitude=39.9),
                Location(longitude=116.4, latitude=39.9), NoCalls(), "北京", "walking", {}, set())
    finally:
        cancellation_var.reset(token)


def test_route_upstream_timeout_still_exposes_unverified_estimate():
    class UnavailableRoute:
        def plan_route_by_locations(self, *args, **kwargs):
            raise TimeoutError("upstream timeout")

    unknown = set()
    result = _route(Location(longitude=116.3, latitude=39.9),
        Location(longitude=116.4, latitude=39.9), UnavailableRoute(), "北京", "walking", {}, unknown)
    assert result == (30, False)
    assert "route_duration_estimated_30_minutes" in unknown


@pytest.mark.parametrize("timeout", [True, False])
def test_cancellation_during_route_call_propagates(timeout):
    flag = Event()

    class CancellingRoute:
        def plan_route_by_locations(self, *args, **kwargs):
            flag.set()
            if timeout:
                raise TimeoutError("upstream timeout")
            return {"duration": 600}

    token = cancellation_var.set(flag)
    try:
        with pytest.raises(TimeoutError, match="cancelled"):
            _route(Location(longitude=116.3, latitude=39.9),
                Location(longitude=116.4, latitude=39.9), CancellingRoute(), "北京", "walking", {}, set())
    finally:
        cancellation_var.reset(token)


@pytest.mark.parametrize("operation", [finalize_plan, repair_plan_routes])
def test_legacy_route_quality_does_not_hide_internal_adapter_errors(operation):
    class BrokenRoute:
        def plan_route_by_locations(self, *args, **kwargs):
            raise AttributeError("adapter bug")

    plan = make_complete_trip_plan()
    plan.days[0].attractions.append(plan.days[1].attractions[0])
    with pytest.raises(AttributeError, match="adapter bug"):
        if operation is finalize_plan:
            operation(plan, TripRequest(**VALID_REQUEST), BrokenRoute())
        else:
            operation(plan, BrokenRoute(), "步行")
