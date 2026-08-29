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


def observe_trip_plan(quality: dict, cached: bool) -> None:
    """记录不含用户、城市、提示词等高基数或敏感标签的聚合指标。"""
    quality_label = "passed" if quality.get("passed") else "warning"
    outcome = "cached" if cached else "generated"
    TRIP_PLAN_TOTAL.labels(outcome=outcome, quality=quality_label).inc()
    TRIP_PLAN_QUALITY_SCORE.observe(float(quality.get("score", 0)))
    TRIP_PLAN_WARNINGS_TOTAL.inc(len(quality.get("warnings", [])))
