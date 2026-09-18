import httpx

from scripts import performance_drill


def test_percentile_uses_nearest_rank():
    assert performance_drill.percentile([0.4, 0.1, 0.3, 0.2], 0.50) == 0.2
    assert performance_drill.percentile([0.4, 0.1, 0.3, 0.2], 0.95) == 0.4
    assert performance_drill.percentile([], 0.95) is None


def test_one_shot_reports_status_and_business_code():
    def respond(request):
        return httpx.Response(503, json={"code": "AGENT_CIRCUIT_OPEN"})

    with httpx.Client(
        base_url="http://fixture", transport=httpx.MockTransport(respond)
    ) as client:
        result = performance_drill.one_shot(
            client, [("GET", "/fixture", {}) for _ in range(4)]
        )
    assert result["statuses"] == {"503": 4}
    assert result["codes"] == {"AGENT_CIRCUIT_OPEN": 4}
    assert result["error_rate"] == 1.0
