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
from ..services.plan_quality import evaluate_plan


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


def _amap_cache_snapshot(planner: Any) -> dict[str, int]:
    """评测可选读取事实缓存计数；mock Agent 不需要实现该能力。"""
    cache_stats = getattr(getattr(planner, "amap_service", None), "cache_stats", None)
    if not callable(cache_stats):
        return {}
    stats = cache_stats()
    return {str(key): int(value) for key, value in stats.items() if isinstance(value, (int, float))}


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: max(0, after.get(key, 0) - before.get(key, 0)) for key in set(before) | set(after)}


def _quality_snapshot(plan: Any, expected_days: int) -> dict[str, float | bool | int]:
    days = list(getattr(plan, "days", []) or [])
    attractions = [item for day in days for item in (getattr(day, "attractions", []) or [])]
    fallback_days = sum(getattr(day, "generation_mode", "") == "fallback" for day in days)
    deterministic_quality = evaluate_plan(plan, expected_days)
    return {
        "days_match": len(days) == expected_days,
        "trusted_poi": bool(attractions) and all(bool(getattr(item, "poi_id", "")) for item in attractions),
        "days": len(days),
        "attractions": len(attractions),
        "fallback_days": fallback_days,
        "deterministic_score": deterministic_quality.score,
        "deterministic_passed": deterministic_quality.passed,
        "deterministic_warning_count": len(deterministic_quality.warnings),
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
    stage_latencies: dict[str, list[float]] = {}

    for index in range(runs):
        started_at = clock()
        cache_before = _amap_cache_snapshot(planner)
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
            elif event == "stage_duration":
                stage = str(payload.get("stage", "unknown"))
                seconds = payload.get("seconds")
                if isinstance(seconds, (int, float)) and seconds >= 0:
                    stage_latencies.setdefault(stage, []).append(float(seconds))

        try:
            plan = planner.plan_trip(
                request,
                user_id=0,
                progress_callback=progress_callback,
                trace_callback=trace_callback,
            )
            run["quality"] = _quality_snapshot(plan, request.travel_days)
            run["outcome"] = "success"
        except Exception as exc:  # 报告失败类型，后续运行仍继续，避免一个上游波动中止全批。
            run["error_type"] = type(exc).__name__
            run["error"] = str(exc)[:200]
        finally:
            run["plan_total_seconds"] = clock() - started_at
            run["first_progress_seconds"] = first_progress
            run["first_llm_token_from_plan_start_seconds"] = first_token
            run["amap_fact_cache"] = _counter_delta(cache_before, _amap_cache_snapshot(planner))
            reports.append(run)
            total_latencies.append(run["plan_total_seconds"])
            if first_progress is not None:
                first_progress_latencies.append(first_progress)
            if first_token is not None:
                first_token_from_start.append(first_token)

    successful = [item for item in reports if item["outcome"] == "success"]
    qualities = [item["quality"] for item in successful]
    after_usage = _usage_snapshot()
    cache_totals: dict[str, int] = {}
    for run in reports:
        for key, value in run.get("amap_fact_cache", {}).items():
            cache_totals[key] = cache_totals.get(key, 0) + int(value)
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
            "deterministic_score_mean": (
                sum(float(item["deterministic_score"]) for item in qualities) / len(qualities)
                if qualities else 0.0
            ),
            "deterministic_pass_rate": (
                sum(bool(item["deterministic_passed"]) for item in qualities) / len(qualities)
                if qualities else 0.0
            ),
        },
        "timings": {
            "plan_total": summarize_latencies(total_latencies),
            "first_progress": summarize_latencies(first_progress_latencies),
            "first_llm_token_from_plan_start": summarize_latencies(first_token_from_start),
            "per_day_llm_ttft": summarize_latencies(day_ttft_latencies),
            **{stage: summarize_latencies(values) for stage, values in sorted(stage_latencies.items())},
        },
        "model_usage": _usage_delta(before_usage, after_usage),
        "amap_fact_cache": cache_totals,
        "details": reports,
        "limitations": [
            "这是低频功能评测，不能用作并发压测或线上 SLA。",
            "estimated_cost_usd 仅在部署环境配置模型单价且供应商返回 usage 时有效。",
            "first_progress 是工作流阶段事件；first_llm_token_from_plan_start 才是从规划开始到首个模型 token。",
            "确定性质量分校验天数、餐饮、时长与可信 POI 等规则，不等同于主观行程满意度或最终问答事实正确率。",
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
        f"- 确定性质量分均值：{report['quality']['deterministic_score_mean']:.1f}/100",
        f"- 确定性质量通过率：{report['quality']['deterministic_pass_rate']:.2%}",
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
    ])
    cache_stats = report.get("amap_fact_cache")
    if cache_stats:
        lines.extend([
            "",
            "## 高德事实缓存",
            "",
            f"- POI 命中 / 未命中：{cache_stats.get('poi_hits', 0)} / {cache_stats.get('poi_misses', 0)}",
            f"- 天气命中 / 未命中：{cache_stats.get('weather_hits', 0)} / {cache_stats.get('weather_misses', 0)}",
        ])
    lines.extend([
        "",
        "## 解释边界",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ])
    return "\n".join(lines) + "\n"


def combine_single_run_reports(reports: list[dict]) -> dict:
    """汇总同一请求的独立单次报告，保留 n=3 的原始样本口径。

    发生瞬时供应商网络波动时，独立单次运行比一个长进程更容易保留已完成
    的样本。该函数仅接收 ``runs=1`` 且请求完全相同的报告，避免把不同
    城市、日期或配置混成一条基线。
    """
    if not reports:
        raise ValueError("至少需要一份单次报告")
    reference = reports[0]
    if any(report.get("runs") != 1 for report in reports):
        raise ValueError("只能汇总 runs=1 的独立报告")
    if any(report.get("request") != reference.get("request") for report in reports[1:]):
        raise ValueError("待汇总报告的请求必须完全一致")

    details: list[dict] = []
    successful: list[dict] = []
    total_latencies: list[float] = []
    first_progress_latencies: list[float] = []
    first_token_latencies: list[float] = []
    stage_latencies: dict[str, list[float]] = {}
    model_usage = {key: 0.0 for key in reference["model_usage"]}
    cache_totals: dict[str, int] = {}

    for index, report in enumerate(reports, start=1):
        detail = dict(report["details"][0])
        detail["run"] = index
        details.append(detail)
        total_latencies.append(float(detail["plan_total_seconds"]))
        if detail.get("first_progress_seconds") is not None:
            first_progress_latencies.append(float(detail["first_progress_seconds"]))
        if detail.get("first_llm_token_from_plan_start_seconds") is not None:
            first_token_latencies.append(float(detail["first_llm_token_from_plan_start_seconds"]))
        for stage, summary in report["timings"].items():
            if stage in {"plan_total", "first_progress", "first_llm_token_from_plan_start"}:
                continue
            if summary.get("samples"):
                stage_latencies.setdefault(stage, []).append(float(summary["mean_seconds"]))
        for key, value in report["model_usage"].items():
            model_usage[key] += float(value)
        for key, value in report.get("amap_fact_cache", {}).items():
            cache_totals[key] = cache_totals.get(key, 0) + int(value)
        if detail.get("outcome") == "success":
            successful.append(detail["quality"])

    total_days = sum(int(item["days"]) for item in successful)
    timings = {
        "plan_total": summarize_latencies(total_latencies),
        "first_progress": summarize_latencies(first_progress_latencies),
        "first_llm_token_from_plan_start": summarize_latencies(first_token_latencies),
        **{stage: summarize_latencies(values) for stage, values in sorted(stage_latencies.items())},
    }
    return {
        "mode": reference["mode"],
        "scope": reference["scope"],
        "request": reference["request"],
        "runs": len(reports),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "outcomes": {
            "success": len(successful),
            "error": len(reports) - len(successful),
            "success_rate": len(successful) / len(reports),
        },
        "quality": {
            "days_match_rate": sum(bool(item["days_match"]) for item in successful) / len(successful) if successful else 0.0,
            "trusted_poi_rate": sum(bool(item["trusted_poi"]) for item in successful) / len(successful) if successful else 0.0,
            "fallback_day_rate": sum(int(item["fallback_days"]) for item in successful) / total_days if total_days else 0.0,
            "deterministic_score_mean": sum(float(item["deterministic_score"]) for item in successful) / len(successful) if successful else 0.0,
            "deterministic_pass_rate": sum(bool(item["deterministic_passed"]) for item in successful) / len(successful) if successful else 0.0,
        },
        "timings": timings,
        "model_usage": model_usage,
        "amap_fact_cache": cache_totals,
        "details": details,
        "limitations": [
            *reference["limitations"],
            "该报告由相同请求的独立单次运行汇总；样本数量有限，不能推断线上 SLA。",
        ],
    }


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
    parser.add_argument(
        "--combine-single-runs", type=Path, nargs="+", metavar="REPORT",
        help="汇总多个相同请求、runs=1 的 JSON 报告；不会调用外部服务",
    )
    parser.add_argument("--output", type=Path, default=Path("evals/results/planning_live.json"))
    args = parser.parse_args()
    if not 1 <= args.days <= 30:
        raise ValueError("days 必须在 1 到 30 之间")

    if args.combine_single_runs:
        report = combine_single_run_reports([
            json.loads(path.read_text(encoding="utf-8")) for path in args.combine_single_runs
        ])
    else:
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
