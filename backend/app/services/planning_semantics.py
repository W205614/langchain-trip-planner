"""Runtime planning contract, mirrored into the Java artifact and checked by tests."""
import json
from pathlib import Path

CONTRACT = json.loads((Path(__file__).parents[1] / "contracts" / "planning-semantics.json").read_text(encoding="utf-8"))
FACT_TTLS = CONTRACT["fact_ttl_seconds"]
