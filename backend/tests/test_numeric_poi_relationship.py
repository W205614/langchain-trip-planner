"""Numeric POI identifiers are complete names, not nested venue prefixes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.models.planning import PlanningInput
from app.models.schemas import Attraction, POIInfo
from app.services.time_planner import fact_current
from app.services.travel_facts import relation_pairs, suspected_same_visit


def poi(identity, name):
    return POIInfo(id=identity, name=name, type="风景名胜", address="测试地址",
                   location={"longitude": 116.4, "latitude": 39.9})


@pytest.mark.parametrize(("left", "right"), [
    ("验证景点1", "验证景点10"), ("验证景点11", "验证景点1"),
    ("1号展馆", "11号展馆"), ("展馆1.1", "展馆1.10"),
    ("验证景点１", "验证景点１０"), ("１号展馆", "１１号展馆"),
])
def test_numeric_name_prefixes_do_not_merge_independent_places(left, right):
    assert not suspected_same_visit(poi("left", left), poi("right", right))


@pytest.mark.parametrize(("left", "right"), [
    ("终南山古楼观历史文化景区", "终南山古楼观历史文化景区岳元帅"),
    ("验证景点1", "验证景点1儿童展馆"),
    ("1号展馆", "1号展馆临时展厅"),
    ("验证景点1", "验证景点1"),
    ("验证景点１", "验证景点１儿童展馆"),
])
def test_genuine_containing_names_and_equal_names_keep_same_visit_heuristic(left, right):
    assert suspected_same_visit(poi("left", left), poi("right", right))


def test_same_poi_identity_still_overrides_numbered_aliases():
    visits = [Attraction(poi_id=identity, activity_id=activity, name=name, address="测试地址",
                         location={"longitude": 116.4, "latitude": 39.9},
                         visit_duration=60, description="测试")
              for identity, activity, name in (("one", "a", "验证景点1"), ("ten", "b", "验证景点10"))]
    pairs, unknown = relation_pairs(visits, PlanningInput(), fact_current)
    assert pairs == [] and unknown == set()
    visits[1].poi_id = "one"
    pairs, _ = relation_pairs(visits, PlanningInput(), fact_current)
    assert pairs == [("a", "b")]


def test_actual_validation_fixture_keeps_ninety_choices_for_thirty_days():
    code = '''
import json,runpy
fixture=runpy.run_path("scripts/validation_server.py",run_name="fixture_numeric_test")
from app.agents.candidates import independent_candidate_groups,split_pois_for_days
from app.models.schemas import TripRequest
from app.agents.trip_planner_agent import get_trip_planner_agent
req=TripRequest(city="北京",start_date="2026-09-11",end_date="2026-10-10",travel_days=30,
    transportation="公共交通",accommodation="经济型酒店")
pois=fixture["fixture_map"].search_poi("景点","北京")
groups=independent_candidate_groups(pois,req)
allocated=split_pois_for_days(pois,30,request=req)
print(json.dumps({"raw_pois":len(pois),"independent_groups":len(groups),
    "empty_candidate_days":sum(not items for items in allocated)}),flush=True)
assert len(pois)==len(groups)==90
assert all(allocated)
plan=get_trip_planner_agent().plan_trip(req)
assert len(plan.days)==30 and all(day.attractions for day in plan.days)
identities=[a.poi_id for day in plan.days for a in day.attractions]
assert len(identities)==len(set(identities))==90
print("THIRTY_DAY_NUMERIC_FIXTURE_PASSED")
'''
    env = {**os.environ, "APP_ENV": "validation", "VALIDATION_ALLOW_FIXTURES": "yes",
           "VALIDATION_AGENT_ONLY": "yes", "AMAP_API_KEY": "fixture", "LLM_API_KEY": "fixture",
           "LLM_BASE_URL": "http://127.0.0.1:1", "RAG_ENABLED": "false", "OUTBOUND_LEDGER_REQUIRED": "false"}
    env.pop("FINAL_ACCEPTANCE_RUN_ID", None)
    env.pop("ACCEPTANCE_BUDGET_FILE", None)
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1],
                            env=env, text=True, capture_output=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "THIRTY_DAY_NUMERIC_FIXTURE_PASSED" in result.stdout
