package com.tripplanner.domain;

import com.github.benmanes.caffeine.cache.*;
import com.tripplanner.api.ApiException;
import java.io.ByteArrayOutputStream;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import tools.jackson.databind.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.*;

/** Java-owned deterministic AMap REST adapter. It never falls back to the Python MCP path. */
@Service
public class AmapGateway {
  public record Image(byte[] body, String mediaType, boolean placeholder) {}

  private final String baseUrl;
  private final String apiKey;
  private final JsonMapper json;
  private final HttpClient http;
  private final Cache<String, JsonNode> poiCache;
  private final Cache<String, JsonNode> weatherCache;
  private final Cache<String, JsonNode> detailCache;
  private final AtomicLong requests = new AtomicLong(), failures = new AtomicLong();

  public AmapGateway(
      @Value("${trip.amap.base-url}") String baseUrl,
      @Value("${trip.amap.api-key:}") String apiKey,
      @Value("${trip.amap.poi-cache-seconds:900}") long poiTtl,
      @Value("${trip.amap.weather-cache-seconds:300}") long weatherTtl,
      @Value("${trip.amap.detail-cache-seconds:3600}") long detailTtl,
      @Value("${trip.amap.cache-maximum-size:1000}") long maximumSize,
      @Value("${APP_ENV:production}") String environment,
      @Value("${VALIDATION_ALLOW_FIXTURES:no}") String validationFixtures,
      JsonMapper json) {
    URI base;
    try {
      base = URI.create(baseUrl);
    } catch (Exception ex) {
      throw new IllegalArgumentException("AMAP_REST_BASE_URL 无效");
    }
    if (!Set.of("https", "http").contains(base.getScheme()) || base.getHost() == null
        || base.getUserInfo() != null || base.getFragment() != null)
      throw new IllegalArgumentException("AMAP_REST_BASE_URL 无效");
    boolean isolatedFixture = "validation".equals(environment) && "yes".equals(validationFixtures);
    if ("http".equals(base.getScheme())
        && !Set.of("localhost", "127.0.0.1", "::1").contains(base.getHost())
        && !isolatedFixture)
      throw new IllegalArgumentException("高德 REST 地址必须使用 HTTPS");
    this.baseUrl = baseUrl.replaceAll("/+$", "");
    this.apiKey = apiKey.strip();
    this.json = json;
    this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5))
        .followRedirects(HttpClient.Redirect.NEVER).build();
    this.poiCache = cache(poiTtl, maximumSize);
    this.weatherCache = cache(weatherTtl, maximumSize);
    this.detailCache = cache(detailTtl, maximumSize);
  }

  private static Cache<String, JsonNode> cache(long ttl, long size) {
    return Caffeine.newBuilder().maximumSize(Math.max(1, size))
        .expireAfterWrite(Math.max(0, ttl), TimeUnit.SECONDS).build();
  }

  public boolean configured() { return !apiKey.isBlank(); }

  public Map<String, Long> metrics() {
    return Map.of("requests", requests.get(), "failures", failures.get(),
        "poi_cache_size", poiCache.estimatedSize(), "weather_cache_size", weatherCache.estimatedSize(),
        "detail_cache_size", detailCache.estimatedSize());
  }

  private JsonNode get(String path, Map<String, ?> params) {
    if (!configured()) throw new ApiException(503, "地图服务未配置", "MAP_NOT_CONFIGURED");
    var query = new StringBuilder();
    var all = new LinkedHashMap<String, Object>(params);
    all.put("key", apiKey);
    all.forEach((key, value) -> {
      if (value == null) return;
      if (!query.isEmpty()) query.append('&');
      query.append(URLEncoder.encode(key, StandardCharsets.UTF_8)).append('=')
          .append(URLEncoder.encode(value.toString(), StandardCharsets.UTF_8));
    });
    try {
      requests.incrementAndGet();
      var request = HttpRequest.newBuilder(URI.create(baseUrl + path + "?" + query))
          .timeout(Duration.ofSeconds(10)).GET().build();
      var response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
      if (response.statusCode() / 100 != 2) throw new ApiException(503, "地图服务暂不可用", "MAP_UPSTREAM_ERROR");
      JsonNode data = json.readTree(response.body());
      if (!"1".equals(data.path("status").asText())) {
        String info = data.path("info").asText("");
        throw new ApiException(info.contains("LIMIT") ? 429 : 503,
            info.contains("LIMIT") ? "地图服务请求过于频繁" : "地图服务暂不可用",
            info.contains("LIMIT") ? "MAP_RATE_LIMITED" : "MAP_UPSTREAM_ERROR");
      }
      return data;
    } catch (ApiException ex) {
      failures.incrementAndGet();
      throw ex;
    } catch (Exception ex) {
      failures.incrementAndGet();
      if (ex instanceof InterruptedException) Thread.currentThread().interrupt();
      throw new ApiException(503, "地图服务连接失败", "MAP_UNAVAILABLE");
    }
  }

  private static String text(JsonNode node, String field) {
    JsonNode value = node.path(field);
    return value.isTextual() || value.isNumber() ? value.asText("") : "";
  }

  private ObjectNode normalizePoi(JsonNode item) {
    var result = json.createObjectNode();
    result.put("id", text(item, "id"));
    result.put("name", text(item, "name"));
    result.put("type", text(item, "type"));
    result.put("address", text(item, "address"));
    result.put("city", text(item, "cityname"));
    result.put("tel", text(item, "tel"));
    var location = result.putObject("location");
    String raw = text(item, "location");
    try {
      String[] parts = raw.split(",", -1);
      location.put("longitude", Double.parseDouble(parts[0]));
      location.put("latitude", Double.parseDouble(parts[1]));
    } catch (Exception ignored) {
      location.put("longitude", 0).put("latitude", 0);
    }
    String opening = "";
    JsonNode ext = item.path("biz_ext");
    for (String name : List.of("opentime2", "open_time", "opentime"))
      if (opening.isBlank()) opening = text(ext, name);
    result.put("opening_hours", opening);
    var photos = result.putArray("photos");
    item.path("photos").forEach(photo -> {
      String url = text(photo, "url");
      if (!url.isBlank()) photos.add(url.replaceFirst("^http://", "https://"));
    });
    return result;
  }

  public ArrayNode search(String keywords, String city, boolean cityLimit) {
    keywords = Objects.toString(keywords, "").strip();
    city = Objects.toString(city, "").strip();
    if (keywords.isBlank() || keywords.length() > 100 || city.isBlank() || city.length() > 64)
      throw new ApiException(422, "景点搜索参数无效");
    String key = keywords.toLowerCase(Locale.ROOT) + "\n" + city.toLowerCase(Locale.ROOT) + "\n" + cityLimit;
    JsonNode cached = poiCache.getIfPresent(key);
    if (cached != null) return (ArrayNode) cached.deepCopy();
    JsonNode data = get("/v3/place/text", Map.of("keywords", keywords, "city", city,
        "citylimit", cityLimit, "offset", 20, "page", 1, "extensions", "all"));
    var result = json.createArrayNode();
    data.path("pois").forEach(item -> {
      ObjectNode poi = normalizePoi(item);
      if (!poi.path("id").asText().isBlank()
          && poi.path("location").path("longitude").asDouble() != 0
          && poi.path("location").path("latitude").asDouble() != 0) result.add(poi);
    });
    poiCache.put(key, result.deepCopy());
    return result;
  }

  public ObjectNode detail(String id) {
    if (id == null || !id.matches("[A-Za-z0-9_-]{1,64}")) throw new ApiException(422, "POI ID 无效");
    JsonNode cached = detailCache.getIfPresent(id);
    if (cached != null) return (ObjectNode) cached.deepCopy();
    JsonNode pois = get("/v3/place/detail", Map.of("id", id, "extensions", "all")).path("pois");
    if (!pois.isArray() || pois.isEmpty()) throw new ApiException(404, "景点不存在", "POI_NOT_FOUND");
    ObjectNode result = normalizePoi(pois.get(0));
    detailCache.put(id, result.deepCopy());
    return result;
  }

  private JsonNode geocode(String address, String city) {
    var params = new LinkedHashMap<String, Object>();
    params.put("address", address);
    if (city != null && !city.isBlank()) params.put("city", city);
    JsonNode rows = get("/v3/geocode/geo", params).path("geocodes");
    return rows.isArray() && !rows.isEmpty() ? rows.get(0) : MissingNode.getInstance();
  }

  public ArrayNode weather(String city) {
    city = Objects.toString(city, "").strip();
    if (city.isBlank() || city.length() > 64) throw new ApiException(422, "城市参数无效");
    JsonNode cached = weatherCache.getIfPresent(city.toLowerCase(Locale.ROOT));
    if (cached != null) return (ArrayNode) cached.deepCopy();
    String adcode = text(geocode(city, city), "adcode");
    var result = json.createArrayNode();
    if (!adcode.isBlank()) {
      JsonNode forecasts = get("/v3/weather/weatherInfo", Map.of("city", adcode, "extensions", "all")).path("forecasts");
      forecasts.forEach(f -> f.path("casts").forEach(c -> result.addObject()
          .put("date", text(c, "date")).put("day_weather", text(c, "dayweather"))
          .put("night_weather", text(c, "nightweather"))
          .put("day_temp", parseInt(text(c, "daytemp"))).put("night_temp", parseInt(text(c, "nighttemp")))
          .put("wind_direction", text(c, "daywind")).put("wind_power", text(c, "daypower"))));
    }
    weatherCache.put(city.toLowerCase(Locale.ROOT), result.deepCopy());
    return result;
  }

  private static int parseInt(String value) {
    try { return Integer.parseInt(value); } catch (Exception ignored) { return 0; }
  }

  public ObjectNode route(JsonNode body) {
    String type = body.path("route_type").asText("walking");
    if (!Set.of("walking", "driving", "transit").contains(type)) throw new ApiException(422, "路线类型无效");
    JsonNode origin = body.path("origin"), destination = body.path("destination");
    String left, right;
    if (origin.isObject() && destination.isObject()) {
      left = coordinate(origin); right = coordinate(destination);
    } else {
      String originAddress = body.path("origin_address").asText("").strip();
      String destinationAddress = body.path("destination_address").asText("").strip();
      if (originAddress.isBlank() || destinationAddress.isBlank()) throw new ApiException(422, "路线地址无效");
      left = text(geocode(originAddress, body.path("origin_city").asText("")), "location");
      right = text(geocode(destinationAddress, body.path("destination_city").asText("")), "location");
    }
    if (left.isBlank() || right.isBlank()) throw new ApiException(503, "无法确认路线端点", "ROUTE_UNVERIFIED");
    return routeCoordinates(left, right, type,
        body.path("city").asText(body.path("origin_city").asText("")),
        body.path("destination_city").asText(""));
  }

  private static String coordinate(JsonNode node) {
    double lon = node.path("longitude").asDouble(Double.NaN), lat = node.path("latitude").asDouble(Double.NaN);
    if (!Double.isFinite(lon) || !Double.isFinite(lat) || lon < -180 || lon > 180 || lat < -90 || lat > 90) return "";
    return lon + "," + lat;
  }

  public ObjectNode routeBetween(JsonNode left, JsonNode right, String type, String city) {
    // PlanRules supplies the normalized location nodes directly.
    String l = coordinate(left), r = coordinate(right);
    if (l.isBlank() || r.isBlank()) throw new ApiException(503, "无法确认路线端点", "ROUTE_UNVERIFIED");
    return routeCoordinates(l, r, type, city, city);
  }

  private ObjectNode routeCoordinates(String left, String right, String type, String city, String destinationCity) {
    String path = switch (type) {
      case "driving" -> "/v3/direction/driving";
      case "transit" -> "/v3/direction/transit/integrated";
      default -> "/v3/direction/walking";
    };
    var params = new LinkedHashMap<String, Object>();
    params.put("origin", left); params.put("destination", right);
    if (type.equals("transit") && city != null && !city.isBlank()) params.put("city", city);
    if (type.equals("transit") && destinationCity != null && !destinationCity.isBlank()) params.put("cityd", destinationCity);
    JsonNode route = get(path, params).path("route");
    JsonNode candidates = type.equals("transit") ? route.path("transits") : route.path("paths");
    if (!candidates.isArray() || candidates.isEmpty()) throw new ApiException(503, "暂无可核验路线", "ROUTE_UNVERIFIED");
    JsonNode first = candidates.get(0);
    double distance;
    int duration;
    try {
      distance = Double.parseDouble(text(first, "distance"));
      duration = Integer.parseInt(text(first, "duration"));
    } catch (Exception ex) {
      throw new ApiException(503, "路线数据不完整", "ROUTE_UNVERIFIED");
    }
    var result = json.createObjectNode().put("distance", distance).put("duration", duration)
        .put("route_type", type).put("description", "全程约" + String.format(Locale.ROOT, "%.1f", distance / 1000) + "公里,预计" + duration / 60 + "分钟");
    if (type.equals("walking")) result.put("walking_distance", distance);
    else if (!text(first, "walking_distance").isBlank()) result.put("walking_distance", Double.parseDouble(text(first, "walking_distance")));
    else result.putNull("walking_distance");
    return result;
  }

  public Image image(String name, String poiId, String city) {
    try {
      ObjectNode poi = poiId != null && !poiId.isBlank() ? detail(poiId)
          : search(name, city == null ? "" : city, city != null && !city.isBlank()).isEmpty() ? null
              : (ObjectNode) search(name, city, true).get(0);
      if (poi == null) return placeholder(name);
      for (JsonNode candidate : poi.path("photos")) {
        URI uri = URI.create(candidate.asText(""));
        String host = Objects.toString(uri.getHost(), "").toLowerCase(Locale.ROOT);
        if (!"https".equals(uri.getScheme()) || !(host.endsWith(".amap.com") || host.endsWith(".autonavi.com"))) continue;
        var response = http.send(HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(8)).GET().build(),
            HttpResponse.BodyHandlers.ofInputStream());
        String media = response.headers().firstValue("Content-Type").orElse("").split(";", 2)[0].strip().toLowerCase(Locale.ROOT);
        if (response.statusCode() / 100 != 2 || !Set.of("image/jpeg", "image/png", "image/webp", "image/gif").contains(media)) continue;
        try (var input = response.body(); var output = new ByteArrayOutputStream()) {
          byte[] buffer = new byte[8192]; int read, total = 0;
          while ((read = input.read(buffer)) >= 0) {
            total += read; if (total > 5 * 1024 * 1024) throw new ApiException(413, "景点图片过大");
            output.write(buffer, 0, read);
          }
          return new Image(output.toByteArray(), media, false);
        }
      }
    } catch (Exception ignored) {
    }
    return placeholder(name);
  }

  private Image placeholder(String name) {
    String safe = Objects.toString(name, "景点").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    if (safe.length() > 24) safe = safe.substring(0, 24);
    String svg = "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='450'><rect width='100%' height='100%' fill='#eef3f8'/><text x='50%' y='48%' text-anchor='middle' font-size='28' fill='#52606d'>" + safe + "</text><text x='50%' y='58%' text-anchor='middle' font-size='18' fill='#7b8794'>暂无可信实景图片</text></svg>";
    return new Image(svg.getBytes(StandardCharsets.UTF_8), "image/svg+xml", true);
  }
}
