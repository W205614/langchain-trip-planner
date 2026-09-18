package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import com.tripplanner.api.ApiException;
import com.tripplanner.agent.AgentClient;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class AgentClientTest {
  @Test
  void agentCapacityRejectionDoesNotOpenTheDependencyCircuit() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    var calls = new AtomicInteger();
    server.createContext(
        "/internal/v1/capabilities/research",
        exchange -> {
          int call = calls.incrementAndGet();
          byte[] body = (call <= 6 ? "{\"code\":\"AGENT_CAPACITY_REJECTED\"}" : "{}")
              .getBytes(StandardCharsets.UTF_8);
          exchange.sendResponseHeaders(call <= 6 ? 429 : 200, body.length);
          exchange.getResponseBody().write(body);
          exchange.close();
        });
    server.start();
    try {
      var client =
          new AgentClient(
              "http://127.0.0.1:" + server.getAddress().getPort(), "test-key", new JsonMapper());
      for (int i = 0; i < 6; i++) {
        var error = assertThrows(ApiException.class, () -> client.post("/capabilities/research", Map.of()));
        assertEquals("AGENT_BUSY", error.code);
      }
      assertNotNull(client.post("/capabilities/research", Map.of()));
      assertEquals(7, calls.get());
    } finally {
      server.stop(0);
    }
  }

  @Test
  void preservesStreamEventsWithoutHttp2UpgradeOrAutomaticRetry() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    var calls = new AtomicInteger();
    server.createContext(
        "/internal/v1/executions",
        exchange -> {
          calls.incrementAndGet();
          assertNull(exchange.getRequestHeaders().getFirst("Upgrade"));
          assertEquals("test-key", exchange.getRequestHeaders().getFirst("X-Service-Key"));
          assertEquals("trace-test", exchange.getRequestHeaders().getFirst("X-Request-ID"));
          byte[] body =
              ": heartbeat\n\nevent: progress\ndata: {\"percent\":10}\n\nevent: result\ndata: {\"execution_id\":\"test\"}\n\n"
                  .getBytes(StandardCharsets.UTF_8);
          exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
          exchange.sendResponseHeaders(200, body.length);
          exchange.getResponseBody().write(body);
          exchange.close();
        });
    server.start();
    try {
      var client =
          new AgentClient(
              "http://127.0.0.1:" + server.getAddress().getPort(), "test-key", new JsonMapper());
      var events = new ArrayList<String>();
      client.generate(
          Map.of("request_id", "trace-test"),
          Duration.ofSeconds(3),
          (event, data) -> events.add(event));
      assertEquals(List.of("progress", "result"), events);
      assertEquals(1, calls.get());
    } finally {
      server.stop(0);
    }
  }

  @Test
  void disconnectedStreamFailsWithoutRegenerating() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    var calls = new AtomicInteger();
    server.createContext(
        "/internal/v1/executions",
        exchange -> {
          calls.incrementAndGet();
          byte[] body = "event: progress\ndata: {}\n\n".getBytes(StandardCharsets.UTF_8);
          exchange.sendResponseHeaders(200, body.length);
          exchange.getResponseBody().write(body);
          exchange.close();
        });
    server.start();
    try {
      var client =
          new AgentClient(
              "http://127.0.0.1:" + server.getAddress().getPort(), "test-key", new JsonMapper());
      assertThrows(
          java.io.EOFException.class,
          () -> client.generate(Map.of(), Duration.ofSeconds(3), (event, data) -> {}));
      assertEquals(1, calls.get());
    } finally {
      server.stop(0);
    }
  }

  @Test
  void genericCapabilityStreamForwardsTokenAndTerminalResult() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/internal/v1/capabilities/research/stream",
        exchange -> {
          byte[] body = ("event: token\ndata: {\"delta\":\"预约\"}\n\n"
              + "event: result\ndata: {\"answer\":\"预约\"}\n\n")
              .getBytes(StandardCharsets.UTF_8);
          exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
          exchange.sendResponseHeaders(200, body.length);
          exchange.getResponseBody().write(body);
          exchange.close();
        });
    server.start();
    try {
      var client = new AgentClient(
          "http://127.0.0.1:" + server.getAddress().getPort(), "test-key", new JsonMapper());
      var events = new ArrayList<String>();
      client.stream("/capabilities/research/stream", Map.of(), Duration.ofSeconds(3),
          (event, data) -> events.add(event + ":" + data.path("answer").asText(data.path("delta").asText(""))));
      assertEquals(List.of("token:预约", "result:预约"), events);
    } finally {
      server.stop(0);
    }
  }
}
