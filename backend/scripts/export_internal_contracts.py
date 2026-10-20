"""Generate checked-in wire schemas; does not initialize services or call providers."""
import json
from pathlib import Path
import sys
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent_api.main import Execution, ExecutionResult, ExecutionV2, ExecutionResultV2
from app.models.planning import PlanningInput, DayWindow, CitySegment, RevisionScope
from app.models.schemas import TripPlan, TravelResearchRequest, RouteRequest, POISearchRequest, POIDetailRequest
from app.agent_api.contracts import WIRE_MODELS

ROOT = Path(__file__).resolve().parents[2] / "contracts" / "internal-v1"


def generated(version=1):
    models = (Execution, ExecutionResult, TripPlan, TravelResearchRequest, RouteRequest, POISearchRequest, POIDetailRequest,*WIRE_MODELS)
    if version == 2:
        models = (ExecutionV2, ExecutionResultV2, PlanningInput, DayWindow, CitySegment, RevisionScope, TripPlan)
    return {model.__name__: {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
            for model in models}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=(1,2),type=int,default=1)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    target=args.output or ROOT.parent/f"internal-v{args.version}"
    target.mkdir(parents=True, exist_ok=True)
    for name, schema in generated(args.version).items():
        (target / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
