"""高德服务单元测试: 用 monkeypatch 替换 _get, 不发起真实请求"""

import pytest

from app.models.schemas import Location
from app.services.amap_service import AmapService

# 高德真实响应样例 (北京故宫)
AMAP_POI_RESPONSE = {
    "status": "1",
    "count": "1",
    "pois": [
        {
            "id": "B000A8UIN8",
            "name": "故宫博物院",
            "type": "风景名胜;风景名胜;世界遗产",
            "address": "北京市东城区景山前街4号",
            "location": "116.397026,39.918058",
            "tel": "010-85007420",
        }
    ],
}

AMAP_WEATHER_RESPONSE = {
    "status": "1",
    "forecasts": [
        {
            "adcode": "110000",
            "casts": [
                {
                    "date": "2026-08-01",
                    "dayweather": "晴",
                    "nightweather": "多云",
                    "daytemp": "32",
                    "nighttemp": "24",
                    "daywind": "东南风",
                    "daypower": "3",
                }
            ],
        }
    ],
}


@pytest.fixture
def service():
    """构造 AmapService (不发请求, 仅用于测试解析逻辑)"""
    return AmapService()


def test_parse_location():
    """坐标字符串 '经度,纬度' 应解析为 Location"""
    loc = AmapService._parse_location("116.397026,39.918058")
    assert isinstance(loc, Location)
    assert loc.longitude == 116.397026
    assert loc.latitude == 39.918058


def test_parse_location_invalid():
    """非法坐标应抛异常"""
    with pytest.raises(ValueError):
        AmapService._parse_location("invalid")


def test_search_poi_parses_result(service, monkeypatch):
    """search_poi 应把高德响应解析为 POIInfo 列表"""
    monkeypatch.setattr(service, "_get", lambda path, params: AMAP_POI_RESPONSE)

    pois = service.search_poi("故宫", "北京")

    assert len(pois) == 1
    poi = pois[0]
    assert poi.name == "故宫博物院"
    assert poi.address == "北京市东城区景山前街4号"
    assert poi.location.longitude == 116.397026


def test_search_poi_empty_result(service, monkeypatch):
    """高德无结果时返回空列表"""
    monkeypatch.setattr(service, "_get", lambda path, params: {"status": "1", "pois": []})

    pois = service.search_poi("不存在的景点xyz", "北京")
    assert pois == []


def test_search_poi_reuses_short_ttl_cache_without_shared_mutation(service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: calls.append(1) or AMAP_POI_RESPONSE)

    first = service.search_poi("故宫", "北京")
    first[0].name = "被调用方修改"
    second = service.search_poi("故宫", "北京")

    assert len(calls) == 1
    assert second[0].name == "故宫博物院"
    assert service.cache_stats()["poi_hits"] == 1


def test_weather_cache_can_be_disabled(service, monkeypatch):
    service._weather_cache_ttl_seconds = 0
    geocode_calls = []
    monkeypatch.setattr(
        service, "geocode", lambda *_args, **_kwargs: geocode_calls.append(1) or [{"adcode": "110000"}],
    )
    monkeypatch.setattr(service, "_get", lambda *_args, **_kwargs: AMAP_WEATHER_RESPONSE)

    service.get_weather("北京")
    service.get_weather("北京")

    assert len(geocode_calls) == 2


def test_get_weather_parses_result(service, monkeypatch):
    """get_weather 应先地理编码再解析天气预报"""
    # mock 地理编码返回 adcode
    monkeypatch.setattr(
        service,
        "geocode",
        lambda address, city=None: [{"adcode": "110000", "location": "116.4,39.9"}],
    )
    monkeypatch.setattr(service, "_get", lambda path, params: AMAP_WEATHER_RESPONSE)

    weather = service.get_weather("北京")

    assert len(weather) == 1
    assert weather[0].date == "2026-08-01"
    assert weather[0].day_weather == "晴"
    assert weather[0].day_temp == 32  # 字符串 "32" 应被转换为 int


def test_get_weather_no_geocode(service, monkeypatch):
    """地理编码失败时返回空列表"""
    monkeypatch.setattr(service, "geocode", lambda address, city=None: [])

    weather = service.get_weather("不存在城市")
    assert weather == []


def test_plan_route_parses_result(service, monkeypatch):
    """plan_route 应解析路线结果"""
    monkeypatch.setattr(
        service,
        "geocode",
        lambda address, city=None: [{"location": "116.397026,39.918058"}],
    )
    monkeypatch.setattr(
        service,
        "_get",
        lambda path, params: {
            "status": "1",
            "route": {
                "paths": [{"distance": "1200", "duration": "900"}]
            },
        },
    )

    route = service.plan_route("故宫", "天安门")

    assert route["distance"] == 1200
    assert route["duration"] == 900
    assert "公里" in route["description"]


def test_plan_route_by_locations_uses_poi_coordinates_without_geocoding(service, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "geocode", lambda *_args, **_kwargs: pytest.fail("must not geocode"))
    monkeypatch.setattr(
        service,
        "_get",
        lambda path, params: calls.append((path, params)) or {
            "status": "1", "route": {"paths": [{"distance": "800", "duration": "600"}]}
        },
    )

    route = service.plan_route_by_locations(
        Location(longitude=116.39, latitude=39.91),
        Location(longitude=116.40, latitude=39.92),
    )

    assert route["duration"] == 600
    assert calls[0][1]["origin"] == "116.39,39.91"
    assert calls[0][1]["destination"] == "116.4,39.92"


def test_transit_route_parses_transits_and_uses_city(service, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        service,
        "_get",
        lambda path, params: seen.update(path=path, params=params) or {
            "status": "1", "route": {"transits": [{"distance": "1200", "duration": "900"}]}
        },
    )

    route = service.plan_route_by_locations(
        Location(longitude=116.39, latitude=39.91),
        Location(longitude=116.40, latitude=39.92), route_type="transit", city="北京",
    )

    assert route["duration"] == 900
    assert seen["path"] == "/v3/direction/transit/integrated"
    assert seen["params"]["city"] == "北京"
