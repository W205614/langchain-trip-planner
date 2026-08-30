"""旅行规划业务指标，和通用 HTTP 指标互补。"""

from prometheus_client import Counter, Histogram

TRIP_PLAN_TOTAL = Counter(
    "trip_plan_total",
    "旅行规划请求总数",
    ["outcome", "quality"],
)
TRIP_PLAN_QUALITY_SCORE = Histogram(
    "trip_plan_quality_score",
    "确定性质量评分（0-100）",
    buckets=(0, 40, 60, 80, 90, 100),
)
TRIP_PLAN_WARNINGS_TOTAL = Counter(
    "trip_plan_quality_warnings_total",
    "行程质量告警总数",
)
DAILY_LLM_SECONDS = Histogram(
    "trip_daily_llm_seconds",
    "单日 LLM 草稿调用耗时（秒）",
    buckets=(1, 5, 10, 20, 30, 45, 60),
)
DAILY_LLM_DEGRADATIONS_TOTAL = Counter(
    "trip_daily_llm_degradations_total",
    "单日 LLM 降级总数",
    ["reason"],
)


def observe_trip_plan(quality: dict, cached: bool) -> None:
    """记录不含用户、城市、提示词等高基数或敏感标签的聚合指标。"""
    quality_label = "passed" if quality.get("passed") else "warning"
    outcome = "cached" if cached else "generated"
    TRIP_PLAN_TOTAL.labels(outcome=outcome, quality=quality_label).inc()
    TRIP_PLAN_QUALITY_SCORE.observe(float(quality.get("score", 0)))
    TRIP_PLAN_WARNINGS_TOTAL.inc(len(quality.get("warnings", [])))


def observe_daily_llm(invoke_seconds: float, fallback_reason: str | None = None) -> None:
    """只记录低基数耗时与降级原因，不附带用户、城市或模型文本。"""
    DAILY_LLM_SECONDS.observe(invoke_seconds)
    if fallback_reason:
        DAILY_LLM_DEGRADATIONS_TOTAL.labels(reason=fallback_reason).inc()
