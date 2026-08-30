"""离线可靠性评测夹具：不请求高德、LLM 或真实 Chroma。"""

import json
from pathlib import Path


def test_offline_reliability_evaluation_fixture_covers_critical_degradations():
    fixture_path = Path(__file__).resolve().parents[1] / "evals" / "offline_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert {case["id"] for case in cases} == {
        "unknown_poi_rejected",
        "route_duration_repaired",
        "route_unavailable_degraded",
        "daily_llm_timeout_degraded",
        "history_rag_user_isolation",
    }
    assert all(case["description"] for case in cases)
