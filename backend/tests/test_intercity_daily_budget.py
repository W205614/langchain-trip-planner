"""The daily elapsed-time cap includes fixed transport and its origin access."""
from datetime import datetime

from test_time_planner import intercity_hotel_scenario, RecordedRoutes, Routes
from app.services.time_planner import solve_day


def test_confirmed_bridge_and_verified_access_consume_daily_budget():
    request, plan = intercity_hotel_scenario()
    request.constraints.daily_minutes = 360
    plan.days[1].attractions[0].visit_duration = 180
    timeline, report, _ = solve_day(plan.days[1], request, RecordedRoutes())
    assert timeline == []
    assert report['status'] == 'infeasible'


def test_fitting_intercity_schedule_respects_whole_day_elapsed_budget():
    request, plan = intercity_hotel_scenario()
    request.constraints.daily_minutes = 360
    timeline, report, _ = solve_day(plan.days[1], request, RecordedRoutes())
    assert timeline and report['status'] == 'unverified'
    span = (max(datetime.fromisoformat(r['end_at']) for r in timeline)
            - min(datetime.fromisoformat(r['start_at']) for r in timeline)).total_seconds() / 60
    assert span <= 360


def test_estimated_access_does_not_prove_a_daily_budget_conflict():
    request, plan = intercity_hotel_scenario()
    request.constraints.daily_minutes = 360
    plan.days[1].attractions[0].visit_duration = 100
    timeline, report, _ = solve_day(plan.days[1], request, Routes(missing=True))
    assert timeline == []
    assert report['status'] == 'unverified'
    assert 'route_duration_estimated_30_minutes' in report['unknown']


def test_outbound_fixed_bridge_end_also_respects_daily_budget():
    request, plan = intercity_hotel_scenario()
    request.constraints.daily_minutes = 360
    bridge = request.planning_input.commitments[0]
    bridge['start_at'] = plan.days[0].date + 'T13:00:00+08:00'
    bridge['end_at'] = plan.days[0].date + 'T18:00:00+08:00'
    timeline, report, _ = solve_day(plan.days[0], request, RecordedRoutes())
    assert timeline == []
    assert report['status'] == 'infeasible'
