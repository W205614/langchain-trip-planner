"""可复现的 RAG 检索评测：固定标注集、真实基线与可比较报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Iterable


DEFAULT_CASES = Path(__file__).resolve().parents[2] / "evals" / "rag_cases.json"
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    text: str
    source: str = ""
    page: int | None = None


def _dcg(relevances: Iterable[int]) -> float:
    from math import log2

    return sum(rel / log2(index + 2) for index, rel in enumerate(relevances))


def _percentile(values: list[float], percentile: float) -> float:
    """线性插值百分位；小样本时由 samples 字段提示其统计局限。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_latencies(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "mean_seconds": mean(values) if values else 0.0,
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": max(values) if values else 0.0,
    }


def score_case(case: dict, results: list[RetrievalResult], k: int) -> dict:
    relevant = set(case["relevant_chunk_ids"])
    ranked = results[:k]
    hits = [item for item in ranked if item.chunk_id in relevant]
    relevances = [1 if item.chunk_id in relevant else 0 for item in ranked]
    first = next((index + 1 for index, value in enumerate(relevances) if value), None)
    ideal = [1] * min(len(relevant), k)
    expected_facts = case.get("expected_facts", [])
    joined_text = "\n".join(item.text for item in ranked)
    fact_coverage = (
        sum(fact in joined_text for fact in expected_facts) / len(expected_facts)
        if expected_facts else 1.0
    )
    return {
        "id": case["id"],
        "category": case.get("category", "other"),
        f"recall_at_{k}": len(hits) / len(relevant) if relevant else 1.0,
        f"precision_at_{k}": len(hits) / k,
        f"mrr_at_{k}": 1 / first if first else 0.0,
        f"ndcg_at_{k}": _dcg(relevances) / _dcg(ideal) if ideal else 1.0,
        # 这是检索片段覆盖标注事实，不等同于最终 LLM 答案正确率。
        "fact_coverage": fact_coverage,
        "source_coverage": sum(bool(item.source) for item in ranked) / len(ranked) if ranked else 0.0,
        "results": [item.__dict__ for item in ranked],
    }


def evaluate(cases: list[dict], rankings: dict[str, list[RetrievalResult]], k_values=(3, 5)) -> dict:
    reports = []
    categories: dict[str, list[dict]] = {}
    for case in cases:
        report = {str(k): score_case(case, rankings.get(case["id"], []), k) for k in k_values}
        reports.append(report)
        categories.setdefault(case.get("category", "other"), []).append(report)
    aggregate: dict[str, float] = {}
    for k in k_values:
        for name in (f"recall_at_{k}", f"precision_at_{k}", f"mrr_at_{k}", f"ndcg_at_{k}"):
            aggregate[name] = mean(report[str(k)][name] for report in reports) if reports else 0.0
    aggregate["fact_coverage"] = mean(report[str(k_values[0])]["fact_coverage"] for report in reports) if reports else 0.0
    aggregate["source_coverage"] = mean(report[str(k_values[0])]["source_coverage"] for report in reports) if reports else 0.0
    category_metrics = {
        category: {
            "cases": len(category_reports),
            **{
                metric: mean(item[str(k_values[-1])][metric] for item in category_reports)
                for metric in (f"recall_at_{k_values[-1]}", f"mrr_at_{k_values[-1]}", f"ndcg_at_{k_values[-1]}")
            },
            "fact_coverage": mean(item[str(k_values[0])]["fact_coverage"] for item in category_reports),
        }
        for category, category_reports in sorted(categories.items())
    }
    return {
        "cases": len(cases), "metrics": aggregate, "category_metrics": category_metrics,
        "category_metric_k": k_values[-1], "details": reports,
    }


def load_dataset(path: Path) -> tuple[list[dict], dict]:
    """读取带版本元数据的标注集，同时兼容早期 JSON 数组格式。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        cases, metadata = raw, {"dataset_id": "legacy-list", "schema_version": "0"}
    elif isinstance(raw, dict) and isinstance(raw.get("cases"), list):
        cases, metadata = raw["cases"], {
            "dataset_id": str(raw.get("dataset_id", "unnamed")),
            "schema_version": str(raw.get("schema_version", "1")),
            "description": str(raw.get("description", "")),
        }
    else:
        raise ValueError("标注集必须是案例数组，或包含 cases 数组的对象")

    seen_ids = set()
    for case in cases:
        missing = [field for field in ("id", "query", "city", "relevant_chunk_ids") if not case.get(field)]
        if missing:
            raise ValueError(f"标注案例缺少字段: {','.join(missing)}")
        if case["id"] in seen_ids:
            raise ValueError(f"标注案例 id 重复: {case['id']}")
        seen_ids.add(case["id"])
    return cases, metadata


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_snapshot(path: Path, metadata: dict) -> dict:
    """记录标注集和静态知识语料摘要，阻止不同数据快照的伪比较。"""
    return {
        "dataset_id": metadata["dataset_id"],
        "schema_version": metadata["schema_version"],
        "cases_sha256": _file_sha256(path),
        "knowledge_files_sha256": {
            item.name: _file_sha256(item)
            for item in sorted(KNOWLEDGE_DIR.glob("*.md"))
        },
    }


def _offline_rankings(cases: list[dict]) -> dict[str, list[RetrievalResult]]:
    """仅供 CI 验证指标计算；不能把 fixture 结果当作线上向量基线。"""
    return {
        case["id"]: [
            RetrievalResult(chunk_id=chunk_id, text=" ".join(case.get("expected_facts", [])), source=chunk_id.split(":")[0])
            for chunk_id in case["relevant_chunk_ids"]
        ]
        for case in cases
    }


def _live_rankings(cases: list[dict], k: int) -> tuple[dict[str, list[RetrievalResult]], dict]:
    from app.services.rag_service import get_rag_service

    rag = get_rag_service()
    if not rag.enabled:
        raise RuntimeError("RAG 未启用，无法生成真实向量基线")
    rag.ensure_knowledge_index()
    rankings: dict[str, list[RetrievalResult]] = {}
    embedding_latencies: list[float] = []
    vector_search_latencies: list[float] = []
    end_to_end_latencies: list[float] = []
    for case in cases:
        started_at = perf_counter()
        embedding_started_at = perf_counter()
        query_embedding = rag._embedding.embed_query(case["query"])
        embedding_latencies.append(perf_counter() - embedding_started_at)

        search_started_at = perf_counter()
        docs = rag._knowledge_store.similarity_search_by_vector(query_embedding, k=k, filter={"city": case["city"]})
        vector_search_latencies.append(perf_counter() - search_started_at)
        end_to_end_latencies.append(perf_counter() - started_at)
        rankings[case["id"]] = [
            RetrievalResult(
                chunk_id=str(doc.metadata.get("chunk_id", "")),
                text=doc.page_content,
                source=str(doc.metadata.get("source", "")),
                page=doc.metadata.get("page"),
            )
            for doc in docs
        ]
    return rankings, {
        "scope": "仅查询 embedding 与公共知识 Chroma 检索；不包含动态建库、历史检索、上下文拼接或 LLM 生成",
        "embedding_model": getattr(rag._embedding, "model", "unknown"),
        "query_embedding": summarize_latencies(embedding_latencies),
        "knowledge_vector_search": summarize_latencies(vector_search_latencies),
        "retrieval_end_to_end": summarize_latencies(end_to_end_latencies),
    }


def compare_reports(baseline: dict, current: dict) -> dict:
    if baseline.get("dataset_snapshot") != current.get("dataset_snapshot"):
        raise ValueError("基线与当前报告的数据快照不一致，拒绝生成不可比结论")
    for setting in ("embedding_model", "top_k"):
        if baseline.get("run", {}).get(setting) != current.get("run", {}).get(setting):
            raise ValueError(f"基线与当前报告的 {setting} 不一致，拒绝生成不可比结论")
    before = baseline.get("metrics", {})
    after = current.get("metrics", {})
    return {
        "baseline_mode": baseline.get("mode", "unknown"),
        "metric_delta": {
            name: after[name] - before[name]
            for name in sorted(set(before) & set(after))
            if isinstance(before[name], (int, float)) and isinstance(after[name], (int, float))
        },
    }


def markdown_report(report: dict) -> str:
    run = report.get("run", {})
    lines = [
        "# RAG 检索评测报告",
        "",
        f"- 模式：`{report['mode']}`",
        f"- 案例数：{report['cases']}",
        f"- 标注集：`{report['dataset_snapshot']['dataset_id']}`",
        f"- 标注集 SHA-256：`{report['dataset_snapshot']['cases_sha256']}`",
        f"- 检索配置：`{run.get('variant', 'unknown')}`，embedding=`{run.get('embedding_model', 'unknown')}`，top-k={run.get('top_k', 'unknown')}",
        "",
        "## 检索质量",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {name} | {value:.4f} |" for name, value in report["metrics"].items())
    lines.extend(["", "## 时延", ""])
    timings = report.get("timings", {})
    if timings.get("scope"):
        lines.extend([f"> 范围：{timings['scope']}", ""])
    for stage, values in timings.items():
        if not isinstance(values, dict):
            continue
        lines.append(
            f"- `{stage}`：n={values['samples']}，平均 {values['mean_seconds']:.4f}s，"
            f"p50 {values['p50_seconds']:.4f}s，p95 {values['p95_seconds']:.4f}s，"
            f"最大 {values['max_seconds']:.4f}s"
        )
    if categories := report.get("category_metrics"):
        category_k = int(report.get("category_metric_k", 5))
        lines.extend(["", "## 分类表现", "", f"| 分类 | 案例数 | Recall@{category_k} | MRR@{category_k} | nDCG@{category_k} | 事实覆盖率 |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
        for category, values in categories.items():
            lines.append(
                f"| {category} | {values['cases']} | {values.get(f'recall_at_{category_k}', 0.0):.4f} | "
                f"{values.get(f'mrr_at_{category_k}', 0.0):.4f} | {values.get(f'ndcg_at_{category_k}', 0.0):.4f} | "
                f"{values['fact_coverage']:.4f} |"
            )
    if comparison := report.get("comparison"):
        lines.extend(["", "## 相对基线变化", "", f"- 基线模式：`{comparison['baseline_mode']}`"])
        lines.extend(f"- `{name}`：{delta:+.4f}" for name, delta in comparison["metric_delta"].items())
    lines.extend([
        "",
        "## 解释边界",
        "",
        "- `fact_coverage` 是召回片段包含标注事实的比例，不是最终 LLM 答案正确率。",
        "- p95 需要足够多且分布稳定的样本；请结合 `n` 解读，不能把小样本结果当作线上 SLA。",
        "- 只有标注集、知识快照、embedding 模型与检索配置一致时，才可比较两份报告。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reproducible RAG retrieval benchmark")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=Path("evals/rag_report.json"))
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--baseline", type=Path, help="同一数据快照的既有 JSON 报告，用于生成指标差异")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--variant", default="dense_chroma", help="本次只改变一个检索因素时的配置名称")
    args = parser.parse_args()
    if args.top_k < 1:
        raise ValueError("top-k 必须大于 0")

    cases, metadata = load_dataset(args.cases)
    if args.mode == "live":
        rankings, timings = _live_rankings(cases, args.top_k)
    else:
        rankings, timings = _offline_rankings(cases), {
            "scope": "离线 fixture，只验证指标与报告格式",
            "embedding_model": "not-called",
        }
    report = evaluate(cases, rankings)
    report["mode"] = args.mode
    report["timings"] = timings
    report["dataset_snapshot"] = dataset_snapshot(args.cases, metadata)
    report["run"] = {
        "variant": args.variant,
        "top_k": args.top_k,
        "embedding_model": timings.get("embedding_model", "unknown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["comparison"] = compare_reports(baseline, report)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output = args.markdown_output or args.output.with_suffix(".md")
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({
        "mode": args.mode,
        "cases": report["cases"],
        "metrics": report["metrics"],
        "timings": timings,
        "output": str(args.output),
        "markdown_output": str(markdown_output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
