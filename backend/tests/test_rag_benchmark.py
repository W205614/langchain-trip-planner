from app.evals.rag_benchmark import RetrievalResult, evaluate


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
