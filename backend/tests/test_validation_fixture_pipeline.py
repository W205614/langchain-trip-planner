"""Exercise the offline provider against the actual planner, not a mock plan."""
import os
from pathlib import Path
import subprocess
import sys


def test_validation_provider_obeys_each_day_whitelist_and_real_categories():
    code='''
import runpy,json
fixture=runpy.run_path("scripts/validation_server.py",run_name="fixture_test")
from app.models.schemas import TripRequest
from app.agents.trip_planner_agent import get_trip_planner_agent
from app.services.planning_constraints import finalize_plan
req=TripRequest(city="北京",start_date="2026-11-01",end_date="2026-11-03",travel_days=3,transportation="公共交通",accommodation="经济型酒店")
planner=get_trip_planner_agent()
plan=planner.plan_trip(req)
quality=finalize_plan(plan,req,planner.amap_service,enhanced=True)
assert quality["outcome"] != "draft",quality
ids=[a.poi_id for day in plan.days for a in day.attractions]
assert len(set(ids))==len(ids)==9,ids
assert all(day.meals and all("restaurant" in meal.poi_id for meal in day.meals) for day in plan.days)
assert all(day.hotel and "酒店" in day.hotel.name for day in plan.days[:-1])
assert plan.days[-1].hotel is None  # no extra accommodation night after departure
assert all(day.timeline for day in plan.days)
assert fixture["fixture_map"].plan_route_by_locations(fixture["Location"](longitude=121.47,latitude=31.23),fixture["Location"](longitude=121.48,latitude=31.23))["duration"] == 600
print("FIXTURE_PIPELINE_PASSED")
'''
    env={**os.environ,"APP_ENV":"validation","VALIDATION_ALLOW_FIXTURES":"yes","VALIDATION_AGENT_ONLY":"yes","AMAP_API_KEY":"fixture","LLM_API_KEY":"fixture","LLM_BASE_URL":"http://127.0.0.1:1","RAG_ENABLED":"false","OUTBOUND_LEDGER_REQUIRED":"false"}
    env.pop("FINAL_ACCEPTANCE_RUN_ID",None)
    env.pop("ACCEPTANCE_BUDGET_FILE",None)
    result=subprocess.run([sys.executable,"-c",code],cwd=Path(__file__).parents[1],env=env,text=True,capture_output=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert "FIXTURE_PIPELINE_PASSED" in result.stdout
