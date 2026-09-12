"""Adapt official Amap MCP tools to the existing typed travel service contract."""
from copy import deepcopy
import logging

from pydantic import ValidationError

from ..config import get_settings
from ..models.schemas import WeatherInfo
from .amap_service import AmapService
from .amap_mcp_client import AmapMCPClient, AmapMCPError

logger = logging.getLogger(__name__)


def normalize_poi(item: dict) -> dict:
    """MCP detail flattens photo/opening fields that REST nests."""
    result = dict(item)
    if not result.get("photos") and result.get("photo"):
        result["photos"] = [{"url": result["photo"]}]
    if not isinstance(result.get("biz_ext"), dict):
        result["biz_ext"] = {}
    result["biz_ext"] = {**result["biz_ext"]}
    for key in ("opentime2", "open_time", "opentime"):
        if result.get(key):
            result["biz_ext"][key] = result[key]
    return result


def normalize_route(data: dict, route_type: str) -> dict:
    """Do not use root transit 'distance': it is not the selected itinerary length."""
    route = deepcopy(data.get("route", data))
    if route_type == "transit":
        for transit in route.get("transits", []):
            if transit.get("distance") not in (None, "", []):
                continue
            # The official MCP payload omits per-itinerary distance. Sum only
            # complete, unambiguous segment distances; otherwise leave unknown.
            distance = 0.0
            complete = bool(transit.get("segments"))
            for segment in transit.get("segments", []):
                walking = segment.get("walking") or {}
                lines = (segment.get("bus") or {}).get("buslines") or []
                railway = segment.get("railway") or {}
                if railway.get("name") or railway.get("trip") or segment.get("taxi"):
                    complete = False
                if walking:
                    try:
                        distance += float(walking["distance"])
                    except (KeyError, TypeError, ValueError):
                        complete = False
                if lines:
                    # Multiple bus alternatives can have different lengths.
                    try:
                        lengths = {float(line["distance"]) for line in lines}
                        if len(lengths) != 1:
                            complete = False
                        else:
                            distance += lengths.pop()
                    except (KeyError, TypeError, ValueError):
                        complete = False
                if not walking and not lines:
                    complete = False
            if complete:
                transit["distance"] = distance
    return {"route": route}


class AmapMCPService(AmapService):
    transport = "mcp"

    def __init__(self, mcp_client=None):
        super().__init__()
        settings = get_settings()
        self.mcp_client = mcp_client or AmapMCPClient(settings.amap_mcp_url, self.api_key, settings.amap_mcp_timeout_seconds)
        self._search_limit = settings.amap_mcp_search_limit
        self._detail_cache = {}

    def _detail(self, poi_id: str) -> dict:
        cached = self._read_fact_cache(self._detail_cache, poi_id, self._poi_cache_ttl_seconds, "poi")
        if cached is not None:
            return cached
        data = self.mcp_client.call("maps_search_detail", {"id": poi_id})
        if "pois" in data:
            data = next((poi for poi in data["pois"] if poi.get("id") == poi_id), {})
        if data.get("id") != poi_id:
            raise AmapMCPError("高德 MCP 详情与请求的 POI ID 不一致")
        detail = normalize_poi(data)
        self._write_fact_cache(self._detail_cache, poi_id, detail, self._poi_cache_ttl_seconds)
        return detail

    def _get(self, path: str, params: dict) -> dict:
        # Every supported data request crosses MCP. Never call super()._get().
        if path == "/v3/place/text":
            arguments = {"keywords": params["keywords"], "citylimit": str(params.get("citylimit", "false")).lower() == "true"}
            if params.get("city"):
                arguments["city"] = params["city"]
            data = self.mcp_client.call("maps_text_search", arguments)
            pois = []
            for raw in data.get("pois", [])[:min(int(params.get("offset", self._search_limit)), self._search_limit)]:
                if not raw.get("id"):
                    continue
                item = normalize_poi(raw)
                try:
                    if not item.get("location"):
                        item = {**item, **self._detail(item["id"])}
                    # Reject absent/invalid coordinates instead of REST's (0,0) fallback.
                    location = self._parse_location(item.get("location", ""))
                    if location.longitude == 0 and location.latitude == 0:
                        continue
                except TimeoutError:
                    raise
                except (AmapMCPError, ValueError, TypeError, ValidationError):
                    logger.warning("MCP POI 缺少可验证坐标，已跳过该候选")
                    continue
                pois.append(item)
            return {"pois": pois}
        if path == "/v3/place/detail":
            return {"pois": [self._detail(params["id"])]}
        if path == "/v3/geocode/geo":
            data = self.mcp_client.call("maps_geo", params)
            return {"geocodes": data.get("results", data.get("geocodes", []))}
        route_tools = {
            "/v3/direction/walking": ("maps_direction_walking", "walking"),
            "/v3/direction/driving": ("maps_direction_driving", "driving"),
            "/v3/direction/transit/integrated": ("maps_direction_transit_integrated", "transit"),
        }
        if path in route_tools:
            name, mode = route_tools[path]
            arguments = {key: params[key] for key in ("origin", "destination")}
            if mode == "transit":
                if not params.get("city"):
                    raise AmapMCPError("公交 MCP 工具需要起终点城市")
                arguments.update(city=params["city"], cityd=params.get("cityd") or params["city"])
            return normalize_route(self.mcp_client.call(name, arguments), mode)
        raise AmapMCPError("当前高德 MCP 适配器不支持该操作")

    def get_weather(self, city: str) -> list[WeatherInfo]:
        cache_key = city.strip().lower()
        cached = self._read_fact_cache(self._weather_cache, cache_key, self._weather_cache_ttl_seconds, "weather")
        if cached is not None:
            return cached
        data = self.mcp_client.call("maps_weather", {"city": city})
        forecasts = data.get("forecasts", [])
        casts = [cast for entry in forecasts for cast in entry.get("casts", [entry])]
        weather = [WeatherInfo(date=cast["date"], day_weather=cast["dayweather"], night_weather=cast["nightweather"],
            day_temp=cast["daytemp"], night_temp=cast["nighttemp"], wind_direction=cast["daywind"], wind_power=cast["daypower"])
            for cast in casts]
        self._write_fact_cache(self._weather_cache, cache_key, weather, self._weather_cache_ttl_seconds)
        return weather

    def close(self):
        self.mcp_client.close()
        super().close()
