from datetime import date, timedelta

import pytest

from app.agents.candidates import select_day_candidates, split_pois_for_days
from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import DayPlan, POIInfo, TripRequest


def poi(index, required=False):
    return POIInfo(id=str(index), name=f"景点{index}", type="景点", address="北京",
                   location={"longitude": 116 + index / 1000, "latitude": 39.9},
                   requested_names=[f"必去{index}"] if required else [])


def request(days=1):
    start = date(2026, 11, 1)
    return TripRequest(city="北京", start_date=start.isoformat(),
                       end_date=(start + timedelta(days=days-1)).isoformat(), travel_days=days,
                       transportation="公共交通", accommodation="酒店")


def test_required_candidates_beyond_four_remain_in_the_actual_day_prompt_and_whitelist():
    planner = object.__new__(MultiAgentTripPlanner)
    planner._build_day_base_info = lambda *args: ""
    seen = []
    def generate(query, index, current_date, req, state):
        seen.append((query, [p.id for p in state["attraction_pois"]]))
        return DayPlan(day_index=index, date=current_date, description="测试", transportation=req.transportation,
                       accommodation=req.accommodation, attractions=[], meals=[])
    planner._generate_one_day = generate
    candidates = [poi(i, required=True) for i in range(6)] + [poi(6)]
    result = planner._generate_trip_plan({"request": request(), "attraction_pois": candidates})
    assert len(result["trip_plan"].days) == 1
    assert seen[0][1] == [str(i) for i in range(6)]
    assert all(f"poi_id={i} |" in seen[0][0] for i in range(6))
    assert "poi_id=6 |" not in seen[0][0]


def test_confirmed_reservations_and_revision_protection_take_precedence_over_optional_choices():
    req = request()
    req.planning_input.commitments = [{"day_index": 0, "poi_id": "6", "status": "confirmed"},
                                       {"day_index": 0, "poi_id": "5", "status": "cancelled"}]
    candidates = [poi(i) for i in range(7)]
    selected = select_day_candidates(candidates, req, 0, {"4"})
    assert [p.id for p in selected] == ["4", "6", "0", "1"]
    assert len(select_day_candidates(candidates, req, 1)) == 4


@pytest.mark.parametrize("days", [1, 7, 30])
def test_geographic_allocation_and_selection_keep_candidates_unique_and_days_populated(days):
    req = request(days)
    candidates = [poi(i) for i in range(days * 4)]
    allocated = split_pois_for_days(candidates + candidates[:2], days)
    selected = [select_day_candidates(values, req, day) for day, values in enumerate(allocated)]
    assert [len(values) for values in selected] == [4] * days
    identities = [p.id for values in selected for p in values]
    assert len(identities) == len(set(identities)) == days * 4
