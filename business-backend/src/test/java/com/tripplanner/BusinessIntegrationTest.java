package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.when;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.AmapGateway;
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
    assertEquals(404,request("/api/trips/"+id,null,other).statusCode());
    var share=request("/api/trips/"+id+"/shares","{\"expires_days\":7}",owner);
    assertEquals(200,share.statusCode(),share.body()); String token=json.readTree(share.body()).path("token").asText();
    assertEquals(200,request("/api/shared-trips/"+token,null,null).statusCode());
    assertEquals(200,request("/api/shared-trips/"+token+"/copy","{}",other).statusCode());
    long shareId=json.readTree(share.body()).path("id").asLong();
    assertEquals(404,request("DELETE","/api/trips/"+id+"/shares/"+shareId,null,other,Map.of()).statusCode());
    assertEquals(200,request("DELETE","/api/trips/"+id+"/shares/"+shareId,null,owner,Map.of()).statusCode());
    assertEquals(404,request("/api/shared-trips/"+token,null,null).statusCode());
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
