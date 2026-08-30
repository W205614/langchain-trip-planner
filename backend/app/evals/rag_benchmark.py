"""RAG 检索评测。离线模式验证指标；live 模式使用当前 Chroma 与 embedding。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Iterable


DEFAULT_CASES = Path(__file__).resolve().parents[2] / "evals" / "rag_cases.json"


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: str
    text: str
    source: str = ""
    page: int | None = None


def _dcg(relevances: Iterable[int]) -> float:
    from math import log2

    return sum(rel / log2(index + 2) for index, rel in enumerate(relevances))


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
        f"recall_at_{k}": len(hits) / len(relevant) if relevant else 1.0,
        f"precision_at_{k}": len(hits) / k,
        f"mrr_at_{k}": 1 / first if first else 0.0,
        f"ndcg_at_{k}": _dcg(relevances) / _dcg(ideal) if ideal else 1.0,
        "fact_coverage": fact_coverage,
        "source_coverage": sum(bool(item.source) for item in ranked) / len(ranked) if ranked else 0.0,
        "results": [item.__dict__ for item in ranked],
    }


def evaluate(cases: list[dict], rankings: dict[str, list[RetrievalResult]], k_values=(3, 5)) -> dict:
    reports = []
    for case in cases:
        results = rankings.get(case["id"], [])
        reports.append({str(k): score_case(case, results, k) for k in k_values})
    aggregate: dict[str, float] = {}
    for k in k_values:
        names = (f"recall_at_{k}", f"precision_at_{k}", f"mrr_at_{k}", f"ndcg_at_{k}")
        for name in names:
            aggregate[name] = mean(report[str(k)][name] for report in reports) if reports else 0.0
    aggregate["fact_coverage"] = mean(report[str(k_values[0])]["fact_coverage"] for report in reports) if reports else 0.0
    aggregate["source_coverage"] = mean(report[str(k_values[0])]["source_coverage"] for report in reports) if reports else 0.0
    return {"cases": len(cases), "metrics": aggregate, "details": reports}


def _offline_rankings(cases: list[dict]) -> dict[str, list[RetrievalResult]]:
    """仅为 CI 验证指标计算；不应把这个 mock 结果当成线上向量基线。"""
    return {
        case["id"]: [
            RetrievalResult(chunk_id=chunk_id, text=" ".join(case.get("expected_facts", [])), source=chunk_id.split(":")[0])
            for chunk_id in case["relevant_chunk_ids"]
        ]
        for case in cases
    }


def _live_rankings(cases: list[dict], k: int) -> tuple[dict[str, list[RetrievalResult]], dict[str, float]]:
    from app.services.rag_service import get_rag_service

    rag = get_rag_service()
    if not rag.enabled:
        raise RuntimeError("RAG 未启用，无法生成真实向量基线")
    rag.ensure_knowledge_index()
    rankings: dict[str, list[RetrievalResult]] = {}
    latencies = []
    for case in cases:
        started_at = perf_counter()
        query_embedding = rag._embedding.embed_query(case["query"])
        docs = rag._knowledge_store.similarity_search_by_vector(query_embedding, k=k, filter={"city": case["city"]})
        latencies.append(perf_counter() - started_at)
        rankings[case["id"]] = [
            RetrievalResult(
                chunk_id=str(doc.metadata.get("chunk_id", "")),
                text=doc.page_content,
                source=str(doc.metadata.get("source", "")),
                page=doc.metadata.get("page"),
            )
            for doc in docs
        ]
    return rankings, {"retrieval_p50_seconds": sorted(latencies)[len(latencies) // 2], "retrieval_p95_seconds": max(latencies)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG retrieval benchmark")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=Path("evals/rag_report.json"))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.mode == "live":
        rankings, timings = _live_rankings(cases, args.top_k)
    else:
        rankings, timings = _offline_rankings(cases), {"mode": "offline_fixture"}
    report = evaluate(cases, rankings)
    report["mode"] = args.mode
    report["timings"] = timings
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "cases": report["cases"], "metrics": report["metrics"], "timings": timings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
