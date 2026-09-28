package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.TaskService;
import com.tripplanner.persistence.HistoryMapper;
import com.tripplanner.persistence.TaskMapper;
import io.micrometer.core.instrument.MeterRegistry;
import java.net.URI;
import java.net.http.*;
import java.util.Map;
import java.util.UUID;
import java.util.function.BiConsumer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.util.ReflectionTestUtils;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "trip.jwt-secret=integration-secret-only-01234567890123456789",
      "trip.internal-key=integration-internal-only-01234567890123456789",
      "spring.datasource.url=${TEST_DATABASE_URL}",
      "spring.datasource.username=trip",
      "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}",
      "WORKERS_ENABLED=false"
    })
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class AssistantProposalIntegrationTest {
  @Value("${local.server.port}") int port;
  @Autowired JdbcTemplate jdbc;
  @Autowired JsonMapper json;
  @Autowired TaskService tasks;
  @Autowired TaskMapper taskMapper;
  @Autowired HistoryMapper history;
  @Autowired MeterRegistry registry;
  @MockitoBean AgentClient agent;
  final HttpClient http = HttpClient.newHttpClient();

  @BeforeEach
  void agentFixture() {
    when(agent.available()).thenReturn(true);
    when(agent.post(eq("/capabilities/assistant-intent"), any()))
        .thenReturn(json.readTree("""
            {"data":{"operation":"propose_change","target_day":0,"missing_slots":[],
              "intent":"revise_day","confirmation_required":true}}
            """));
  }

  private HttpResponse<String> request(String method, String path, String token, String body, String version)
      throws Exception {
    var builder = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
        .header("Content-Type", "application/json");
    if (token != null) builder.header("Authorization", "Bearer " + token);
    if (version != null) builder.header("If-Match", version);
    builder.header("Idempotency-Key", UUID.randomUUID().toString());
    builder.method(method, body == null ? HttpRequest.BodyPublishers.noBody()
        : HttpRequest.BodyPublishers.ofString(body));
    return http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
  }

  private String token(String username) throws Exception {
    var response = request("POST", "/api/auth/register", null,
        json.writeValueAsString(Map.of("username", username, "password", "browser123")), null);
    assertEquals(200, response.statusCode(), response.body());
    return json.readTree(response.body()).path("access_token").asText();
  }

  private ObjectNode plan(String description) {
    var plan = json.createObjectNode().put("city", "北京")
        .put("start_date", "2026-10-01").put("end_date", "2026-10-01");
    plan.putObject("constraints").putArray("must_visit").add("Java可信景点");
    var day = plan.putArray("days").addObject().put("day_index", 0)
        .put("date", "2026-10-01").put("description", description);
    var attraction = day.putArray("attractions").addObject()
        .put("poi_id", "fixture-poi").put("name", "Java可信景点");
    attraction.putObject("location").put("longitude", 116.4).put("latitude", 39.9);
    return plan;
  }

  private long original(long uid) {
    return jdbc.queryForObject("""
        INSERT INTO trip_records(user_id,city,start_date,end_date,travel_days,transportation,
          accommodation,preferences,free_text_input,plan_json,quality_json)
        VALUES (?,'北京','2026-10-01','2026-10-01',1,'步行','经济','[]','',?,
          '{"outcome":"complete"}') RETURNING id
        """, Long.class, uid, json.writeValueAsString(plan("原安排")));
  }

  private String conversation(String token, long original) throws Exception {
    var response = request("POST", "/api/assistant/conversations", token,
        "{\"active_trip_id\":" + original + "}", null);
    assertEquals(200, response.statusCode(), response.body());
    return json.readTree(response.body()).path("id").asText();
  }

  private long proposal(String token, String conversation, String outcome, boolean trusted) throws Exception {
    var response = request("POST", "/api/assistant/conversations/" + conversation + "/messages", token,
        "{\"content\":\"调整第1天，少走路\",\"mode\":\"auto\"}", null);
    assertEquals(202, response.statusCode(), response.body());
    String taskId = json.readTree(response.body()).path("task_id").asText();
    assertFalse(taskId.isBlank());
    doAnswer(invocation -> {
      ObjectNode execution = invocation.getArgument(0);
      @SuppressWarnings("unchecked")
      BiConsumer<String, JsonNode> consumer = invocation.getArgument(2);
      var result = json.createObjectNode().put("protocol_version", 1)
          .put("execution_id", execution.path("execution_id").asText());
      var updated = plan("新安排");
      if (!trusted) ((ObjectNode)updated.path("days").get(0).path("attractions").get(0))
          .put("poi_id", "invented-poi");
      result.set("plan", updated);
      var quality = json.createObjectNode().put("outcome", outcome).put("rules_passed", true);
      quality.set("issues", json.createArrayNode());
      quality.putArray("data_gaps").add("route_duration_unavailable");
      result.set("quality", quality);
      result.set("trusted_candidates", json.createArrayNode());
      result.putObject("usage");
      consumer.accept("usage", json.createObjectNode().put("input_tokens", 120).put("output_tokens", 40));
      consumer.accept("result", result);
      return null;
    }).when(agent).generate(any(), any(), any());
    jdbc.update("UPDATE trip_tasks SET status='running',execution_id=? WHERE id=?",
        UUID.randomUUID().toString(), taskId);
    ReflectionTestUtils.invokeMethod(tasks, "executeCorrelated", taskMapper.get(taskId));
    var state = taskMapper.get(taskId);
    if (!trusted) {
      assertEquals("failed", state.get("status"));
      assertEquals("TRUSTED_POI_UNAVAILABLE", state.get("error_code"));
      return 0;
    }
    assertEquals("needs_attention", state.get("status"));
    return ((Number)state.get("record_id")).longValue();
  }

  @Test
  void generationValidationProposalAndConfirmationKeepOriginalUntouchedUntilApproval() throws Exception {
    String username = "proposal_" + UUID.randomUUID().toString().substring(0, 12);
    String owner = token(username), other = token("other_" + UUID.randomUUID().toString().substring(0, 12));
    long uid = jdbc.queryForObject("SELECT id FROM users WHERE username=?", Long.class, username);
    long original = original(uid);
    String conversation = conversation(owner, original);
    long proposal = proposal(owner, conversation, "degraded", true);
    assertNotEquals(original, proposal);
    assertEquals(1, history.owned(uid, original).get("version"));
    assertTrue(history.owned(uid, original).get("plan_json").toString().contains("原安排"));
    var pending = json.readTree(history.owned(uid, proposal).get("quality_json").toString());
    assertEquals("pending", pending.path("assistant_proposal_status").asText());
    assertEquals("degraded", pending.path("validated_outcome").asText());
    assertEquals("route_duration_unavailable", pending.path("data_gaps").get(0).asText());
    assertTrue(pending.path("rules_passed").asBoolean());
    assertTrue(json.readTree(history.owned(uid, proposal).get("plan_json").toString())
        .path("days").get(0).path("attractions").get(0).path("poi_id").asText().equals("fixture-poi"));
    assertEquals(original, pending.path("revision_parent").path("record_id").asLong());
    assertEquals(120, json.readTree(taskMapper.get(jdbc.queryForObject(
        "SELECT id FROM trip_tasks WHERE record_id=?", String.class, proposal)).get("usage_json").toString())
        .path("input_tokens").asInt());
    assertEquals(0L, jdbc.queryForObject("SELECT count(*) FROM rag_sync_jobs WHERE record_id=?", Long.class, original));

    String url = "/api/assistant/conversations/" + conversation + "/proposals/" + proposal + "/confirm";
    assertEquals(404, request("POST", url, other, "{}", "1").statusCode());
    var unrelated = request("POST", "/api/assistant/conversations", owner, "{}", null);
    assertEquals(200, unrelated.statusCode(), unrelated.body());
    String otherConversation = json.readTree(unrelated.body()).path("id").asText();
    assertEquals(409, request("POST", "/api/assistant/conversations/" + otherConversation + "/messages", owner,
        "{\"content\":\"调整第1天\",\"mode\":\"revise\",\"record_id\":" + original
            + ",\"day_index\":0,\"version\":1}", null).statusCode());
    assertEquals(409, request("POST", url.replace(conversation, otherConversation), owner, "{}", "1").statusCode());
    assertEquals(409, request("POST", "/api/history/" + proposal + "/apply-draft", owner, "{}", "1").statusCode());
    assertEquals(409, request("DELETE", "/api/history/" + proposal, owner, null, null).statusCode());
    assertEquals(409, request("POST", url, owner, "{}", "2").statusCode());
    assertEquals(409, request("POST", "/api/trips/" + proposal + "/shares", owner, "{}", null).statusCode());
    assertEquals(409, request("POST", "/api/community/cards", owner,
        "{\"record_id\":" + proposal + "}", null).statusCode());

    var confirmed = request("POST", url, owner, "{}", "1");
    assertEquals(200, confirmed.statusCode(), confirmed.body());
    assertEquals("degraded", json.readTree(confirmed.body()).path("quality").path("outcome").asText());
    assertEquals(2, history.owned(uid, original).get("version"));
    assertTrue(registry.counter("trip.assistant.proposal.total", "action", "confirmed").count() >= 1);
    assertTrue(registry.timer("trip.task.execution.duration", "outcome", "success").count() >= 1);
    assertTrue(history.owned(uid, original).get("plan_json").toString().contains("新安排"));
    assertEquals("confirmed", json.readTree(history.owned(uid, proposal).get("quality_json").toString())
        .path("assistant_proposal_status").asText());
    assertEquals(1L, jdbc.queryForObject("SELECT count(*) FROM rag_sync_jobs WHERE record_id=?", Long.class, original));
    assertEquals(409, request("POST", url, owner, "{}", "1").statusCode());
    assertEquals(2, history.owned(uid, original).get("version"));
  }

  @Test
  void discardAndStaleParentNeverOverwriteOriginal() throws Exception {
    String username = "proposal_" + UUID.randomUUID().toString().substring(0, 12);
    String owner = token(username);
    long uid = jdbc.queryForObject("SELECT id FROM users WHERE username=?", Long.class, username);
    long original = original(uid);
    String conversation = conversation(owner, original);
    long discarded = proposal(owner, conversation, "complete", true);
    String discardUrl = "/api/assistant/conversations/" + conversation + "/proposals/" + discarded + "/discard";
    assertEquals(200, request("POST", discardUrl, owner, "{}", "1").statusCode());
    assertEquals(409, request("POST", discardUrl, owner, "{}", "1").statusCode());
    assertEquals(1, history.owned(uid, original).get("version"));
    assertTrue(history.owned(uid, original).get("plan_json").toString().contains("原安排"));

    long stale = proposal(owner, conversation, "complete", true);
    jdbc.update("UPDATE trip_records SET version=version+1 WHERE id=?", original);
    String confirmUrl = "/api/assistant/conversations/" + conversation + "/proposals/" + stale + "/confirm";
    assertEquals(409, request("POST", confirmUrl, owner, "{}", "1").statusCode());
    assertEquals("pending", json.readTree(history.owned(uid, stale).get("quality_json").toString())
        .path("assistant_proposal_status").asText());
    assertTrue(history.owned(uid, original).get("plan_json").toString().contains("原安排"));
    assertEquals(200, request("POST", "/api/assistant/conversations/" + conversation
        + "/proposals/" + stale + "/discard", owner, "{}", "1").statusCode());
  }

  @Test
  void untrustedAgentResultCannotCreateAProposal() throws Exception {
    String username = "proposal_" + UUID.randomUUID().toString().substring(0, 12);
    String owner = token(username);
    long uid = jdbc.queryForObject("SELECT id FROM users WHERE username=?", Long.class, username);
    long original = original(uid);
    String conversation = conversation(owner, original);
    proposal(owner, conversation, "complete", false);
    assertEquals(1L, history.count(uid, ""));
    assertEquals(1, history.owned(uid, original).get("version"));
  }

  @Test
  void regularRevisionIgnoresClientSuppliedAssistantMarker() throws Exception {
    String username = "manual_" + UUID.randomUUID().toString().substring(0, 12);
    String owner = token(username);
    long uid = jdbc.queryForObject("SELECT id FROM users WHERE username=?", Long.class, username);
    long original = original(uid);
    var response = request("POST", "/api/history/" + original + "/revise-task", owner,
        "{\"day_index\":0,\"instruction\":\"调整第1天\",\"assistant_conversation_id\":\"forged\"}", "1");
    assertEquals(202, response.statusCode(), response.body());
    String taskId = json.readTree(response.body()).path("data").path("id").asText();
    doAnswer(invocation -> {
      ObjectNode execution = invocation.getArgument(0);
      @SuppressWarnings("unchecked") BiConsumer<String, JsonNode> consumer = invocation.getArgument(2);
      var result = json.createObjectNode().put("protocol_version", 1)
          .put("execution_id", execution.path("execution_id").asText());
      result.set("plan", plan("手工改排结果"));
      result.set("quality", json.createObjectNode().put("outcome", "complete")
          .set("issues", json.createArrayNode()));
      result.set("trusted_candidates", json.createArrayNode());
      consumer.accept("result", result);
      return null;
    }).when(agent).generate(any(), any(), any());
    jdbc.update("UPDATE trip_tasks SET status='running',execution_id=? WHERE id=?",
        UUID.randomUUID().toString(), taskId);
    ReflectionTestUtils.invokeMethod(tasks, "executeCorrelated", taskMapper.get(taskId));
    assertEquals("succeeded", taskMapper.get(taskId).get("status"));
    assertEquals(original, ((Number) taskMapper.get(taskId).get("record_id")).longValue());
    assertEquals(2, history.owned(uid, original).get("version"));
    assertTrue(history.owned(uid, original).get("plan_json").toString().contains("手工改排结果"));
  }
}
