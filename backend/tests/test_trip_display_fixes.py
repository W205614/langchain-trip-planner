from app.agents.trip_planner_agent import MultiAgentTripPlanner
from app.models.schemas import DayPlanDraft, POIInfo, Location, TripRequest, TripPlan
from app.services.planning_constraints import finalize_plan


def test_required_attraction_hotel_and_facts_survive_empty_model_draft():
    request = TripRequest(city='北京', start_date='2026-09-11', end_date='2026-09-12',
        travel_days=2, transportation='公共交通', accommodation='经济型酒店',
        constraints={'must_visit': ['故宫博物院']})
    poi = POIInfo(id='P1', name='故宫博物院', type='景点', address='北京',
        location=Location(longitude=116.4, latitude=39.9), opening_hours='08:30-17:00',
        photos=['https://example.test/1.jpg'])
    hotel = poi.model_copy(update={'id': 'H1', 'name': '测试酒店'})
    day = MultiAgentTripPlanner._build_day_plan_from_draft(DayPlanDraft(), 0, request.start_date,
        request, {'attraction_pois': [poi], 'hotel_pois': [hotel]})
    assert [a.poi_id for a in day.attractions] == ['P1']
    assert day.attractions[0].opening_hours == '08:30-17:00'
    assert day.attractions[0].photos == poi.photos
    assert day.hotel.name == '测试酒店'
    last = MultiAgentTripPlanner._build_day_plan_from_draft(DayPlanDraft(), 1, request.end_date,
        request, {'attraction_pois': [], 'hotel_pois': [hotel]})
    assert last.hotel is None
    plan = TripPlan(city=request.city, start_date=request.start_date, end_date=request.end_date,
        days=[day, last], overall_suggestions='test')
    finalize_plan(plan, request)
    assert plan.budget.total_attractions == 80
    assert plan.budget.total_hotels == 250
    assert plan.budget.total_transportation == 60
    assert plan.budget.assumptions
    restored = TripPlan.model_validate_json(plan.model_dump_json())
    finalize_plan(restored, request)
    assert restored.budget == plan.budget


def test_explicit_free_ticket_is_not_replaced_by_allowance():
    from app.evals.constraint_benchmark import fixture
    request, plan, routes = fixture({'days': [['A']]})
    plan.days[0].attractions[0].ticket_price = 0
    plan.days[0].attractions[0].price_source = 'model_estimate'
    finalize_plan(plan, request, routes)
    assert plan.budget.total_attractions == 0
    assert plan.budget.total_hotels == 0


def test_real_opening_hours_and_transit_walking_are_retained(monkeypatch):
    from app.services.amap_service import AmapService
    service = AmapService()
    monkeypatch.setattr(service, '_get', lambda *args: {'pois': [{'id': 'P1', 'name': '景点',
        'location': '116.4,39.9', 'biz_ext': {'opentime2': '周二至周日 08:30-17:00'},
        'photos': [{'url': 'https://example.test/1.jpg'}]}]})
    poi = service.search_poi('景点', '北京')[0]
    assert poi.opening_hours == '周二至周日 08:30-17:00'
    assert len(poi.photos) == 1
    monkeypatch.setattr(service, '_get', lambda *args: {'route': {'transits': [
        {'distance': '7500', 'duration': '3800', 'walking_distance': '2800'}]}})
    route = service.plan_route_by_locations(poi.location, poi.location, 'transit', '北京')
    assert route['walking_distance'] == 2800
    monkeypatch.setattr(service, '_get', lambda *args: {'route': {'transits': [{'distance': '7500'}]}})
    assert service.plan_route_by_locations(poi.location, poi.location, 'transit', '北京') == {}


def test_photo_id_tries_second_image_without_name_search(client, monkeypatch):
    from app.api.routes import poi
    from types import SimpleNamespace
    poi._photo_url_cache.clear()
    monkeypatch.setattr(poi, 'get_amap_service', lambda: SimpleNamespace(get_poi_detail=lambda id: {
        'photos': [{'url': 'https://example.test/broken'}, {'url': 'https://example.test/good'}]}))
    monkeypatch.setattr(poi, '_download_photo', lambda url: (b'image', 'image/jpeg') if url.endswith('good') else None)
    monkeypatch.setattr(poi, '_resolve_attraction_photo', lambda name: (_ for _ in ()).throw(AssertionError('wrong lookup')))
    response = client.get('/api/poi/photo/image', params={'name': '故宫', 'poi_id': 'P1', 'city': '北京'})
    assert response.status_code == 200
    assert response.content == b'image'
    poi._photo_url_cache.clear()

def test_shanghai_disney_park_variant_resolves_unique_park_not_resort_or_ticket_office():
    from app.evals.constraint_benchmark import fixture
    park = POIInfo(id='DISNEY', name='上海迪士尼乐园', type='游乐场', address='浦东',
        location=Location(longitude=121.667, latitude=31.144))
    resort = park.model_copy(update={'id': 'RESORT', 'name': '上海迪士尼度假区'})
    ticket = park.model_copy(update={'id': 'TICKET', 'name': '上海迪士尼乐园第1售票处'})
    matched = MultiAgentTripPlanner._resolve_required_poi('上海迪士尼公园', [resort, ticket, park])
    assert matched.id == 'DISNEY'
    assert MultiAgentTripPlanner._resolve_required_poi('上海迪士尼公园', [resort, ticket]) is None
    assert MultiAgentTripPlanner._resolve_required_poi('上海迪士尼公园', [park, park.model_copy(update={'id': 'OTHER'})]) is None
    park.requested_names = ['上海迪士尼公园']
    request, plan, routes = fixture({'days': [['A', 'B']], 'constraints': {'must_visit': ['上海迪士尼公园']}})
    request.city = plan.city = '上海'
    MultiAgentTripPlanner._complete_day(plan.days[0], request, {'attraction_pois': [park]})
    quality = finalize_plan(plan, request, routes)
    assert [a.poi_id for a in plan.days[0].attractions] == ['DISNEY']
    assert plan.days[0].attractions[0].visit_duration == 480
    assert not any('未满足必去' in warning for warning in quality['warnings'])
    restored = TripPlan.model_validate_json(plan.model_dump_json())
    assert not any('未满足必去' in warning for warning in finalize_plan(restored, request, routes)['warnings'])

def test_transit_empty_uses_verified_short_walk_but_not_long_walk(monkeypatch):
    from app.services.amap_service import AmapService
    service = AmapService()
    location = Location(longitude=121.4, latitude=31.2)
    def response(path, params):
        return {'route': {'transits': []}} if 'transit' in path else {
            'route': {'paths': [{'distance': '336', 'duration': '269'}]}}
    monkeypatch.setattr(service, '_get', response)
    route = service.plan_route_by_locations(location, location, 'transit', '上海')
    assert route['route_type'] == 'walking'
    assert route['duration'] == 269 and route['walking_distance'] == 336
    monkeypatch.setattr(service, '_get', lambda path, params: {'route': {'transits': []}} if 'transit' in path
        else {'route': {'paths': [{'distance': '8000', 'duration': '6400'}]}})
    assert service.plan_route_by_locations(location, location, 'transit', '上海') == {}


def test_fake_ip_compatibility_is_limited_to_verified_amap_https_endpoint(monkeypatch):
    import socket
    from app.api.routes.poi import _is_safe_remote_url
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.0.85', 443))])
    assert _is_safe_remote_url('https://store.is.autonavi.com/showpic/photo?type=pic')
    assert not _is_safe_remote_url('https://evil.example/showpic/photo')
    assert not _is_safe_remote_url('http://store.is.autonavi.com/showpic/photo')
    assert not _is_safe_remote_url('https://store.is.autonavi.com:8080/showpic/photo')
    assert not _is_safe_remote_url('https://store.is.autonavi.com/admin')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    assert not _is_safe_remote_url('https://store.is.autonavi.com/showpic/photo')

def test_amap_qps_failure_has_one_bounded_retry(monkeypatch):
    from app.services.amap_service import AmapService
    from types import SimpleNamespace
    service = AmapService()
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        body = {'status': '0', 'info': 'CUQPS_HAS_EXCEEDED_THE_LIMIT'} if len(calls) == 1 else {'status': '1'}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setattr(service.client, 'get', get)
    monkeypatch.setattr('app.services.amap_service.time.sleep', lambda _: None)
    assert service._get('/v3/direction/walking', {}) == {'status': '1'}
    assert len(calls) == 2
