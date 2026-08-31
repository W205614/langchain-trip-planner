from unittest.mock import MagicMock

from app.core import trip_metrics


def test_observe_model_call_records_returned_tokens_and_configured_cost(monkeypatch):
    call_seconds = MagicMock()
    input_tokens = MagicMock()
    output_tokens = MagicMock()
    cost = MagicMock()
    monkeypatch.setattr(trip_metrics, "MODEL_CALL_SECONDS", call_seconds)
    monkeypatch.setattr(trip_metrics, "MODEL_INPUT_TOKENS_TOTAL", input_tokens)
    monkeypatch.setattr(trip_metrics, "MODEL_OUTPUT_TOKENS_TOTAL", output_tokens)
    monkeypatch.setattr(trip_metrics, "MODEL_ESTIMATED_COST_USD_TOTAL", cost)

    trip_metrics.observe_model_call(
        "trip_day",
        1.25,
        {"input_tokens": 1_000, "output_tokens": 500},
        input_price_per_million_usd=2.0,
        output_price_per_million_usd=4.0,
    )

    call_seconds.labels.assert_called_once_with(operation="trip_day", outcome="success")
    input_tokens.labels.return_value.inc.assert_called_once_with(1_000.0)
    output_tokens.labels.return_value.inc.assert_called_once_with(500.0)
    cost.labels.return_value.inc.assert_called_once_with(0.004)


def test_usage_tokens_accepts_openai_legacy_field_names():
    assert trip_metrics._usage_tokens({"prompt_tokens": 12, "completion_tokens": 3}) == (12.0, 3.0)


def test_stream_metrics_records_first_visible_event_without_claiming_ttft(monkeypatch):
    first_event = MagicMock()
    total = MagicMock()
    monkeypatch.setattr(trip_metrics, "TRIP_STREAM_FIRST_EVENT_SECONDS", first_event)
    monkeypatch.setattr(trip_metrics, "TRIP_STREAM_TOTAL_SECONDS", total)

    trip_metrics.observe_trip_stream(0.2, 2.5, "success")

    first_event.observe.assert_called_once_with(0.2)
    total.labels.assert_called_once_with(outcome="success")
    total.labels.return_value.observe.assert_called_once_with(2.5)


def test_model_first_token_metric_is_recorded_separately(monkeypatch):
    metric = MagicMock()
    monkeypatch.setattr(trip_metrics, "MODEL_TIME_TO_FIRST_TOKEN_SECONDS", metric)

    trip_metrics.observe_model_first_token("trip_day", 0.35)

    metric.labels.assert_called_once_with(operation="trip_day")
    metric.labels.return_value.observe.assert_called_once_with(0.35)
