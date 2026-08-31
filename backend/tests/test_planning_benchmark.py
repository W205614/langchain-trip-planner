from types import SimpleNamespace

import pytest

from app.evals import planning_benchmark
from app.models.schemas import TripRequest


class _FakePlanner:
    def plan_trip(self, request, user_id, progress_callback, trace_callback):
        assert user_id == 0
        progress_callback("search_attractions", 10, "正在搜索真实景点")
        trace_callback("llm_first_token", {"day_index": 0, "seconds": 0.12})
        attraction = SimpleNamespace(poi_id="B0001")
        day = SimpleNamespace(attractions=[attraction], generation_mode="llm")
        return SimpleNamespace(days=[day])


class _FailingPlanner:
    def plan_trip(self, request, user_id, progress_callback, trace_callback):
        progress_callback("search_attractions", 10, "正在搜索真实景点")
        trace_callback("llm_first_token", {"day_index": 0, "seconds": 0.2})
        raise RuntimeError("trusted POI unavailable")


def test_planning_benchmark_reports_trace_quality_and_usage(monkeypatch):
    snapshots = iter([
        {"model_calls": 10.0, "input_tokens": 100.0, "output_tokens": 20.0, "estimated_cost_usd": 0.01},
        {"model_calls": 12.0, "input_tokens": 160.0, "output_tokens": 50.0, "estimated_cost_usd": 0.03},
    ])
    monkeypatch.setattr(planning_benchmark, "_usage_snapshot", lambda: next(snapshots))
    request = TripRequest(
        city="北京", start_date="2026-08-31", end_date="2026-08-31", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店", preferences=["历史文化"],
    )

    report = planning_benchmark.run_benchmark(_FakePlanner(), request, runs=2)

    assert report["outcomes"] == {"success": 2, "error": 0, "success_rate": 1.0}
    assert report["quality"]["days_match_rate"] == 1.0
    assert report["quality"]["trusted_poi_rate"] == 1.0
    assert report["timings"]["first_progress"]["samples"] == 2
    assert report["timings"]["first_llm_token_from_plan_start"]["samples"] == 2
    assert report["timings"]["per_day_llm_ttft"]["p50_seconds"] == 0.12
    assert report["model_usage"]["model_calls"] == 2.0
    assert report["model_usage"]["input_tokens"] == 60.0
    assert report["model_usage"]["output_tokens"] == 30.0
    assert report["model_usage"]["estimated_cost_usd"] == pytest.approx(0.02)
    assert "first_llm_token_from_plan_start" in planning_benchmark.markdown_report(report)


def test_planning_benchmark_keeps_upstream_failure_as_failure(monkeypatch):
    snapshots = iter([
        {"model_calls": 0.0, "input_tokens": 0.0, "output_tokens": 0.0, "estimated_cost_usd": 0.0},
        {"model_calls": 1.0, "input_tokens": 10.0, "output_tokens": 5.0, "estimated_cost_usd": 0.0},
    ])
    monkeypatch.setattr(planning_benchmark, "_usage_snapshot", lambda: next(snapshots))
    request = TripRequest(
        city="北京", start_date="2026-08-31", end_date="2026-08-31", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店",
    )

    report = planning_benchmark.run_benchmark(_FailingPlanner(), request, runs=1)

    assert report["outcomes"] == {"success": 0, "error": 1, "success_rate": 0.0}
    assert report["details"][0]["error_type"] == "RuntimeError"
    assert report["timings"]["first_llm_token_from_plan_start"]["samples"] == 1
