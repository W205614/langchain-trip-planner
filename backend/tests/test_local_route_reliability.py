from datetime import datetime, timedelta, timezone

from app.models.schemas import Location
from app.services import time_planner
from test_time_planner import Routes, scenario, intercity_hotel_scenario


def test_anchor_round_trips_are_queried_before_discretionary_pair_edges(monkeypatch):
    monkeypatch.setenv('PLANNER_ROUTE_LOOKUPS', '4')
    request, plan = scenario()
    day = plan.days[0]
    hotel = Location.model_validate(request.planning_input.hotel_stays[0]['location'])
    visits = [a.location for a in day.attractions]
    calls = []

    class Provider:
        def plan_route_by_locations(self, left, right, **kwargs):
            calls.append((left.longitude, right.longitude))
            return {'duration': 600, 'distance': 1000, 'walking_distance': 1000}

    time_planner.solve_day(day, request, Provider())
    assert set(calls) == {(hotel.longitude, p.longitude) for p in visits} | {(p.longitude, hotel.longitude) for p in visits}


def test_an_unused_failed_reverse_route_does_not_expire_a_verified_schedule():
    request, plan = scenario()
    day = plan.days[0]
    first, second = day.attractions
    request.planning_input.opening_windows[0]['windows'] = [{'start': '09:00', 'end': '10:00'}]
    request.planning_input.opening_windows[1]['windows'] = [{'start': '11:00', 'end': '12:00'}]
    cache = {}

    class Provider:
        def plan_route_by_locations(self, left, right, **kwargs):
            if left == second.location and right == first.location:
                return {}
            return {'duration': 600, 'distance': 1000, 'walking_distance': 1000}

    timeline, report, order = time_planner.solve_day(day, request, Provider(), cache)
    assert order == [first.activity_id, second.activity_id]
    assert report['status'] == 'feasible'
    assert not any(gap.startswith('route_') for gap in report['unknown'])
    assert datetime.fromisoformat(report['route_fact_expires_at']).year > 1970
    failed = next(value for key, value in cache.items() if isinstance(key, tuple) and key[:4] == (
        second.location.longitude, second.location.latitude, first.location.longitude, first.location.latitude))
    assert failed[1] is False
    assert all(row['status'] == 'confirmed' for row in timeline if row['kind'] == 'travel')


def test_verified_route_is_preferred_to_an_unknown_shortcut_at_equal_visit_count():
    request, plan = scenario()
    day = plan.days[0]
    first, second = day.attractions

    class Provider:
        def plan_route_by_locations(self, left, right, **kwargs):
            if left == second.location and right == first.location:
                return {}
            return {'duration': 2400 if left == first.location and right == second.location else 600,
                    'distance': 1000, 'walking_distance': 1000}

    _, report, order = time_planner.solve_day(day, request, Provider())
    assert order == [first.activity_id, second.activity_id]
    assert report['status'] == 'feasible'


def test_last_day_without_a_lodging_or_departure_destination_ends_at_the_last_visit():
    request, plan = scenario([['A']])
    day = plan.days[0]
    day.hotel = None
    request.planning_input.hotel_stays = []
    timeline, report, _ = time_planner.solve_day(day, request, Routes())
    assert report['route_minutes'] == 30
    assert timeline[-1]['kind'] == 'attraction'
    assert 'start_endpoint_unknown' in report['unknown']
    assert 'hotel_endpoint_unknown' not in report['unknown']


def test_a_declared_departure_without_a_known_destination_keeps_an_unverified_connection():
    request, plan = scenario([['A']])
    day = plan.days[0]
    day.hotel = None
    request.planning_input.hotel_stays = []
    request.planning_input.departure_at = datetime.fromisoformat(day.date+'T16:00:00+08:00')
    timeline, report, _ = time_planner.solve_day(day, request, Routes())
    assert 'departure_endpoint_unknown' in report['unknown']
    assert timeline[-1]['kind'] == 'travel' and timeline[-1]['status'] == 'estimated'
    assert '离开' in timeline[-1]['title']
    assert timeline[-1]['notes']


def test_a_missing_intermediate_night_is_still_an_explicit_lodging_gap():
    request, plan = scenario([['A'], ['B']])
    plan.days[0].hotel = None
    request.planning_input.hotel_stays = []
    timeline, report, _ = time_planner.solve_day(plan.days[0], request, Routes())
    assert 'hotel_endpoint_unknown' in report['unknown']
    assert timeline[-1]['kind'] == 'travel' and '住宿' in timeline[-1]['title']


def test_origin_connection_expiring_during_later_queries_is_downgraded_without_renewal(monkeypatch):
    from app.services.route_facts import current
    request, plan = intercity_hotel_scenario()
    now = datetime.now(timezone.utc)
    expiry = (now + timedelta(seconds=5)).isoformat()
    clock = {'advanced': False}
    monkeypatch.setattr(time_planner, 'route_current', lambda fact: current(fact, now=now + timedelta(seconds=60 if clock['advanced'] else 0)))
    calls = []

    class Provider:
        def plan_route_by_locations(self, left, right, **kwargs):
            calls.append((left.longitude, right.longitude))
            if len(calls) == 1:
                return {'duration': 600, 'observed_at': now.isoformat(), 'expires_at': expiry}
            clock['advanced'] = True
            return {'duration': 600, 'observed_at': now.isoformat()}

    timeline, report, _ = time_planner.solve_day(plan.days[1], request, Provider(), previous_day=plan.days[0])
    connection = next(row for row in timeline if row['activity_id'] == 'connection:bridge:origin')
    assert report['status'] == 'unverified'
    assert 'route_fact_expired_or_unobserved' in report['unknown']
    assert connection['status'] == 'estimated' and connection['notes']
    assert datetime.fromisoformat(connection['end_at']) - datetime.fromisoformat(connection['start_at']) == timedelta(minutes=10)
    assert report['route_fact_expires_at'] == expiry
    assert calls.count(calls[0]) == 1


def test_a_selected_unsent_route_requires_resume_instead_of_a_completed_unverified_task(monkeypatch):
    monkeypatch.setenv('PLANNER_ROUTE_LOOKUPS', '1')
    request, plan = scenario()
    timeline, report, _ = time_planner.solve_day(plan.days[0], request, Routes())
    assert timeline
    assert 'route_lookup_deferred' in report['unknown']
    assert report['status'] == 'not_found'
    assert report['conflicts'] == ['Resume to continue scheduling deferred routes']


def test_unknown_endpoint_does_not_expire_the_actual_selected_provider_facts():
    request, plan = scenario()
    request.planning_input.hotel_stays=[]
    plan.days[0].hotel=None
    _, report, _ = time_planner.solve_day(plan.days[0], request, Routes())
    assert report['status']=='unverified' and 'route_endpoint_unknown' in report['unknown']
    assert datetime.fromisoformat(report['route_fact_expires_at'])>datetime.now(timezone.utc)
    assert report['route_fact_observed_at'] is not None


def test_unknown_endpoint_without_any_provider_fact_has_no_invented_expiry():
    request, plan = scenario([['A']])
    request.planning_input.hotel_stays=[]
    plan.days[0].hotel=None
    _, report, _ = time_planner.solve_day(plan.days[0], request, Routes())
    assert report['status']=='unverified' and 'route_endpoint_unknown' in report['unknown']
    assert report['route_fact_expires_at'] is None
    assert report['route_fact_observed_at'] is None


def test_selected_failed_provider_fact_still_has_no_valid_lifetime():
    request, plan = scenario()
    _, report, _ = time_planner.solve_day(plan.days[0], request, Routes(missing=True))
    assert report['status']=='unverified'
    assert report['route_fact_expires_at']==time_planner.ROUTE_EPOCH
    assert report['route_fact_observed_at'] is None
