"""真实旅行规划链路的低频性能评测。

该工具直接调用 Agent，不创建历史记录、不调用 HTTP/SSE 包装层；用于回答
"数据服务、RAG、LLM 到首 token 和完整规划各花多久"，不是压测工具。
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta, timezone, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from prometheus_client import REGISTRY

from .rag_benchmark import summarize_latencies
from ..models.schemas import TripRequest


def _counter_value(metric_name: str, operation: str = "trip_day") -> float:
    """读取当前进程中指定 operation 的 Counter 增量基线。"""
    total = 0.0
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == metric_name and sample.labels.get("operation") == operation:
                total += float(sample.value)
    return total


def _usage_snapshot() -> dict[str, float]:
    return {
        "model_calls": _counter_value("ai_model_call_seconds_count"),
        "input_tokens": _counter_value("ai_model_input_tokens_total"),
        "output_tokens": _counter_value("ai_model_output_tokens_total"),
        "estimated_cost_usd": _counter_value("ai_model_estimated_cost_usd_total"),
    }


def _usage_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {name: max(0.0, after[name] - before[name]) for name in before}


def _quality_snapshot(plan: Any, expected_days: int) -> dict[str, float | bool | int]:
    days = list(getattr(plan, "days", []) or [])
    attractions = [item for day in days for item in (getattr(day, "attractions", []) or [])]
    fallback_days = sum(getattr(day, "generation_mode", "") == "fallback" for day in days)
    return {
        "days_match": len(days) == expected_days,
        "trusted_poi": bool(attractions) and all(bool(getattr(item, "poi_id", "")) for item in attractions),
        "days": len(days),
        "attractions": len(attractions),
        "fallback_days": fallback_days,
    }


def run_benchmark(
    planner: Any,
    request: TripRequest,
    runs: int = 3,
    *,
    clock: Callable[[], float] = perf_counter,
) -> dict:
    """执行少量真实规划，按运行记录首进度、首 LLM token 与完整耗时。"""
    if runs < 1:
        raise ValueError("runs 必须大于 0")
    before_usage = _usage_snapshot()
    reports: list[dict] = []
    total_latencies: list[float] = []
    first_progress_latencies: list[float] = []
    first_token_from_start: list[float] = []
    day_ttft_latencies: list[float] = []

    for index in range(runs):
        started_at = clock()
        first_progress: float | None = None
        first_token: float | None = None
        run: dict[str, Any] = {"run": index + 1, "outcome": "error"}

        def progress_callback(_: str, __: int, ___: str) -> None:
            nonlocal first_progress
            if first_progress is None:
                first_progress = clock() - started_at

        def trace_callback(event: str, payload: dict) -> None:
            nonlocal first_token
            if event == "llm_first_token":
                day_ttft_latencies.append(float(payload["seconds"]))
                if first_token is None:
                    first_token = clock() - started_at

        try:
            plan = planner.plan_trip(
                request,
                user_id=0,
                progress_callback=progress_callback,
                trace_callback=trace_callback,
            )
            run["outcome"] = "success"
            run["quality"] = _quality_snapshot(plan, request.travel_days)
        except Exception as exc:  # 报告失败类型，后续运行仍继续，避免一个上游波动中止全批。
            run["error_type"] = type(exc).__name__
            run["error"] = str(exc)[:200]
        finally:
            run["plan_total_seconds"] = clock() - started_at
            run["first_progress_seconds"] = first_progress
            run["first_llm_token_from_plan_start_seconds"] = first_token
            reports.append(run)
            total_latencies.append(run["plan_total_seconds"])
            if first_progress is not None:
                first_progress_latencies.append(first_progress)
            if first_token is not None:
                first_token_from_start.append(first_token)

    successful = [item for item in reports if item["outcome"] == "success"]
    qualities = [item["quality"] for item in successful]
    after_usage = _usage_snapshot()
    return {
        "mode": "planning_live",
        "scope": (
            "直接调用 Agent；包含高德数据节点、RAG、逐日 LLM 与本地质量修复，"
            "不包含 HTTP/SSE 传输、鉴权、历史持久化和路线 API 二次校验。"
        ),
        "request": request.model_dump(mode="json"),
        "runs": runs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "outcomes": {
            "success": len(successful),
            "error": runs - len(successful),
            "success_rate": len(successful) / runs,
        },
        "quality": {
            "days_match_rate": sum(bool(item["days_match"]) for item in qualities) / len(qualities) if qualities else 0.0,
            "trusted_poi_rate": sum(bool(item["trusted_poi"]) for item in qualities) / len(qualities) if qualities else 0.0,
            "fallback_day_rate": (
                sum(int(item["fallback_days"]) for item in qualities)
                / max(1, sum(int(item["days"]) for item in qualities))
            ) if qualities else 0.0,
        },
        "timings": {
            "plan_total": summarize_latencies(total_latencies),
            "first_progress": summarize_latencies(first_progress_latencies),
            "first_llm_token_from_plan_start": summarize_latencies(first_token_from_start),
            "per_day_llm_ttft": summarize_latencies(day_ttft_latencies),
        },
        "model_usage": _usage_delta(before_usage, after_usage),
        "details": reports,
        "limitations": [
            "这是低频功能评测，不能用作并发压测或线上 SLA。",
            "estimated_cost_usd 仅在部署环境配置模型单价且供应商返回 usage 时有效。",
            "first_progress 是工作流阶段事件；first_llm_token_from_plan_start 才是从规划开始到首个模型 token。",
        ],
    }


def markdown_report(report: dict) -> str:
    lines = [
        "# 旅行规划全链路评测报告",
        "",
        f"- 模式：`{report['mode']}`",
        f"- 运行次数：{report['runs']}",
        f"- 范围：{report['scope']}",
        "",
        "## 结果与质量边界",
        "",
        f"- 成功率：{report['outcomes']['success_rate']:.2%} ({report['outcomes']['success']}/{report['runs']})",
        f"- 行程天数匹配率：{report['quality']['days_match_rate']:.2%}",
        f"- 可信 POI 覆盖率：{report['quality']['trusted_poi_rate']:.2%}",
        f"- 单日 LLM 兜底率：{report['quality']['fallback_day_rate']:.2%}",
        "",
        "## 时延",
        "",
    ]
    for stage, values in report["timings"].items():
        lines.append(
            f"- `{stage}`：n={values['samples']}，平均 {values['mean_seconds']:.4f}s，"
            f"p50 {values['p50_seconds']:.4f}s，p95 {values['p95_seconds']:.4f}s，"
            f"最大 {values['max_seconds']:.4f}s"
        )
    usage = report["model_usage"]
    lines.extend([
        "",
        "## 模型用量",
        "",
        f"- 调用数：{usage['model_calls']:.0f}",
        f"- 输入 / 输出 Token：{usage['input_tokens']:.0f} / {usage['output_tokens']:.0f}",
        f"- 估算成本（USD）：{usage['estimated_cost_usd']:.6f}",
        "",
        "## 解释边界",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ])
    return "\n".join(lines) + "\n"


def _request_from_args(args: argparse.Namespace) -> TripRequest:
    start = date.fromisoformat(args.start_date) if args.start_date else date.today()
    end = start + timedelta(days=args.days - 1)
    return TripRequest(
        city=args.city,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        travel_days=args.days,
        transportation=args.transportation,
        accommodation=args.accommodation,
        preferences=args.preferences,
        free_text_input=args.free_text_input,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run low-frequency live trip-planning benchmark")
    parser.add_argument("--city", default="北京")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--start-date", help="YYYY-MM-DD；默认今天")
    parser.add_argument("--transportation", default="公共交通")
    parser.add_argument("--accommodation", default="经济型酒店")
    parser.add_argument("--preferences", nargs="*", default=["历史文化"])
    parser.add_argument("--free-text-input", default="")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("evals/results/planning_live.json"))
    args = parser.parse_args()
    if not 1 <= args.days <= 30:
        raise ValueError("days 必须在 1 到 30 之间")

    from app.agents.trip_planner_agent import get_trip_planner_agent

    report = run_benchmark(get_trip_planner_agent(), _request_from_args(args), args.runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output = args.output.with_suffix(".md")
    markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({
        "runs": report["runs"], "outcomes": report["outcomes"],
        "timings": report["timings"], "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
