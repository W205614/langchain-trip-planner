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
MODEL_CALL_SECONDS = Histogram(
    "ai_model_call_seconds",
    "模型调用耗时（秒）；不包含用户文本或模型名称标签",
    ["operation", "outcome"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 45, 60, 120),
)
MODEL_TIME_TO_FIRST_TOKEN_SECONDS = Histogram(
    "ai_model_time_to_first_token_seconds",
    "模型流式调用从发起到首个非空输出 token 的耗时（秒）",
    ["operation"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
MODEL_INPUT_TOKENS_TOTAL = Counter(
    "ai_model_input_tokens_total",
    "模型调用返回的输入 token 总数",
    ["operation"],
)
MODEL_OUTPUT_TOKENS_TOTAL = Counter(
    "ai_model_output_tokens_total",
    "模型调用返回的输出 token 总数",
    ["operation"],
)
MODEL_ESTIMATED_COST_USD_TOTAL = Counter(
    "ai_model_estimated_cost_usd_total",
    "按部署方显式配置单价估算的模型成本（美元）",
    ["operation"],
)
TRIP_STREAM_FIRST_EVENT_SECONDS = Histogram(
    "trip_stream_time_to_first_event_seconds",
    "从服务接收旅行规划流式请求到首个真实 SSE 进度事件的耗时（不是模型 TTFT）",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
TRIP_STREAM_TOTAL_SECONDS = Histogram(
    "trip_stream_generation_seconds",
    "旅行规划流式生成完成前的服务端耗时（秒）",
    ["outcome"],
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120),
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


def _usage_tokens(usage: dict | None) -> tuple[float, float]:
    """兼容 OpenAI/LangChain 常见 usage 字段；未返回 usage 时不猜测 token。"""
    usage = usage or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    try:
        return max(0.0, float(input_tokens or 0)), max(0.0, float(output_tokens or 0))
    except (TypeError, ValueError):
        return 0.0, 0.0


def observe_model_call(
    operation: str,
    invoke_seconds: float,
    usage: dict | None = None,
    *,
    outcome: str = "success",
    input_price_per_million_usd: float = 0.0,
    output_price_per_million_usd: float = 0.0,
) -> None:
    """记录模型调用、供应商返回 token 和显式单价下的估算成本。"""
    MODEL_CALL_SECONDS.labels(operation=operation, outcome=outcome).observe(invoke_seconds)
    input_tokens, output_tokens = _usage_tokens(usage)
    if input_tokens:
        MODEL_INPUT_TOKENS_TOTAL.labels(operation=operation).inc(input_tokens)
    if output_tokens:
        MODEL_OUTPUT_TOKENS_TOTAL.labels(operation=operation).inc(output_tokens)
    estimated_cost = (
        input_tokens * max(0.0, input_price_per_million_usd)
        + output_tokens * max(0.0, output_price_per_million_usd)
    ) / 1_000_000
    if estimated_cost:
        MODEL_ESTIMATED_COST_USD_TOTAL.labels(operation=operation).inc(estimated_cost)


def observe_model_first_token(operation: str, seconds: float) -> None:
    """仅在供应商通过流式接口返回首个非空 token 时记录。"""
    MODEL_TIME_TO_FIRST_TOKEN_SECONDS.labels(operation=operation).observe(seconds)


def observe_trip_stream(first_event_seconds: float | None, total_seconds: float, outcome: str) -> None:
    """首个真实 SSE 阶段事件是用户可见进度指标，不冒充模型首 token。"""
    if first_event_seconds is not None:
        TRIP_STREAM_FIRST_EVENT_SECONDS.observe(first_event_seconds)
    TRIP_STREAM_TOTAL_SECONDS.labels(outcome=outcome).observe(total_seconds)
