"""Regressions from the Beijing driving itinerary screenshots."""
import pytest
from app.models.schemas import PlanningConstraints, POIInfo, Location
from app.services.attraction_names import resolve_name
from app.services.planning_constraints import finalize_plan, refresh_saved_quality
from app.evals.constraint_benchmark import fixture


def poi(name, id=None):
    return POIInfo(id=id or name, name=name, type='景点', address='北京', location=Location(longitude=116.4,latitude=39.9))


def test_pasted_names_split_before_validation_and_limits():
    assert PlanningConstraints(must_visit=['北京故宫博物馆,天安门，天坛；颐和园、圆明园']).must_visit == ['北京故宫博物馆','天安门','天坛','颐和园','圆明园']
    with pytest.raises(ValueError):
        PlanningConstraints(must_visit=[','.join(str(i) for i in range(9))])
    with pytest.raises(ValueError):
        PlanningConstraints(must_visit=[' '])
    with pytest.raises(ValueError):
        PlanningConstraints(must_visit=['A,B'], avoid=['B'])


@pytest.mark.parametrize('name', ['故宫','北京故宫博物馆','北京市故宫博物院','紫禁城'])
def test_palace_aliases_resolve_main_attraction_not_internal_halls(name):
    result=resolve_name(name,[poi('故宫博物院'),poi('故宫博物院-交泰殿'),poi('故宫博物院(南门)')],'北京')
    assert result.name == '故宫博物院'


def test_area_alias_does_not_claim_tower_ticket_or_other_city():
    assert resolve_name('天安门',[poi('天安门广场')],'北京').name == '天安门广场'
    assert resolve_name('天安门城楼',[poi('天安门广场')],'北京') is None
    assert resolve_name('紫禁城',[poi('故宫博物院')],'台北') is None


def test_ambiguous_branches_and_service_pois_are_not_silently_accepted():
    assert resolve_name('上海博物馆',[poi('上海博物馆(东馆)'),poi('上海博物馆(人民广场馆)')],'上海') is None
    assert resolve_name('故宫博物院',[poi('故宫博物院(南门)')],'北京') is None
    assert resolve_name('迪士尼公园',[poi('上海迪士尼乐园售票处')],'上海') is None


def test_beijing_driving_saved_plan_is_corrected_without_regeneration():
    request,plan,routes=fixture({'days':[['故宫博物院','天安门广场']], 'constraints':{'must_visit':['北京故宫博物馆,天安门']}})
    request.city=plan.city='北京'
    request.transportation='自驾'
    routes.plan_route_by_locations=lambda *a,**kw: {'duration':600,'distance':2000,'route_type':'driving'}
    before=[a.poi_id for a in plan.days[0].attractions]
    report=finalize_plan(plan,request,routes,repair=False)
    assert not any('未满足必去' in w for w in report['warnings'])
    assert report['day_checks'][0]['walking_status']=='not_applicable'
    assert report['day_checks'][0]['inter_stop_walking_km'] is None
    refreshed=refresh_saved_quality(plan,request,report)
    assert refreshed['route_checked']
    assert not any('未满足必去' in w for w in refreshed['warnings'])
    assert before==[a.poi_id for a in plan.days[0].attractions]


@pytest.mark.parametrize('body,expected', [(b'\xff\xd8\xfftest','image/jpeg'),(b'<html>error</html>',None)])
def test_amap_legacy_http_octet_stream_is_upgraded_and_sniffed(monkeypatch,body,expected):
    from app.api.routes import poi as api
    import httpx
    seen=[]
    class Client:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
    class Stream:
        def __enter__(self): return httpx.Response(200,headers={'content-type':'application/octet-stream'},content=body,request=httpx.Request('GET','https://aos-cdn-image.amap.com/sns/x.jpg'))
        def __exit__(self,*args): pass
    def stream(self,method,url):
        seen.append(url);return Stream()
    Client.stream=stream
    monkeypatch.setattr(api.httpx,'Client',Client)
    monkeypatch.setattr(api,'_is_safe_remote_url',lambda u:u.startswith('https://aos-cdn-image.amap.com/sns/'))
    result=api._download_photo('http://aos-cdn-image.amap.com/sns/x.jpg')
    assert seen==['https://aos-cdn-image.amap.com/sns/x.jpg']
    assert result == ((body,expected) if expected else None)

def test_official_amap_comment_cdn_fake_ip_is_scoped(monkeypatch):
    import socket
    from app.api.routes.poi import _is_safe_remote_url
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.2',443))])
    assert _is_safe_remote_url('https://aos-comment.amap.com/B000A83U0P/headerImg/photo.jpg')
    assert _is_safe_remote_url('https://aos-comment.amap.com/B0LAB73CTR/comment/content_media_external_file_100014081_1759506340047_82018524.jpg')
    assert not _is_safe_remote_url('http://aos-comment.amap.com/B0LAB73CTR/comment/photo.jpg')
    assert not _is_safe_remote_url('https://aos-comment.amap.com/B0LAB73CTR/comment/../../admin.jpg')
    assert not _is_safe_remote_url('https://aos-comment.amap.com/B0LAB73CTR/comment/photo.html')
    assert not _is_safe_remote_url('https://aos-comment.amap.com/admin')
    assert not _is_safe_remote_url('https://aos-comment.amap.com.evil.test/B000/headerImg/photo.jpg')
