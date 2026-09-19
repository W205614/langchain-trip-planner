package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.when;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.AmapGateway;
import com.tripplanner.domain.BusinessMetrics;
import com.tripplanner.domain.TripLedgerService;
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
      "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}",
      "WORKERS_ENABLED=false",
      "TRIP_USER_ACTIVE_LIMIT=4"
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
  @Autowired TripVersionMapper tripVersions;
  @Autowired TripLedgerService ledger;
  @Autowired ShareMapper shares;
  @Autowired AssistantMapper conversations;
  @Autowired BusinessMetrics businessMetrics;
  @MockitoBean AgentClient agent;
  @MockitoBean AmapGateway amap;
  final HttpClient http = HttpClient.newHttpClient();

  @BeforeEach
  void agentIsAvailableForTaskPersistenceTests() {
    when(agent.available()).thenReturn(true);
    when(amap.detail(org.mockito.ArgumentMatchers.anyString())).thenAnswer(invocation -> {
      String id=invocation.getArgument(0);
      var poi=json.createObjectNode().put("id",id).put("name","Java可信景点").put("city","北京").put("address","可信地址").put("opening_hours","");
      poi.putObject("location").put("longitude",116.4).put("latitude",39.9); poi.putArray("photos"); return poi;
    });
    when(amap.weather(org.mockito.ArgumentMatchers.anyString())).thenReturn(json.createArrayNode());
    when(amap.routeBetween(any(),any(),anyString(),anyString())).thenReturn(
        json.createObjectNode().put("distance",600).put("duration",600).put("walking_distance",600).put("route_type","walking"));
  }

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
        "UPDATE trip_tasks SET created_at=timezone('UTC',now())-interval '10 seconds',"
            + " deadline_at=timezone('UTC',now())-interval '1 second' WHERE id=?",
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
  void actionableTaskListExcludesPersistedResultsAndDrafts() {
    long uid = userId();
    for (String status :
        List.of("queued", "running", "failed", "cancelled", "succeeded", "needs_attention")) {
      jdbc.update(
          "INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,request_id,deadline_at,status)"
              + " VALUES (?,?,?,?,?,'test',timezone('UTC',now())+interval '5 minutes',?)",
          UUID.randomUUID().toString(),
          uid,
          UUID.randomUUID().toString(),
          UUID.randomUUID().toString(),
          "{\"city\":\"北京\"}",
          status);
    }
    assertEquals(4, taskMapper.actionableCount(uid));
    assertEquals(
        Set.of("queued", "running", "failed", "cancelled"),
        taskMapper.actionableList(uid, 0, 20).stream()
            .map(row -> row.get("status").toString())
            .collect(java.util.stream.Collectors.toSet()));
  }

  HttpResponse<String> request(String method, String path, String body, String token, Map<String,String> headers) throws Exception {
    var b=HttpRequest.newBuilder(URI.create("http://localhost:"+port+path)).header("Content-Type","application/json");
    if(token!=null)b.header("Authorization","Bearer "+token); headers.forEach(b::header);
    b.method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofString(body));
    return http.send(b.build(),HttpResponse.BodyHandlers.ofString());
  }

  String registerToken() throws Exception {
    var response=request("/api/auth/register",json.writeValueAsString(Map.of(
        "username","api_"+UUID.randomUUID().toString().substring(0,12),"password","browser123")),null);
    assertEquals(200,response.statusCode(),response.body()); return json.readTree(response.body()).path("access_token").asText();
  }

  @Test
  void traditionalTravelDataIsCanonicalIdempotentAndOwnerScoped() throws Exception {
    String owner=registerToken(),other=registerToken();
    String favorite=json.writeValueAsString(Map.of("poi_id","fixture-beijing-1","name","篡改名称","longitude",0));
    var first=request("/api/favorites",favorite,owner); assertEquals(200,first.statusCode(),first.body());
    assertEquals("Java可信景点",json.readTree(first.body()).path("data").path("name").asText());
    assertEquals(200,request("/api/favorites",favorite,owner).statusCode());
    assertEquals(1,json.readTree(request("/api/favorites",null,owner).body()).path("total").asInt());
    var manual=json.createObjectNode().put("title","传统闭环").put("city","北京")
        .put("start_date","2026-10-01").put("end_date","2026-10-01").put("travel_days",1)
        .put("transportation","步行").put("accommodation","经济型酒店").put("free_text_input","");
    manual.putArray("preferences"); manual.putObject("constraints");
    manual.putArray("days").addObject().putArray("poi_ids").add("fixture-beijing-1");
    var created=request("/api/trips",manual.toString(),owner); assertEquals(200,created.statusCode(),created.body());
    long id=json.readTree(created.body()).path("id").asLong();
    var versions=request("/api/trips/"+id+"/versions",null,owner);
    assertEquals(200,versions.statusCode(),versions.body());
    assertEquals(1,json.readTree(versions.body()).path("total").asInt());
    var restored=request("POST","/api/trips/"+id+"/restore","{\"source_version\":1}",owner,Map.of("If-Match","1"));
    assertEquals(200,restored.statusCode(),restored.body());
    assertEquals(2,json.readTree(restored.body()).path("version").asInt());
    assertEquals(200,request("/api/trips/"+id+"/versions/1",null,owner).statusCode());
    assertEquals(404,request("/api/trips/"+id,null,other).statusCode());
    assertEquals(404,request("/api/trips/"+id+"/versions",null,other).statusCode());
    var share=request("/api/trips/"+id+"/shares","{\"expires_days\":7}",owner);
    assertEquals(200,share.statusCode(),share.body()); String token=json.readTree(share.body()).path("token").asText();
    assertEquals(200,request("/api/shared-trips/"+token,null,null).statusCode());
    assertEquals(200,request("/api/shared-trips/"+token+"/copy","{}",other).statusCode());
    var submitted=request("/api/community/cards",json.writeValueAsString(Map.of(
        "record_id",id,"title","审核快照")),owner);
    assertEquals(200,submitted.statusCode(),submitted.body());
    long cardId=json.readTree(submitted.body()).path("id").asLong();
    assertEquals(409,request("/api/community/cards",json.writeValueAsString(Map.of(
        "record_id",id,"title","重复版本")),owner).statusCode());
    assertEquals(404,request("/api/community/cards/"+cardId,null,null).statusCode());
    jdbc.update("UPDATE community_trip_cards SET status='published',published_at=timezone('UTC',now()) WHERE id=?",cardId);
    assertEquals(200,request("/api/community/cards/"+cardId,null,null).statusCode());
    assertEquals(200,request("/api/community/cards/"+cardId+"/copy","{}",other).statusCode());
    long shareId=json.readTree(share.body()).path("id").asLong();
    assertEquals(404,request("DELETE","/api/trips/"+id+"/shares/"+shareId,null,other,Map.of()).statusCode());
    assertEquals(200,request("DELETE","/api/trips/"+id+"/shares/"+shareId,null,owner,Map.of()).statusCode());
    assertEquals(404,request("/api/shared-trips/"+token,null,null).statusCode());
    jdbc.update("DELETE FROM trip_records WHERE id=?",id);
    assertNull(jdbc.queryForObject("SELECT record_id FROM community_trip_cards WHERE id=?",Object.class,cardId));
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
    long uid = userId();
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
                              uid,
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
                  history.outbox(((Number) row.get("id")).longValue(), uid, "upsert");
                  throw new IllegalStateException("injected transaction failure");
                }));
    assertEquals(before, jdbc.queryForObject("SELECT count(*) FROM trip_records", Long.class));
    assertEquals(jobs, jdbc.queryForObject("SELECT count(*) FROM rag_sync_jobs", Long.class));
  }

  @Test
  void immutableVersionsRestoreAsANewVersionAndRemainOwnerScoped() {
    long uid = userId(), other = userId();
    var row = new HashMap<String, Object>();
    row.put("user_id", uid);
    row.put("city", "北京");
    row.put("start_date", "2026-10-01");
    row.put("end_date", "2026-10-01");
    row.put("travel_days", 1);
    row.put("transportation", "步行");
    row.put("accommodation", "经济");
    row.put("preferences", "[]");
    row.put("free_text_input", "");
    row.put("plan_json", "{\"marker\":\"v1\",\"days\":[]}");
    row.put("quality_json", "{\"outcome\":\"complete\"}");
    row.put("title", "版本测试");
    row.put("source", "manual");
    history.insert(row);
    long id = ((Number) row.get("id")).longValue();
    ledger.capture(uid, id, "manual_create", "version-create");
    assertEquals(
        1,
        history.update(
            Map.of(
                "id", id,
                "user_id", uid,
                "version", 1,
                "plan_json", "{\"marker\":\"v2\",\"days\":[]}",
                "quality_json", "{\"outcome\":\"draft\"}")));
    ledger.capture(uid, id, "user_edit", "version-edit");

    var restored = ledger.restore(uid, id, 2, 1, "version-restore");
    assertEquals(3, restored.get("version"));
    assertEquals(3, history.owned(uid, id).get("version"));
    assertTrue(history.owned(uid, id).get("plan_json").toString().contains("v1"));
    assertEquals(3, tripVersions.count(uid, id));
    assertThrows(
        org.springframework.dao.DataAccessException.class,
        () ->
            jdbc.update(
                "UPDATE trip_record_versions SET title='forbidden' WHERE record_id=? AND record_version=1",
                id));
    assertEquals(
        409,
        assertThrows(
                com.tripplanner.api.ApiException.class,
                () -> ledger.restore(uid, id, 2, 1, "stale"))
            .status);
    assertEquals(
        404,
        assertThrows(
                com.tripplanner.api.ApiException.class, () -> ledger.list(other, id, 1, 20))
            .status);
  }

  @Test
  void deletingTripRevokesSharesDetachesConversationAndCommitsTombstone() {
    long uid = userId();
    var row = new HashMap<String, Object>();
    row.put("user_id", uid);
    row.put("city", "北京");
    row.put("start_date", "2026-10-01");
    row.put("end_date", "2026-10-01");
    row.put("travel_days", 1);
    row.put("transportation", "步行");
    row.put("accommodation", "经济");
    row.put("preferences", "[]");
    row.put("free_text_input", "");
    row.put("plan_json", "{\"days\":[]}");
    row.put("quality_json", "{\"outcome\":\"complete\"}");
    row.put("title", "删除测试");
    row.put("source", "manual");
    history.insert(row);
    long id = ((Number) row.get("id")).longValue();
    ledger.capture(uid, id, "manual_create", "delete-create");
    var share = new HashMap<String, Object>();
    share.put("record_id", id);
    share.put("owner_id", uid);
    share.put("token_hash", "a".repeat(64));
    share.put("snapshot_json", "{}");
    share.put("record_version", 1);
    share.put("expires_at", java.sql.Timestamp.from(java.time.Instant.now().plusSeconds(3600)));
    shares.insert(share);
    String conversationId = UUID.randomUUID().toString();
    conversations.conversation(
        new HashMap<>(
            Map.of(
                "id", conversationId,
                "user_id", uid,
                "active_trip_id", id,
                "title", "删除测试")));

    ledger.delete(uid, id, "delete-request");

    assertNull(history.owned(uid, id));
    assertNotNull(jdbc.queryForObject("SELECT revoked_at FROM trip_shares WHERE id=?", Object.class, share.get("id")));
    assertNull(conversations.owned(uid, conversationId).get("active_trip_id"));
    assertEquals(0, tripVersions.count(uid, id));
    assertEquals(
        1L,
        jdbc.queryForObject(
            "SELECT count(*) FROM rag_sync_jobs WHERE record_id=? AND operation='delete'",
            Long.class,
            id));
    assertEquals(
        1L,
        jdbc.queryForObject(
            "SELECT count(*) FROM business_audit_events WHERE action='trip.delete' AND resource_id=?",
            Long.class,
            Long.toString(id)));
  }

  @Test
  void databaseConstraintsAndActuatorMetricsAreEffective() throws Exception {
    assertThrows(
        org.springframework.dao.DataIntegrityViolationException.class,
        () -> jdbc.update("INSERT INTO user_travel_preferences(user_id) VALUES (?)", Long.MAX_VALUE));
    businessMetrics.refresh();
    var metrics = request("/actuator/prometheus", null, null);
    assertEquals(200, metrics.statusCode(), metrics.body());
    assertTrue(metrics.body().contains("jvm_memory_used_bytes"));
    assertTrue(metrics.body().contains("trip_oldest_queue_age_seconds"));
    assertFalse(metrics.body().contains("user_id="));
  }
}
