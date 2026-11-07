from app.config import Settings


def test_vision_model_reads_documented_env_name(monkeypatch):
    monkeypatch.setenv("VISION_MODEL_ID", "gpt-6.1-sol")
    assert Settings().vision_model == "gpt-6.1-sol"


def test_vision_model_defaults_to_openai_vision(monkeypatch):
    monkeypatch.delenv("VISION_MODEL_ID", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    assert Settings().vision_model == "gpt-6.1-sol"


def test_empty_vision_model_env_keeps_the_default(monkeypatch):
    monkeypatch.setenv("VISION_MODEL_ID", "")
    assert Settings().vision_model == "gpt-6.1-sol"
