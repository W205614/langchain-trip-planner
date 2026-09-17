package com.tripplanner.agent;

import com.tripplanner.api.ApiException;
import java.io.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.function.BiConsumer;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@Component
public class AgentClient {
  private final String base, key;
  private final JsonMapper json;
  private final HttpClient http =
      HttpClient.newBuilder()
          .version(HttpClient.Version.HTTP_1_1)
          .connectTimeout(Duration.ofSeconds(5))
          .build();

  public AgentClient(
      @Value("${trip.agent-url}") String base,
      @Value("${trip.internal-key}") String key,
      JsonMapper json) {
    this.base = base;
    this.key = key;
    this.json = json;
  }

  private HttpRequest request(String path, Object body, Duration timeout) {
    var builder = HttpRequest.newBuilder(URI.create(base + "/internal/v1" + path))
        .timeout(timeout)
        .header("X-Service-Key", key)
        .header("Content-Type", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body)));
    String requestId = MDC.get("request_id");
    if ((requestId == null || requestId.isBlank()) && body instanceof JsonNode node)
      requestId = node.path("request_id").asText("");
    if ((requestId == null || requestId.isBlank()) && body instanceof java.util.Map<?, ?> values)
      requestId = java.util.Objects.toString(values.get("request_id"), "");
    if (requestId != null && requestId.matches("[A-Za-z0-9_-]{1,64}"))
      builder.header("X-Request-ID", requestId);
    return builder.build();
  }

  public JsonNode post(String path, Object body) {
    int seconds =
        path.endsWith("/cancel")
            ? 2
            : path.equals("/documents/extract") ? 180 : path.equals("/index/rebuild") ? 300 : 30;
    return post(path, body, Duration.ofSeconds(seconds));
  }

  public JsonNode post(String path, Object body, Duration timeout) {
    try {
      var response = http.send(request(path, body, timeout), HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() == 422)
        throw new ApiException(422, "请求参数不符合能力接口契约", "INVALID_REQUEST");
      if (response.statusCode() / 100 != 2) {
        if (response.statusCode() == 503) {
          try {
            String code = json.readTree(response.body()).path("code").asText("");
            if (java.util.Set.of("RAG_DISABLED", "RAG_UNAVAILABLE").contains(code))
              throw new ApiException(503, "资料检索暂不可用，请稍后重试", code);
          } catch (tools.jackson.core.JacksonException ignored) {
          }
        }
        throw new ApiException(503, "Agent 能力暂不可用", "AGENT_UNAVAILABLE");
      }
      return json.readTree(response.body());
    } catch (ApiException ex) {
      throw ex;
    } catch (Exception ex) {
      if (ex instanceof InterruptedException) Thread.currentThread().interrupt();
      throw new ApiException(503, "Agent 连接失败", "AGENT_UNAVAILABLE");
    }
  }

  public boolean available() {
    try {
      var response = http.send(
          HttpRequest.newBuilder(URI.create(base + "/readyz")).timeout(Duration.ofMillis(800)).GET().build(),
          HttpResponse.BodyHandlers.discarding());
      return response.statusCode() / 100 == 2;
    } catch (Exception ex) {
      if (ex instanceof InterruptedException) Thread.currentThread().interrupt();
      return false;
    }
  }

  public void generate(Object body, Duration timeout, BiConsumer<String, JsonNode> consumer)
      throws IOException, InterruptedException {
    stream("/executions", body, timeout, consumer);
  }

  /** Forward a bounded internal SSE capability without buffering the full response. */
  public void stream(String path, Object body, Duration timeout, BiConsumer<String, JsonNode> consumer)
      throws IOException, InterruptedException {
    var response =
        http.send(request(path, body, timeout), HttpResponse.BodyHandlers.ofInputStream());
    if (response.statusCode() / 100 != 2) {
      response.body().close();
      throw new IOException("Agent rejected execution: " + response.statusCode());
    }
    var stream = response.body();
    var watchdog = java.util.concurrent.Executors.newSingleThreadScheduledExecutor();
    long deadline = System.nanoTime() + timeout.toNanos();
    Thread caller = Thread.currentThread();
    watchdog.scheduleAtFixedRate(
        () -> {
          if (caller.isInterrupted() || System.nanoTime() >= deadline)
            try {
              stream.close();
            } catch (IOException ignored) {
            }
        },
        100,
        100,
        java.util.concurrent.TimeUnit.MILLISECONDS);
    try (var reader =
        new BufferedReader(
            new InputStreamReader(response.body(), java.nio.charset.StandardCharsets.UTF_8))) {
      String event = null, line;
      var data = new StringBuilder();
      boolean terminal = false;
      while ((line = reader.readLine()) != null) {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedException();
        if (line.startsWith("event:")) event = line.substring(6).strip();
        else if (line.startsWith("data:")) data.append(line.substring(5).strip());
        else if (line.isEmpty() && event != null && !data.isEmpty()) {
          consumer.accept(event, json.readTree(data.toString()));
          if (event.equals("result") || event.equals("error")) {
            terminal = true;
            break;
          }
          event = null;
          data.setLength(0);
        }
      }
      if (!terminal) throw new EOFException("Agent stream closed without terminal event");
    } finally {
      watchdog.shutdownNow();
    }
  }
}
