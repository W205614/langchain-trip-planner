package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.*;
import tools.jackson.databind.json.JsonMapper;

class AmapGatewayTest {
  private HttpServer server;
  private final AtomicInteger calls = new AtomicInteger();

  @BeforeEach
  void start() throws Exception {
    server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext("/v3/place/text", exchange -> respond(exchange,
        exchange.getRequestURI().getRawQuery().contains("keywords=limit")
            ? "{\"status\":\"0\",\"info\":\"DAILY_QUERY_OVER_LIMIT\"}"
            : "{\"status\":\"1\",\"pois\":[{\"id\":\"fixture-1\",\"name\":\"可信名称\",\"cityname\":\"北京\",\"address\":\"可信地址\",\"location\":\"116.4,39.9\",\"photos\":[]}]}"));
    server.createContext("/v3/direction/walking", exchange -> respond(exchange,
        exchange.getRequestURI().getRawQuery().contains("120.0%2C39.9")
            ? "{\"status\":\"1\",\"route\":{\"paths\":[]}}"
            : "{\"status\":\"1\",\"route\":{\"paths\":[{\"distance\":\"600\",\"duration\":\"600\"}]}}"));
    server.createContext("/v3/direction/transit/integrated", exchange -> respond(exchange,
        "{\"status\":\"1\",\"route\":{\"transits\":[]}}"));
    server.start();
  }

  @AfterEach
  void stop() { server.stop(0); }

  @Test
  void boundedSearchCacheAndDirectLocationRoutingWork() {
    var gateway = new AmapGateway("http://127.0.0.1:" + server.getAddress().getPort(), "fixture",
        60, 60, 60, 10, "test", "no", JsonMapper.builder().build());
    assertEquals("可信名称", gateway.search("景点", "北京", true).get(0).path("name").asText());
    assertEquals("可信名称", gateway.search("景点", "北京", true).get(0).path("name").asText());
    assertEquals(1, calls.get(), "相同搜索应命中有界缓存");
    var left = JsonMapper.builder().build().createObjectNode().put("longitude", 116.4).put("latitude", 39.9);
    var right = JsonMapper.builder().build().createObjectNode().put("longitude", 116.5).put("latitude", 39.9);
    var route = gateway.routeBetween(left, right, "walking", "北京");
    assertEquals(600, route.path("duration").asInt());
    assertEquals(600, route.path("distance").asInt());
    gateway.routeBetween(left, right, "walking", "北京");
    assertEquals(2, calls.get(), "相同路线应复用短时缓存，不重复请求地图服务");
  }

  @Test
  void rateLimitAndMissingRouteHaveExplicitErrors() {
    var gateway = new AmapGateway("http://127.0.0.1:" + server.getAddress().getPort(), "fixture",
        0, 0, 0, 10, "test", "no", JsonMapper.builder().build());
    var limited = assertThrows(com.tripplanner.api.ApiException.class,
        () -> gateway.search("limit", "北京", true));
    assertEquals(429, limited.status); assertEquals("MAP_RATE_LIMITED", limited.code);
    var left = JsonMapper.builder().build().createObjectNode().put("longitude", 120.0).put("latitude", 39.9);
    var right = JsonMapper.builder().build().createObjectNode().put("longitude", 120.1).put("latitude", 39.9);
    var missing = assertThrows(com.tripplanner.api.ApiException.class,
        () -> gateway.routeBetween(left, right, "walking", "北京"));
    assertEquals(503, missing.status); assertEquals("ROUTE_UNVERIFIED", missing.code);
  }

  @Test
  void nearbyTransitWithoutAnItineraryUsesVerifiedWalkingRoute() {
    var gateway = new AmapGateway("http://127.0.0.1:" + server.getAddress().getPort(), "fixture",
        0, 0, 0, 10, "test", "no", JsonMapper.builder().build());
    var left = JsonMapper.builder().build().createObjectNode().put("longitude", 116.4).put("latitude", 39.9);
    var right = JsonMapper.builder().build().createObjectNode().put("longitude", 116.5).put("latitude", 39.9);
    var route = gateway.routeBetween(left, right, "transit", "北京");
    assertEquals("walking", route.path("route_type").asText());
    assertEquals("transit", route.path("fallback_from").asText());
    assertEquals(600, route.path("distance").asInt());
  }

  @Test
  void missingOrUntrustedPhotoReturnsExplicitPlaceholder() {
    var gateway = new AmapGateway("http://127.0.0.1:" + server.getAddress().getPort(), "fixture",
        0, 0, 0, 10, "test", "no", JsonMapper.builder().build());
    var image = gateway.image("无图景点", "fixture-1", "北京");
    assertTrue(image.placeholder()); assertEquals("image/svg+xml", image.mediaType());
  }

  private void respond(com.sun.net.httpserver.HttpExchange exchange, String body) throws java.io.IOException {
    calls.incrementAndGet();
    byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
    exchange.getResponseHeaders().set("Content-Type", "application/json");
    exchange.sendResponseHeaders(200, bytes.length);
    exchange.getResponseBody().write(bytes);
    exchange.close();
  }
}
