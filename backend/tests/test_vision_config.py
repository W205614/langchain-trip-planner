from app.config import Settings


def test_vision_model_reads_documented_env_name(monkeypatch):
    monkeypatch.setenv("VISION_MODEL_ID", "deepseek-v4-flash-vision-exp")
    assert Settings().vision_model == "deepseek-v4-flash-vision-exp"


def test_vision_model_defaults_to_deepseek_vision(monkeypatch):
    monkeypatch.delenv("VISION_MODEL_ID", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    assert Settings().vision_model == "deepseek-v4-flash-vision-exp"


def test_empty_vision_model_env_keeps_the_default(monkeypatch):
    monkeypatch.setenv("VISION_MODEL_ID", "")
    assert Settings().vision_model == "deepseek-v4-flash-vision-exp"
