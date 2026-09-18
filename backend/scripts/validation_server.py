"""Explicit offline HTTP fixture server. Never imported by the production application."""
import json
import os
import re
import sys
from pathlib import Path
from fastapi import Request
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
    transport = "fixture"
    details = {}

    def close(self):
        pass

    def search_poi(self, keywords, city, *args, **kwargs):
        if city == "无候选":
            return []
        count = 1 if city == "稀疏城市" else 3 if city == "部分失效" else 90
        default_code = {"北京":"beijing", "上海":"shanghai", "稀疏城市":"sparse", "路线失败":"route", "天气失败":"weather", "跨城候选":"cross", "部分失效":"partial"}.get(city, "beijing")
        result = []
        for i in range(count):
            candidate_city = city
            city_code = default_code
            result.append(POIInfo(id=f"fixture-{city_code}-{i}", city=candidate_city, name=f"验证景点{i}", type="风景名胜", address=f"{candidate_city}地址{i}",
                location=Location(longitude=(118 if city == "路线失败" else 116) + i * 0.001, latitude=39.9)))
        self.details.update({item.id: item for item in result})
        return result
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
        time.sleep(max(0.1, min(60.0, float(os.environ.get("VALIDATION_SLOW_SECONDS", "20")))))
    if "fixture:invalid-json" in text:
        return AIMessage(content="invalid")
    ids = re.findall(r"poi_id=([^\s|]+)", text)
    if "fixture:unknown-poi" in text:
        ids = ["not-in-candidates"]
    draft = {"description": "离线验证行程", "attractions": [
        {"poi_id": item, "name": "模型篡改名称", "address": "模型错误地址",
         "location": {"longitude": 0, "latitude": 0}, "visit_duration": 60,
         "description": "fixture", "ticket_price": 9999} for item in ids[:3]],
        "meals": [{"type": kind, "name": kind, "estimated_cost": 20} for kind in ("breakfast", "lunch", "dinner")]}
    return AIMessage(content=json.dumps(draft), usage_metadata={"input_tokens": 100, "output_tokens": 100, "total_tokens": 200})


fixture_map = FixtureMap()
amap_service._amap_service = fixture_map
amap_service.get_amap_service = lambda: fixture_map
trip_planner_agent.get_amap_service = lambda: fixture_map
llm_service.get_llm = lambda timeout=None: RunnableLambda(response)
trip_planner_agent.get_llm = llm_service.get_llm

if os.environ.get("VALIDATION_AGENT_ONLY") == "yes":
    from app.agent_api.main import app
    from app.agent_api import extraction, indexing
    extraction.extract = lambda body: {"pages": [f"## {body['title']}\n来源页: 1\n摘要: 离线资料解析夹具\n- 开放时间请向官方确认"]}
    class FixtureIndex:
        enabled = True
        def add_history_plan(self, *args, **kwargs): return True
        def delete_history_plan(self, *args, **kwargs): return True
        def replace_public_knowledge_document(self, *args, **kwargs): return True
        def delete_public_knowledge_document(self, *args, **kwargs): return True
    indexing.get_rag_service = lambda: FixtureIndex()
elif os.environ.get("VALIDATION_AMAP_ONLY") == "yes":
    from fastapi import FastAPI
    app = FastAPI(title="isolated-amap-rest-fixture")

    @app.get("/readyz")
    def ready():
        return {"status": "ok", "offline_fixture": True}
else:
    raise SystemExit("Select exactly one isolated validation fixture role")
@app.get("/api/validation/fixture")
def fixture_marker():
    return {"offline_fixture": True}

@app.get("/amap-fixture/{path:path}")
def amap_rest_fixture(path: str, request: Request):
    query = dict(request.query_params)
    if path == "v3/place/text":
        city, keyword = query.get("city", "北京"), query.get("keywords", "景点")
        pois = fixture_map.search_poi(keyword, city)[:20]
        return {"status":"1","pois":[{"id":p.id,"name":p.name,"type":p.type,"address":p.address,
            "cityname":p.city,"location":f"{p.location.longitude},{p.location.latitude}","photos":[],"biz_ext":{}} for p in pois]}
    if path == "v3/place/detail":
        identity = query.get("id", "")
        code = identity.split("-")[1] if identity.count("-") >= 2 else "beijing"
        city = "上海" if identity in {"fixture-cross-1", "fixture-partial-1"} else {"beijing":"北京", "shanghai":"上海", "sparse":"稀疏城市", "route":"路线失败", "weather":"天气失败", "cross":"跨城候选", "partial":"部分失效"}.get(code, "北京")
        longitude = 118 if city == "路线失败" else 116.4
        poi = fixture_map.details.get(identity) or POIInfo(id=identity,name="验证景点",type="风景名胜",
            address=f"{city}地址",city=city,location=Location(longitude=longitude,latitude=39.9))
        return {"status":"1","pois":[{"id":poi.id,"name":poi.name,"type":poi.type,"address":poi.address,
            "cityname":poi.city,"location":f"{poi.location.longitude},{poi.location.latitude}","photos":[],"biz_ext":{}}]}
    if path == "v3/geocode/geo":
        return {"status":"1","geocodes":[{"location":"116.4,39.9","adcode":"110000"}]}
    if path == "v3/weather/weatherInfo":
        return {"status":"1","forecasts":[{"casts":[{"date":"2026-09-11","dayweather":"晴","nightweather":"晴","daytemp":"25","nighttemp":"18","daywind":"东","daypower":"3"}]}]}
    if path.startswith("v3/direction/"):
        if query.get("origin", "").startswith("118"):
            return {"status":"1","route":{"paths":[],"transits":[]}}
        return {"status":"1","route":{"paths":[{"distance":"600","duration":"600","walking_distance":"0"}],
            "transits":[{"distance":"600","duration":"600","walking_distance":"200"}]}}
    return {"status":"0","info":"UNKNOWN_FIXTURE_PATH"}

import uvicorn
uvicorn.run(app, host="0.0.0.0", port=9000)
