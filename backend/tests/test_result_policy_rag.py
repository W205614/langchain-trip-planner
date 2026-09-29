from types import SimpleNamespace

from app.services.result_policy import classify


def test_missing_optional_guide_is_informational_but_retrieval_outage_degrades():
    request = SimpleNamespace(constraints=SimpleNamespace(max_inter_stop_walking_km=None))
    plan = SimpleNamespace(days=[], weather_notice="", enrichment_notices=[
        "部分攻略资料暂无可靠匹配，请以官方信息为准"
    ])
    no_match = classify(plan, request, {"day_checks": []})
    assert no_match["outcome"] == "complete"
    assert [issue["code"] for issue in no_match["issues"]] == ["RAG_NO_MATCH"]

    plan.enrichment_notices = ["向量检索暂不可用，已改用关键词检索；攻略资料可能不完整"]
    fallback = classify(plan, request, {"day_checks": []})
    assert fallback["outcome"] == "degraded"
    assert [issue["code"] for issue in fallback["issues"]] == ["RAG_UNAVAILABLE"]
