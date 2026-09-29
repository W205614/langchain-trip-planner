"""One bounded real-provider planning sample without Java writes or secrets in evidence."""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def acceptance(report: dict) -> dict:
    quality = report["details"][0].get("quality") or {}
    names = {item["name"] for day in report.get("selected_days", []) for item in day["attractions"]}
    required = set(report["request"]["constraints"]["must_visit"])
    excluded = set(report["request"]["constraints"]["avoid"])
    checks = {
        "plan_completed": report["outcomes"]["success"] == 1,
        "real_model_used": report["model_usage"]["model_calls"] >= 1,
        "no_fallback": quality.get("fallback_days") == 0,
        "deterministic_rules": quality.get("deterministic_passed") is True,
        "required_pois_selected": required <= names,
        "excluded_pois_absent": not bool(excluded & names),
        "trusted_poi_ids": all(item.get("poi_id") for day in report.get("selected_days", []) for item in day["attractions"]),
    }
    return {"passed": all(checks.values()), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-call-limit", type=int, required=True)
    parser.add_argument("--map-call-limit", type=int, required=True)
    args = parser.parse_args()
    ledger = Path(os.environ.get("ACCEPTANCE_BUDGET_FILE", ""))
    if not ledger.is_absolute() or args.model_call_limit < 1 or args.map_call_limit < 1:
        raise ValueError("An absolute durable budget and positive call limits are required")

    # MCP and HTTP clients may log their credential-bearing request URLs.
    logging.disable(logging.CRITICAL)
    from app.agents.trip_planner_agent import get_trip_planner_agent
    from app.config import get_settings
    from app.evals.planning_benchmark import markdown_report, run_benchmark
    from app.models.schemas import PlanningConstraints, TripRequest
    from app.services import call_budget

    cfg = get_settings()
    if cfg.rag_enabled or not cfg.llm_api_key or not cfg.amap_api_key or cfg.amap_transport != "mcp":
        raise RuntimeError("Expected real model and MCP keys with RAG explicitly disabled")
    call_budget.LIMITS["text"] = args.model_call_limit
    call_budget.LIMITS["amap"] = args.map_call_limit

    request = TripRequest(
        city="北京", start_date="2026-10-15", end_date="2026-10-15", travel_days=1,
        transportation="公共交通", accommodation="经济型酒店",
        preferences=["历史文化"],
        constraints=PlanningConstraints(must_visit=["故宫博物院"], avoid=["八达岭长城"]),
        free_text_input="希望节奏舒缓，必须去故宫博物院，不去八达岭长城。",
    )
    planner = get_trip_planner_agent()
    original = planner.plan_trip
    selected = []

    def capture_plan(*positional, **named):
        plan = original(*positional, **named)
        selected.extend({
            "date": day.date,
            "generation_mode": day.generation_mode,
            "fallback_reason": day.fallback_reason,
            "attractions": [{"name": item.name, "poi_id": item.poi_id} for item in day.attractions],
        } for day in plan.days)
        return plan

    planner.plan_trip = capture_plan
    report = run_benchmark(planner, request, runs=1)
    report["provider_context"] = {
        "model": cfg.llm_model,
        "model_base_host": urlsplit(cfg.llm_base_url).hostname,
        "map_transport": cfg.amap_transport,
        "rag_enabled": cfg.rag_enabled,
        "java_http_included": False,
    }
    report["selected_days"] = selected
    report["acceptance"] = acceptance(report)
    with sqlite3.connect(ledger) as db:
        report["budget_used_after"] = dict(db.execute("SELECT kind, used FROM calls"))
    report["budget_limits"] = {"text": args.model_call_limit, "amap": args.map_call_limit}
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    for detail in report["details"]:
        if detail.get("error"):
            for key in (cfg.llm_api_key, cfg.amap_api_key):
                detail["error"] = detail["error"].replace(key, "<redacted>")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({
        "success": report["outcomes"]["success"],
        "model_calls": report["model_usage"]["model_calls"],
        "tokens": [report["model_usage"]["input_tokens"], report["model_usage"]["output_tokens"]],
        "fallback_days": report["details"][0].get("quality", {}).get("fallback_days"),
        "deterministic_passed": report["details"][0].get("quality", {}).get("deterministic_passed"),
        "acceptance_passed": report["acceptance"]["passed"],
        "budget_used": report["budget_used_after"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if report["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
