"""Explicit offline HTTP fixture server. Never imported by the production application."""
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if os.environ.get("APP_ENV") != "validation" or os.environ.get("VALIDATION_ALLOW_FIXTURES") != "yes":
    raise SystemExit("Only run inside the isolated validation stack")

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from app.models.schemas import POIInfo, Location, WeatherInfo
from app.services import amap_service, llm_service
from app.agents import trip_planner_agent


class FixtureMap:
    api_key = "fixture"
    def search_poi(self, keywords, city, *args, **kwargs):
        if city == "无候选":
            return []
        count = 1 if city == "稀疏城市" else 90
        return [POIInfo(id=f"fixture-{i}", name=f"验证景点{i}", type="风景名胜", address=f"{city}地址{i}",
            location=Location(longitude=(118 if city == "路线失败" else 116) + i * 0.001, latitude=39.9)) for i in range(count)]
    def get_weather(self, city):
        if city == "天气失败":
            raise TimeoutError("fixture weather failure")
        return [WeatherInfo(date="2026-09-11", day_weather="晴", day_temp=25)]
    def plan_route_by_locations(self, left, right, **kwargs):
        if left.longitude >= 118:
            raise TimeoutError("fixture route failure")
        return {"distance": 600, "duration": 600}
    def get_poi_photo_by_name(self, name):
        return None


def response(prompt, **kwargs):
    text = prompt.to_messages()[-1].content
    if "fixture:slow" in text:
        import time
        time.sleep(20)
    if "fixture:invalid-json" in text:
        return AIMessage(content="invalid")
    ids = re.findall(r"poi_id=([^\s|]+)", text)
    if "fixture:unknown-poi" in text:
        ids = ["not-in-candidates"]
    draft = {"description": "离线验证行程", "attractions": [
        {"poi_id": item, "visit_duration": 60, "description": "fixture", "ticket_price": 0} for item in ids[:3]],
        "meals": [{"type": kind, "name": kind, "estimated_cost": 20} for kind in ("breakfast", "lunch", "dinner")]}
    return AIMessage(content=json.dumps(draft), usage_metadata={"input_tokens": 100, "output_tokens": 100, "total_tokens": 200})


fixture_map = FixtureMap()
amap_service._amap_service = fixture_map
amap_service.get_amap_service = lambda: fixture_map
trip_planner_agent.get_amap_service = lambda: fixture_map
llm_service.get_llm = lambda timeout=None: RunnableLambda(response)
trip_planner_agent.get_llm = llm_service.get_llm

from app.api.main import app
@app.get("/api/validation/fixture")
def fixture_marker():
    return {"offline_fixture": True}

from app.core.rate_limit import limiter
# Throughput here is fixture throughput, never an upstream performance measurement.
limiter.enabled = False
import uvicorn
uvicorn.run(app, host="0.0.0.0", port=9000)
