import json

import pytest

from app.evals.rag_benchmark import (
    RetrievalResult,
    compare_reports,
    evaluate,
    load_dataset,
    markdown_report,
    summarize_latencies,
)


def test_rag_benchmark_calculates_ranked_metrics():
    cases = [{
        "id": "case-1", "query": "故宫", "city": "北京",
        "relevant_chunk_ids": ["beijing.md:0", "beijing.md:2"],
        "expected_facts": ["故宫"],
    }]
    rankings = {"case-1": [
        RetrievalResult("other.md:0", "无关内容", "other.md"),
        RetrievalResult("beijing.md:2", "故宫开放时间", "beijing.md"),
        RetrievalResult("beijing.md:0", "故宫门票", "beijing.md"),
    ]}

    report = evaluate(cases, rankings, k_values=(3,))

    assert report["metrics"]["recall_at_3"] == 1.0
    assert report["metrics"]["precision_at_3"] == 2 / 3
    assert report["metrics"]["mrr_at_3"] == 0.5
    assert report["metrics"]["fact_coverage"] == 1.0
    assert report["category_metrics"]["other"]["cases"] == 1
    assert report["category_metrics"]["other"]["recall_at_3"] == 1.0


def test_dataset_loader_requires_unique_complete_cases(tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps({
        "dataset_id": "test-v1",
        "schema_version": "1",
        "cases": [{
            "id": "same", "query": "故宫", "city": "北京", "relevant_chunk_ids": ["beijing.md:1"],
        }, {
            "id": "same", "query": "长城", "city": "北京", "relevant_chunk_ids": ["beijing.md:2"],
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="重复"):
        load_dataset(dataset)


def test_latency_summary_and_markdown_report_expose_sample_count():
    summary = summarize_latencies([0.1, 0.2, 0.3, 0.4])
    assert summary["samples"] == 4
    assert summary["p50_seconds"] == 0.25
    assert summary["p95_seconds"] == pytest.approx(0.385)

    report = {
        "mode": "live",
        "cases": 20,
        "metrics": {"recall_at_3": 0.8},
        "timings": {"query_embedding": summary},
        "dataset_snapshot": {"dataset_id": "travel-rag-static-v1", "cases_sha256": "abc"},
    }
    rendered = markdown_report(report)
    assert "案例数：20" in rendered
    assert "n=4" in rendered
    assert "最终 LLM 答案正确率" in rendered


def test_report_comparison_rejects_different_snapshots():
    baseline = {"dataset_snapshot": {"cases_sha256": "old"}, "metrics": {"recall_at_3": 0.5}}
    current = {"dataset_snapshot": {"cases_sha256": "new"}, "metrics": {"recall_at_3": 0.8}}

    with pytest.raises(ValueError, match="不一致"):
        compare_reports(baseline, current)
