import logging

from app.core.logging import RedactingFormatter


def test_agent_secrets_are_redacted(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_service_key", "fixture-internal-secret")
    monkeypatch.setattr(settings, "vision_api_key", "fixture-vision-secret")
    record = logging.LogRecord("test", logging.INFO, "", 0,
        "fixture-internal-secret fixture-vision-secret", (), None)
    assert RedactingFormatter("%(message)s").format(record) == "[REDACTED] [REDACTED]"
