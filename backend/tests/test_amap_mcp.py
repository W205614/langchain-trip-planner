"""Offline MCP contract and real Streamable HTTP session tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import socket
from threading import Thread
import time
from unittest.mock import Mock

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from app.models.schemas import Location
from app.services.amap_mcp_client import AmapMCPClient, AmapMCPError, decode_tool_result
from app.services.amap_mcp_service import AmapMCPService, normalize_route
from app.services.execution import execution_deadline


@pytest.fixture
def service():
    gateway = Mock()
    service = AmapMCPService(gateway)
    yield service
    service.close()


def test_search_fetches_details_preserves_facts_and_caches(service):
    def call(name, args):
        if name == "maps_text_search":
            assert args == {"keywords": "博物馆", "city": "北京", "citylimit": True}
            return {"pois": [{"id": "P1", "name": "博物馆", "photo": "https://example.test/photo"}]}
        assert name == "maps_search_detail" and args == {"id": "P1"}
        return {"id": "P1", "name": "博物馆", "type": "博物馆", "address": "北京", "location": "116.4,39.9",
                "photo": "https://example.test/photo", "opentime2": "09:00-17:00"}
    service.mcp_client.call.side_effect = call
    first = service.search_poi("博物馆", "北京")
    assert first[0].location.longitude == 116.4
    assert first[0].opening_hours == "09:00-17:00"
    assert first[0].photos == ["https://example.test/photo"]
    first[0].name = "mutated"
    assert service.search_poi("博物馆", "北京")[0].name == "博物馆"
    assert service.get_poi_detail("P1")["id"] == "P1"
    assert service.mcp_client.call.call_count == 2


@pytest.mark.parametrize("detail", [{"id": "P1"}, {"id": "WRONG", "location": "116.4,39.9"}, {"id": "P1", "location": "0,0"}])
def test_missing_or_mismatched_coordinates_are_not_fabricated(service, detail):
    service.mcp_client.call.side_effect = [{"pois": [{"id": "P1", "name": "候选"}]}, detail]
    assert service.search_poi("候选", "北京") == []


def test_weather_uses_city_directly_and_caches_flat_forecasts(service):
    service.mcp_client.call.return_value = {"forecasts": [{"date": "2026-09-12", "dayweather": "晴", "nightweather": "晴",
        "daytemp": "27", "nighttemp": "17", "daywind": "南", "daypower": "1-3"}]}
    assert service.get_weather("北京")[0].day_temp == 27
    service.get_weather("北京")
    service.mcp_client.call.assert_called_once_with("maps_weather", {"city": "北京"})


def test_transit_distance_is_derived_only_from_complete_segments():
    data = {"distance": "999", "transits": [{"duration": "600", "walking_distance": "50", "segments": [
        {"walking": {"distance": "50"}, "bus": {"buslines": [{"distance": "400"}]}}]}]}
    assert normalize_route(data, "transit")["route"]["transits"][0]["distance"] == 450
    assert "distance" not in data["transits"][0]  # Never mutate source/cache.
    data["transits"][0]["segments"][0]["bus"]["buslines"][0].pop("distance")
    assert "distance" not in normalize_route(data, "transit")["route"]["transits"][0]


def test_missing_route_keeps_existing_short_walk_fallback(service):
    service.mcp_client.call.side_effect = [{"transits": []}, {"route": {"paths": [{"distance": 1000, "duration": 800}]}}]
    route = service.plan_route_by_locations(Location(longitude=116.4, latitude=39.9), Location(longitude=116.41, latitude=39.91), "transit", "北京")
    assert route["fallback_from"] == "transit"
    assert route["walking_distance"] == 1000
    assert service.mcp_client.call.call_args_list[0].args[1]["cityd"] == "北京"


def test_cross_city_transit_preserves_destination_city(service):
    service.mcp_client.call.side_effect = [{"results": [{"location": "116.4,39.9"}]},
        {"results": [{"location": "117.2,39.1"}]}, {"transits": []}]
    service.plan_route("起点", "终点", "北京", "天津", "transit")
    assert service.mcp_client.call.call_args.args[1]["cityd"] == "天津"


def test_mcp_failure_does_not_fall_back_to_rest(service, monkeypatch):
    monkeypatch.setattr(service.client, "get", lambda *a, **kw: pytest.fail("REST fallback is forbidden"))
    service.mcp_client.call.side_effect = AmapMCPError("MCP unavailable")
    with pytest.raises(AmapMCPError):
        service.search_poi("景点", "北京")


@pytest.mark.parametrize("result", [
    CallToolResult(content=[TextContent(type="text", text="not JSON")]),
    CallToolResult(content=[TextContent(type="text", text='{"status":"0","info":"key=secret"}')]),
    CallToolResult(isError=True, content=[TextContent(type="text", text="key=secret")]),
])
def test_invalid_results_raise_without_exposing_server_text(result):
    with pytest.raises(AmapMCPError) as exc:
        decode_tool_result(result)
    assert "secret" not in str(exc.value)


@pytest.fixture
def mcp_server():
    server = FastMCP("test-amap", stateless_http=True, json_response=True)

    @server.tool()
    async def echo(city: str, delay: float = 0) -> dict:
        await asyncio.sleep(delay)
        return {"city": city}

    @server.tool()
    async def broken() -> dict:
        raise ValueError("upstream error")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), log_level="critical"))
    thread = Thread(target=app.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not app.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert app.started
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        app.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_real_protocol_discovery_schema_parallel_calls_and_shutdown(mcp_server):
    client = AmapMCPClient(mcp_server, "test-key", timeout=3)
    try:
        assert {tool["name"] for tool in client.tools()} == {"echo", "broken"}
        session = client._session
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda city: client.call("echo", {"city": city}), ["北京", "上海", "天津"]))
        assert [r["city"] for r in results] == ["北京", "上海", "天津"]
        assert client._session is session
        with pytest.raises(AmapMCPError, match="参数"):
            client.call("echo", {})
        with pytest.raises(AmapMCPError, match="缺少工具"):
            client.call("missing", {})
        with pytest.raises(AmapMCPError):
            client.call("broken", {})
        with execution_deadline(datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=0.1)):
            with pytest.raises(TimeoutError):
                client.call("echo", {"city": "北京", "delay": 2})
    finally:
        client.close()
    assert client._portal is None
    assert client._session is None


def test_invalid_endpoint_is_rejected():
    with pytest.raises(ValueError):
        AmapMCPClient("http://example.com/mcp", "secret")
