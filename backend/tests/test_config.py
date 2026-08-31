"""运行环境配置校验。"""

import pytest

from app.config import Settings, validate_config


def test_day_planner_keeps_verified_output_default():
    settings = Settings(_env_file=None)

    assert settings.llm_day_max_tokens == 1800


def test_amap_fact_cache_defaults_are_short_and_can_be_disabled():
    settings = Settings(_env_file=None)

    assert settings.amap_poi_cache_ttl_seconds == 900
    assert settings.amap_weather_cache_ttl_seconds == 300
    assert Settings(_env_file=None, amap_poi_cache_ttl_seconds=0).amap_poi_cache_ttl_seconds == 0


def test_development_allows_default_jwt_with_warning(capsys):
    settings = Settings(
        app_env="development",
        AMAP_API_KEY="test-amap",
        LLM_API_KEY="test-llm",
        jwt_secret_key="dev-secret-change-me",
    )
    assert validate_config(settings) is True
    assert "JWT_SECRET_KEY" in capsys.readouterr().out


def test_production_rejects_default_or_short_jwt():
    settings = Settings(
        app_env="production",
        AMAP_API_KEY="test-amap",
        LLM_API_KEY="test-llm",
        jwt_secret_key="too-short",
    )
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        validate_config(settings)


def test_production_requires_llm_configuration():
    settings = Settings(
        app_env="production",
        AMAP_API_KEY="test-amap",
        LLM_API_KEY="",
        jwt_secret_key="x" * 32,
    )
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        validate_config(settings)
