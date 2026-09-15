import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from app.agent_api.main import Execution
from app.agent_api.contracts import WIRE_MODELS

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "internal-v1"


def test_schema_sample_and_runtime_agree():
    schema = json.loads((CONTRACTS / "Execution.schema.json").read_text(encoding="utf-8"))
    sample = json.loads((CONTRACTS / "execution.sample.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(sample)
    parsed = Execution.model_validate(sample)
    assert parsed.protocol_version == 1 and parsed.request.constraints.max_inter_stop_walking_km is None
    actual = Execution.model_json_schema()
    assert {k:v for k,v in schema.items() if k!="$schema"} == actual


def test_bad_version_is_rejected():
    schema = json.loads((CONTRACTS / "Execution.schema.json").read_text(encoding="utf-8"))
    sample = json.loads((CONTRACTS / "execution.sample.json").read_text(encoding="utf-8"))
    sample["protocol_version"] = 2
    assert not Draft202012Validator(schema).is_valid(sample)


def test_every_capability_schema_matches_runtime():
    samples=json.loads((CONTRACTS/"capabilities.samples.json").read_text(encoding="utf-8"))
    for model in WIRE_MODELS:
        schema=json.loads((CONTRACTS/f"{model.__name__}.schema.json").read_text(encoding="utf-8"))
        assert {k:v for k,v in schema.items() if k!="$schema"}==model.model_json_schema()
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(samples[model.__name__])
        model.model_validate(samples[model.__name__])
