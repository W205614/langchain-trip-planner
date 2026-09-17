"""Generate checked-in wire schemas; does not initialize services or call providers."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent_api.main import Execution, ExecutionResult
from app.models.schemas import TripPlan, TravelResearchRequest, RouteRequest, POISearchRequest, POIDetailRequest
from app.agent_api.contracts import WIRE_MODELS

ROOT = Path(__file__).resolve().parents[2] / "contracts" / "internal-v1"


def generated():
    return {model.__name__: {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
            for model in (Execution, ExecutionResult, TripPlan, TravelResearchRequest, RouteRequest, POISearchRequest, POIDetailRequest,*WIRE_MODELS)}


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, schema in generated().items():
        (ROOT / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
