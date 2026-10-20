"""Both typed protocol versions must reject the other's execution envelope."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError
from app.agent_api.main import Execution, ExecutionV2


def test_v2_contract_matches_model_and_rejects_v1():
    root=Path(__file__).resolve().parents[2]/"contracts"
    schema=json.loads((root/"internal-v2/ExecutionV2.schema.json").read_text(encoding="utf-8"))
    sample=json.loads((root/"internal-v2/execution.sample.json").read_text(encoding="utf-8"))
    assert {k:v for k,v in schema.items() if k!="$schema"} == ExecutionV2.model_json_schema()
    Draft202012Validator(schema).validate(sample)
    assert ExecutionV2.model_validate(sample).protocol_version == 2
    with pytest.raises(ValidationError):
        Execution.model_validate(sample)
    sample["protocol_version"]=1
    assert not Draft202012Validator(schema).is_valid(sample)
    with pytest.raises(ValidationError):
        ExecutionV2.model_validate(sample)
