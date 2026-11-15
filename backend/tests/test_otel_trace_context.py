"""Exercise the pinned OpenTelemetry trace-context security boundary."""
from types import SimpleNamespace

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation import tracecontext


def test_oversized_json_traceparent_is_rejected_before_regex(monkeypatch):
    def unexpected_regex(*_args, **_kwargs):
        raise AssertionError("Oversized untrusted traceparent reached regex parsing")

    monkeypatch.setattr(tracecontext, "re", SimpleNamespace(search=unexpected_regex))
    context = Context({"request": "preserved"})
    header = "00-" + "1" * 32 + "-" + "2" * 16 + "-01" + " " * 100_000 + "\n"
    assert tracecontext.TraceContextTextMapPropagator().extract(
        {"traceparent": header}, context=context
    ) is context


def test_valid_trace_context_roundtrip_and_embedded_newline_rejection():
    propagator = tracecontext.TraceContextTextMapPropagator()
    header = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    context = propagator.extract({"traceparent": header})
    assert trace.get_current_span(context).get_span_context().is_valid
    carrier = {}
    propagator.inject(carrier, context=context)
    assert carrier["traceparent"] == header
    invalid = propagator.extract({"traceparent": header + " \n "})
    assert not trace.get_current_span(invalid).get_span_context().is_valid
