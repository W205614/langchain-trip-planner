"""New deployments must not inherit old Docker credentials or acceptance mode."""
import importlib.util
from pathlib import Path

import pytest
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location(
    "prepare_deployment", Path(__file__).resolve().parents[1] / "scripts/prepare_java_deployment.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_new_config_is_normal_mode_and_private(tmp_path, monkeypatch):
    sources = []
    def source(path):
        sources.append(path.name)
        return {"JWT_SECRET_KEY": "", "LLM_API_KEY": "fixture-model-key"}
    monkeypatch.setattr(module, "dotenv_values", source)
    output = tmp_path / "runtime"
    module.prepare(output)
    business = dotenv_values(output / "business.env")
    agent = dotenv_values(output / "agent.env")
    assert sources == [".env"]
    assert len(business["JWT_SECRET_KEY"]) >= 32
    assert business["WORKERS_ENABLED"] == "true"
    assert agent["ACCEPTANCE_BUDGET_FILE"] == ""
    assert agent["INTERNAL_SERVICE_KEY"] == business["INTERNAL_SERVICE_KEY"]
    assert not any(key in agent for key in ("JWT_SECRET_KEY", "POSTGRES_PASSWORD", "DATABASE_URL", "JDBC_DATABASE_URL"))
    before = (output / "business.env").read_bytes()
    with pytest.raises(FileExistsError):
        module.prepare(output)
    assert (output / "business.env").read_bytes() == before


@pytest.mark.parametrize("config", [{"JWT_SECRET_KEY": "weak"}, {"LLM_API_KEY": "bad'quote"}])
def test_invalid_config_writes_nothing(tmp_path, monkeypatch, config):
    monkeypatch.setattr(module, "dotenv_values", lambda _: config)
    output = tmp_path / "runtime"
    with pytest.raises((RuntimeError, ValueError)):
        module.prepare(output)
    assert not output.exists()


def test_upgrade_preserves_credentials_and_adds_split_amap_keys(tmp_path, monkeypatch):
    output = tmp_path / "runtime"
    output.mkdir()
    (output / "postgres.env").write_text("POSTGRES_PASSWORD='existing-db'\n", encoding="utf-8")
    (output / "business.env").write_text(
        "JWT_SECRET_KEY='existing-jwt-secret-with-more-than-32-bytes'\n"
        "INTERNAL_SERVICE_KEY='existing-internal'\nJDBC_DATABASE_URL='jdbc:existing'\n", encoding="utf-8")
    (output / "agent.env").write_text(
        "INTERNAL_SERVICE_KEY='existing-internal'\nLLM_API_KEY='existing-model'\n", encoding="utf-8")
    monkeypatch.setattr(module, "dotenv_values", lambda path: (
        {"AMAP_REST_API_KEY": "rest-key", "AMAP_MCP_API_KEY": "mcp-key"}
        if path.name == ".env" else dotenv_values(path)))
    module.upgrade(output)

    business = dotenv_values(output / "business.env")
    agent = dotenv_values(output / "agent.env")
    assert business["JWT_SECRET_KEY"] == "existing-jwt-secret-with-more-than-32-bytes"
    assert business["JDBC_DATABASE_URL"] == "jdbc:existing"
    assert business["AMAP_REST_API_KEY"] == "rest-key"
    assert agent["INTERNAL_SERVICE_KEY"] == "existing-internal"
    assert agent["LLM_API_KEY"] == "existing-model"
    assert agent["AMAP_API_KEY"] == "mcp-key"
    assert not any(key in agent for key in ("JWT_SECRET_KEY", "POSTGRES_PASSWORD", "DATABASE_URL", "JDBC_DATABASE_URL"))
