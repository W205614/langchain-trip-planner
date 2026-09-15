package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.persistence.*;
import java.net.URI;
import java.net.http.*;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.*;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "trip.jwt-secret=integration-secret-only-01234567890123456789",
      "trip.internal-key=integration-internal-only-01234567890123456789",
      "spring.datasource.url=${TEST_DATABASE_URL}",
      "spring.datasource.username=trip",
      "spring.datasource.password=isolated-test-only",
      "WORKERS_ENABLED=false"
    })
@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class BusinessIntegrationTest {
  @Value("${local.server.port}")
  int port;

  @Autowired JdbcTemplate jdbc;
  @Autowired HistoryMapper history;
  @Autowired TransactionTemplate tx;
  @Autowired JsonMapper json;
  @Autowired com.tripplanner.domain.TaskService tasks;
  @Autowired TaskMapper taskMapper;
  @MockitoBean AgentClient agent;
  final HttpClient http = HttpClient.newHttpClient();

  long userId() {
    var row = new HashMap<String, Object>();
    row.put("username", "test_" + UUID.randomUUID());
    row.put("hashed_password", "not-a-password");
    return jdbc.queryForObject(
        "INSERT INTO users(username,hashed_password) VALUES (?,?) RETURNING id",
        Long.class,
        "t_" + UUID.randomUUID().toString().substring(0, 20),
        "test-only");
  }

  tools.jackson.databind.node.ObjectNode planning() {
    return json.createObjectNode()
        .put("city", "北京")
        .put("start_date", "2026-10-01")
        .put("end_date", "2026-10-01")
        .put("travel_days", 1)
        .put("transportation", "公共交通")
        .put("accommodation", "经济型酒店");
  }

  @Test
  void concurrentSameKeyProducesOneTask() throws Exception {
    long uid = userId();
    String key = UUID.randomUUID().toString();
    try (var pool = java.util.concurrent.Executors.newFixedThreadPool(6)) {
      var calls = new ArrayList<java.util.concurrent.Future<String>>();
      for (int i = 0; i < 12; i++)
        calls.add(
            pool.submit(
                () ->
                    tasks
                        .submit(uid, planning(), key, "test", null)
                        .path("data")
                        .path("id")
                        .asText("")));
      var ids = new HashSet<String>();
      for (var call : calls) ids.add(call.get());
      assertEquals(1, ids.size());
      assertEquals(1, taskMapper.count(uid));
      taskMapper.cancel(uid, ids.iterator().next());
    }
  }

  @Test
  void changedPayloadWithSameKeyConflicts() {
    long uid = userId();
    String key = UUID.randomUUID().toString();
    var created = tasks.submit(uid, planning(), key, "test", null);
    var error =
        assertThrows(
            com.tripplanner.api.ApiException.class,
            () -> tasks.submit(uid, planning().put("city", "上海"), key, "test", null));
    assertEquals(409, error.status);
    taskMapper.cancel(uid, created.path("data").path("id").asText(""));
  }

  @Test
  void userActiveQuotaIsEnforcedWithoutAffectingOtherUsers() {
    long uid = userId();
    var identities = new ArrayList<String>();
    try {
      for (int i = 0; i < 4; i++)
        identities.add(
            tasks
                .submit(uid, planning(), UUID.randomUUID().toString(), "quota", null)
                .path("data")
                .path("id")
                .asText(""));
      assertEquals(
          429,
          assertThrows(
                  com.tripplanner.api.ApiException.class,
                  () -> tasks.submit(uid, planning(), UUID.randomUUID().toString(), "quota", null))
              .status);
      long other = userId();
      String otherTask =
          tasks
              .submit(other, planning(), UUID.randomUUID().toString(), "quota", null)
              .path("data")
              .path("id")
              .asText("");
      assertEquals(1, taskMapper.cancel(other, otherTask));
    } finally {
      identities.forEach(id -> taskMapper.cancel(uid, id));
    }
  }

  @Test
  void staleAndForeignHistoryUpdatesCannotOverwriteCurrentVersion() {
    long uid = userId();
    long id =
        jdbc.queryForObject(
            "INSERT INTO"
                + " trip_records(user_id,city,start_date,end_date,travel_days,transportation,accommodation,preferences,free_text_input,plan_json,quality_json)"
                + " VALUES (?,'北京','2026-10-01','2026-10-01',1,'步行','经济','[]','','{}','{}')"
                + " RETURNING id",
            Long.class,
            uid);
    var update =
        new HashMap<String, Object>(
            Map.of(
                "id",
                id,
                "user_id",
                uid + 1,
                "version",
                1,
                "plan_json",
                "{}",
                "quality_json",
                "{}"));
    assertEquals(0, history.update(update));
    update.put("user_id", uid);
    assertEquals(1, history.update(update));
    assertEquals(0, history.update(update));
    assertEquals(2, history.owned(uid, id).get("version"));
    assertNull(history.owned(uid + 1, id));
  }

  @Test
  void cancellationRejectsLateCompletionAndWrongOwner() {
    long uid = userId();
    var created = tasks.submit(uid, planning(), UUID.randomUUID().toString(), "test", null);
    String id = created.path("data").path("id").asText("");
    jdbc.update(
        "UPDATE trip_tasks SET status='running',execution_id='execution-test' WHERE id=?", id);
    assertEquals(0, taskMapper.cancel(uid + 1, id));
    assertEquals(1, taskMapper.cancel(uid, id));
    assertEquals(
        0,
        taskMapper.finish(
            Map.of(
                "id",
                id,
                "execution_id",
                "execution-test",
                "status",
                "succeeded",
                "message",
                "late")));
    assertEquals("cancelled", taskMapper.get(id).get("status"));
    assertEquals(
        404,
        assertThrows(com.tripplanner.api.ApiException.class, () -> tasks.snapshot(uid + 1, id))
            .status);
  }

  @Test
  void deadlineAndExecutionIdFenceFinalResults() {
    long uid = userId();
    var created = tasks.submit(uid, planning(), UUID.randomUUID().toString(), "test", null);
    String id = created.path("data").path("id").asText("");
    jdbc.update(
        "UPDATE trip_tasks SET status='running',execution_id='current-execution' WHERE id=?", id);
    assertEquals(
        0,
        taskMapper.finish(
            Map.of(
                "id",
                id,
                "execution_id",
                "old-execution",
                "status",
                "succeeded",
                "message",
                "late")));
    jdbc.update(
        "UPDATE trip_tasks SET deadline_at=timezone('UTC',now())-interval '1 second' WHERE id=?",
        id);
    assertEquals(
        0,
        taskMapper.finish(
            Map.of(
                "id",
                id,
                "execution_id",
                "current-execution",
                "status",
                "succeeded",
                "message",
                "late")));
    taskMapper.expire();
    assertEquals("TASK_TIMEOUT", taskMapper.get(id).get("error_code"));
  }

  HttpResponse<String> request(String path, String body, String token) throws Exception {
    var b =
        HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
            .header("Content-Type", "application/json");
    if (token != null) b.header("Authorization", "Bearer " + token);
    if (body == null) b.GET();
    else b.POST(HttpRequest.BodyPublishers.ofString(body));
    return http.send(b.build(), HttpResponse.BodyHandlers.ofString());
  }

  @Test
  void legacyAccountLifecycleRevokesOldToken() throws Exception {
    String username = "migration_" + UUID.randomUUID().toString().substring(0, 8);
    var registered =
        request(
            "/api/auth/register",
            json.writeValueAsString(Map.of("username", username, "password", "密码-abcdef")),
            null);
    assertEquals(200, registered.statusCode(), registered.body());
    String token = json.readTree(registered.body()).path("access_token").asText();
    assertEquals(200, request("/api/auth/me", null, token).statusCode());
    assertEquals(200, request("/api/auth/logout", "{}", token).statusCode());
    assertEquals(401, request("/api/auth/me", null, token).statusCode());
    assertEquals(401, request("/internal/v1/evidence/revision", "{}", token).statusCode());
  }

  @Test
  void historyAndOutboxRollbackTogether() {
    long before = jdbc.queryForObject("SELECT count(*) FROM trip_records", Long.class);
    long jobs = jdbc.queryForObject("SELECT count(*) FROM rag_sync_jobs", Long.class);
    assertThrows(
        IllegalStateException.class,
        () ->
            tx.executeWithoutResult(
                s -> {
                  var row =
                      new HashMap<String, Object>(
                          Map.of(
                              "user_id",
                              42,
                              "city",
                              "北京",
                              "start_date",
                              "2026-10-01",
                              "end_date",
                              "2026-10-01",
                              "travel_days",
                              1,
                              "transportation",
                              "步行",
                              "accommodation",
                              "经济",
                              "preferences",
                              "[]",
                              "free_text_input",
                              "",
                              "plan_json",
                              "{}"));
                  row.put("quality_json", "{}");
                  history.insert(row);
                  history.outbox(((Number) row.get("id")).longValue(), 42, "upsert");
                  throw new IllegalStateException("injected transaction failure");
                }));
    assertEquals(before, jdbc.queryForObject("SELECT count(*) FROM trip_records", Long.class));
    assertEquals(jobs, jdbc.queryForObject("SELECT count(*) FROM rag_sync_jobs", Long.class));
  }
}
