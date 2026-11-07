import importlib.util
from pathlib import Path
from dotenv import dotenv_values
import pytest
import yaml

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"


def load(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+".py"))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_public_profile_keeps_mail_secrets_out_of_agent(tmp_path,monkeypatch):
    module=load("prepare_java_deployment")
    monkeypatch.setattr(module,"dotenv_values",lambda _: {"LLM_API_KEY":"fixture-model",
        "SMTP_ENABLED":"true","SMTP_HOST":"smtp.example.invalid","SMTP_FROM":"test@example.invalid",
        "SMTP_PASSWORD":"fixture-mail-secret","PUBLIC_BASE_URL":"https://trip.example.invalid","ACME_EMAIL":"admin@example.invalid"})
    output=tmp_path/"runtime"
    module.prepare(output,public=True)
    business=dotenv_values(output/"business.env");agent=dotenv_values(output/"agent.env")
    assert business["PUBLIC_REGISTRATION_MODE"]=="email_verified"
    assert business["APP_ENV"]==agent["APP_ENV"]=="production"
    assert agent["OUTBOUND_LEDGER_REQUIRED"]=="true"
    assert agent["LLM_MODEL_ID"]=="gpt-6.1-sol"
    assert "SMTP_PASSWORD" not in agent and "POSTGRES_PASSWORD" not in agent
    assert dotenv_values(output/"public.env")["PUBLIC_HOSTNAME"]=="trip.example.invalid"


@pytest.mark.parametrize("origin",["http://trip.example.invalid","https://:password@trip.example.invalid"])
def test_invalid_public_configuration_writes_nothing(tmp_path,monkeypatch,origin):
    module=load("prepare_java_deployment")
    monkeypatch.setattr(module,"dotenv_values",lambda _: {"PUBLIC_BASE_URL":origin})
    with pytest.raises(ValueError):module.prepare(tmp_path/"runtime",public=True)
    assert not (tmp_path/"runtime").exists()


def test_external_alert_config_preserves_local_receiver_and_private_password(tmp_path):
    module=load("prepare_external_alerts")
    source=tmp_path/"source.env"
    source.write_text("SMTP_HOST=smtp.invalid\nSMTP_FROM=sender@example.invalid\nSMTP_USERNAME=test\nSMTP_PASSWORD=fixture-secret\nALERT_EMAIL_TO=receiver@example.invalid\n")
    output=tmp_path/"private"/"alerts.yml"
    result=module.prepare(source,output)
    assert result["configured"] and not result["connection_checked"]
    config=yaml.safe_load(output.read_text())
    assert [route["receiver"] for route in config["route"]["routes"]]==["local-durable-inbox","external"]
    assert "fixture-secret" not in str(result)
    with pytest.raises(ValueError):module.prepare(source,output)


@pytest.mark.parametrize("url", ["https://:fixture-secret@example.invalid/alerts", "https://user@example.invalid/alerts"])
def test_external_alert_rejects_embedded_credentials_before_writing(tmp_path, url):
    module=load("prepare_external_alerts")
    source=tmp_path/"source.env"
    source.write_text("ALERT_WEBHOOK_URL="+url+"\n")
    output=tmp_path/"alerts.yml"
    with pytest.raises(ValueError,match="without embedded credentials"):
        module.prepare(source,output)
    assert not output.exists()
