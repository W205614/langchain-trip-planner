from app.config import Settings


def test_vision_model_reads_documented_env_name(monkeypatch):
    monkeypatch.setenv("VISION_MODEL_ID", "deepseek-v4-flash-vision-exp")
    assert Settings().vision_model == "deepseek-v4-flash-vision-exp"
