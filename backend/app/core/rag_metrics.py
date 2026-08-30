"""RAG 检索链路的低基数 Prometheus 指标。"""

from contextlib import contextmanager
from time import perf_counter

from prometheus_client import Counter, Histogram

RAG_OPERATION_SECONDS = Histogram(
    "rag_operation_seconds",
    "RAG 分段耗时（秒）",
    ["stage"],
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
RAG_OPERATION_TOTAL = Counter(
    "rag_operation_total",
    "RAG 分段调用总数",
    ["stage", "outcome"],
)


@contextmanager
def observe_rag_operation(stage: str):
    """记录一个 RAG 分段的耗时、成功或失败，不附带用户文本。"""
    started_at = perf_counter()
    try:
        yield
    except Exception:
        RAG_OPERATION_TOTAL.labels(stage=stage, outcome="error").inc()
        raise
    else:
        RAG_OPERATION_TOTAL.labels(stage=stage, outcome="success").inc()
    finally:
        RAG_OPERATION_SECONDS.labels(stage=stage).observe(perf_counter() - started_at)
