package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;

import com.sun.net.httpserver.HttpServer;
import com.tripplanner.domain.OutboundActorContext;
import com.tripplanner.domain.ExecutionGovernanceService;
import com.tripplanner.domain.SingleInstance;
import com.tripplanner.persistence.UserMapper;
import com.tripplanner.security.Passwords;
import com.tripplanner.security.TokenService;
import java.io.IOException;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.*;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Real HTTP and PostgreSQL boundaries; only the external Python provider is isolated. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
    "trip.jwt-secret=actor-test-jwt-012345678901234567890123456789",
    "trip.internal-key=actor-test-internal-012345678901234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false",
    "PUBLIC_REGISTRATION_MODE=email_verified","PUBLIC_BASE_URL=http://localhost:8380",
    "SMTP_ENABLED=true","SMTP_HOST=127.0.0.1","SMTP_PORT=65500","SMTP_SECURITY=none",
    "SMTP_FROM=trip@example.test","ACCOUNT_SECURITY_ENCRYPTION_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
    "OUTBOUND_REQUIRE_ACTOR_CONTEXT=true"
})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class PublicActorIntegrationTest {
  private static final String KEY="actor-test-internal-012345678901234567890123456789";
  private static final String ANSWER="隔离供应商返回的攻略回答";
  private static HttpServer provider;
  private static ExecutorService providerWorkers;
  private static final BlockingQueue<Observed> observed=new LinkedBlockingQueue<>();
  private record Observed(String path,String operation,String serviceKey){}
  @Autowired JdbcTemplate db;@Autowired UserMapper users;@Autowired TokenService tokens;
  @Autowired OutboundActorContext actors;@Autowired JsonMapper json;
  @Autowired ExecutionGovernanceService governance;
  @Value("${local.server.port}")int port;
  private final HttpClient client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
  @DynamicPropertySource static void upstream(DynamicPropertyRegistry registry)throws IOException {
    provider=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
    providerWorkers=Executors.newVirtualThreadPerTaskExecutor();provider.setExecutor(providerWorkers);
    provider.createContext("/",exchange->{
      String path=exchange.getRequestURI().getPath();exchange.getRequestBody().readAllBytes();
      observed.add(new Observed(path,exchange.getRequestHeaders().getFirst("X-Outbound-Operation"),exchange.getRequestHeaders().getFirst("X-Service-Key")));
      boolean stream=path.endsWith("/research/stream");
      String content=stream?"event: progress\ndata: {\"stage\":\"retrieval\"}\n\nevent: result\ndata: {\"answer\":\""+ANSWER+"\",\"sources\":[],\"answer_status\":\"supported_excerpt_answer\"}\n\n":"{\"success\":true,\"data\":{\"answer\":\""+ANSWER+"\"}}";
      byte[] bytes=content.getBytes(StandardCharsets.UTF_8);exchange.getResponseHeaders().set("Content-Type",stream?"text/event-stream":"application/json");
      exchange.sendResponseHeaders(200,bytes.length);try(var output=exchange.getResponseBody()){output.write(bytes);}
    });provider.start();registry.add("trip.agent-url",()->"http://127.0.0.1:"+provider.getAddress().getPort());
  }
  @AfterAll static void stop(){if(provider!=null)provider.stop(0);if(providerWorkers!=null)providerWorkers.shutdownNow();}
  @BeforeEach void clear(){observed.clear();}
  long user(boolean verified) {
    long uid=db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,?) RETURNING id",Long.class,"actor_"+UUID.randomUUID().toString().replace("-",""),Passwords.hash("Actor-integration-42!"));
    if(verified)db.update("UPDATE users SET email=?,email_verified_at=timezone('UTC',now()) WHERE id=?","actor"+uid+"@example.test",uid);
    return uid;
  }
  long trip(long uid){return db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,title,plan_json) VALUES(?,'北京','2026-12-01','2026-12-01','现有私有行程','{\"days\":[]}') RETURNING id",Long.class,uid);}
  HttpResponse<String> request(String method,String path,String body,Long uid,Map<String,String> extra)throws Exception {
    var builder=HttpRequest.newBuilder(URI.create("http://localhost:"+port+path)).timeout(Duration.ofSeconds(12));
    if(uid!=null)builder.header("Authorization","Bearer "+tokens.issue(users.byId(uid)));
    extra.forEach(builder::header);
    if(body==null)builder.method(method,HttpRequest.BodyPublishers.noBody());else builder.header("Content-Type","application/json").method(method,HttpRequest.BodyPublishers.ofString(body));
    return client.send(builder.build(),HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
  }
  HttpResponse<String> reserve(String operation,String kind)throws Exception {
    var body=json.createObjectNode().put("kind",kind).put("request_key",UUID.randomUUID().toString()).put("reserved_tokens",32);
    if(operation!=null)body.put("operation_id",operation);
    return request("POST","/internal/v2/outbound/reserve",json.writeValueAsString(body),null,Map.of("X-Service-Key",KEY));
  }
  @Test void asyncResearchPreservesAuthenticatedActorAndPersistsReportedAnswer()throws Exception {
    long uid=user(true),trip=trip(uid);String conversation=UUID.randomUUID().toString();
    db.update("INSERT INTO assistant_conversations(id,user_id,active_trip_id) VALUES(?,?,?)",conversation,uid,trip);
    var response=request("POST","/api/assistant/conversations/"+conversation+"/messages/stream","{\"mode\":\"research\",\"content\":\"景点怎样预约\",\"city\":\"北京\"}",uid,Map.of());
    assertEquals(200,response.statusCode(),response.body());assertTrue(response.body().contains(ANSWER),response.body());
    Observed sent=observed.poll(5,TimeUnit.SECONDS);assertNotNull(sent);assertEquals("/internal/v1/capabilities/research/stream",sent.path());assertEquals(KEY,sent.serviceKey());assertNotNull(sent.operation());
    var actor=db.queryForMap("SELECT actor_kind,actor_id,purpose FROM outbound_operations WHERE id=?",UUID.fromString(sent.operation()));
    assertEquals("user",actor.get("actor_kind"));assertEquals(uid,((Number)actor.get("actor_id")).longValue());assertEquals("model",actor.get("purpose"));
    assertEquals(1L,db.queryForObject("SELECT count(*) FROM assistant_messages WHERE conversation_id=? AND user_id=? AND role='assistant' AND content=?",Long.class,conversation,uid,ANSWER));
    var reserved=reserve(sent.operation(),"text");assertEquals(200,reserved.statusCode(),reserved.body());
    assertEquals(1L,db.queryForObject("SELECT reserved_calls FROM outbound_user_usage WHERE user_id=? AND kind='text'",Long.class,uid));
  }
  @Test void expiredMissingAndWrongPurposeContextsCannotAuthorizeText()throws Exception {
    long uid=user(true);String operation=actors.register("user",uid,"model");
    db.update("UPDATE outbound_operations SET expires_at=timezone('UTC',now())-interval '1 second' WHERE id=?",UUID.fromString(operation));
    var response=reserve(operation,"text");assertEquals(409,response.statusCode());assertEquals("OUTBOUND_CONTEXT_EXPIRED",json.readTree(response.body()).path("code").asText());
    response=reserve(null,"text");assertEquals(422,response.statusCode());assertEquals("OUTBOUND_CONTEXT_REQUIRED",json.readTree(response.body()).path("code").asText());
    response=reserve(actors.register("anonymous",null,"map"),"text");assertEquals(422,response.statusCode());assertEquals("OUTBOUND_CONTEXT_INVALID",json.readTree(response.body()).path("code").asText());
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM outbound_user_usage WHERE user_id=?",Long.class,uid));
  }
  @Test void unverifiedUsersCanReadExistingTripsButCannotCallPaidOrPublishEndpoints()throws Exception {
    long uid=user(false),trip=trip(uid);
    var read=request("GET","/api/trips/"+trip,null,uid,Map.of());assertEquals(200,read.statusCode(),read.body());
    for(String path:List.of("/api/trip/tasks","/api/research/answer","/api/knowledge/submissions","/api/trips/"+trip+"/shares")) {
      var response=request("POST",path,"{}",uid,Map.of());assertEquals(403,response.statusCode(),path+" "+response.body());
      assertEquals("EMAIL_VERIFICATION_REQUIRED",json.readTree(response.body()).path("code").asText(),path);
    }
    assertTrue(observed.isEmpty(),"Rejected requests must not reach the provider");
  }
  @Test void browserCannotChooseAnAnonymousMapCallsActor()throws Exception {
    long uid=user(true);String forged=actors.register("user",uid,"model");
    var response=request("GET","/api/poi/detail/B-isolated",null,null,Map.of("X-Outbound-Operation",forged));assertEquals(200,response.statusCode(),response.body());
    Observed sent=observed.poll(5,TimeUnit.SECONDS);assertNotNull(sent);assertNotEquals(forged,sent.operation());
    var actor=db.queryForMap("SELECT actor_kind,actor_id,purpose FROM outbound_operations WHERE id=?",UUID.fromString(sent.operation()));
    assertEquals("anonymous",actor.get("actor_kind"));assertNull(actor.get("actor_id"));assertEquals("map",actor.get("purpose"));
  }
  @Test void supplierUsageReportsAreIdempotentLateAndBoundToOriginalReservation()throws Exception {
    long uid=user(true),other=user(true);String operation=actors.register("user",uid,"model"),key=UUID.randomUUID().toString();
    var reservation=json.createObjectNode().put("kind","embedding").put("request_key",key).put("operation_id",operation).put("reserved_tokens",100);
    var accepted=request("POST","/internal/v2/outbound/reserve",json.writeValueAsString(reservation),null,Map.of("X-Service-Key",KEY));assertEquals(200,accepted.statusCode(),accepted.body());
    String run=json.readTree(accepted.body()).path("run_id").asText();
    db.update("UPDATE users SET account_state='disabled',token_version=token_version+1 WHERE id=?",uid);
    var report=json.createObjectNode().put("request_key",key).put("run_id",run).put("input_tokens",75).put("output_tokens",0).put("user_id",other);
    var first=request("POST","/internal/v2/outbound/report",json.writeValueAsString(report),null,Map.of("X-Service-Key",KEY));assertEquals(200,first.statusCode(),first.body());assertFalse(json.readTree(first.body()).path("cached").asBoolean());
    var duplicate=request("POST","/internal/v2/outbound/report",json.writeValueAsString(report),null,Map.of("X-Service-Key",KEY));assertEquals(200,duplicate.statusCode(),duplicate.body());assertTrue(json.readTree(duplicate.body()).path("cached").asBoolean());
    var stored=db.queryForMap("SELECT user_id,supplier_input_tokens,supplier_output_tokens FROM outbound_reservations WHERE request_key=? AND run_id=?",key,run);
    assertEquals(uid,((Number)stored.get("user_id")).longValue());assertEquals(75L,((Number)stored.get("supplier_input_tokens")).longValue());
    report.put("input_tokens",76);var conflict=request("POST","/internal/v2/outbound/report",json.writeValueAsString(report),null,Map.of("X-Service-Key",KEY));assertEquals(409,conflict.statusCode());
    report.put("request_key",UUID.randomUUID().toString());var missing=request("POST","/internal/v2/outbound/report",json.writeValueAsString(report),null,Map.of("X-Service-Key",KEY));assertEquals(404,missing.statusCode());
    report.put("input_tokens",-1);var invalid=request("POST","/internal/v2/outbound/report",json.writeValueAsString(report),null,Map.of("X-Service-Key",KEY));assertEquals(422,invalid.statusCode());
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM outbound_reservations WHERE user_id=?",Long.class,other));
  }
  @Test void knownExecutionUsesTaskOwnerAndIgnoresUnrelatedOptionalOperation() {
    long uid=user(true);String task=UUID.randomUUID().toString(),execution=UUID.randomUUID().toString(),key=UUID.randomUUID().toString();
    db.update("INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,status,execution_id,deadline_at) VALUES(?,?,?,'isolated-task','{\"travel_days\":1}','running',?,timezone('UTC',now())+interval '5 minutes')",task,uid,UUID.randomUUID().toString(),execution);
    var original=org.springframework.test.util.ReflectionTestUtils.getField(governance,"singleInstance");
    var lease=org.mockito.Mockito.mock(SingleInstance.class);org.mockito.Mockito.when(lease.owned()).thenReturn(true);
    org.springframework.test.util.ReflectionTestUtils.setField(governance,"singleInstance",lease);
    try {
      governance.reserve(json.createObjectNode().put("kind","text").put("request_key",key).put("execution_id",execution).put("operation_id","malformed-client-context").put("reserved_tokens",40));
      var row=db.queryForMap("SELECT user_id,operation_id,execution_id FROM outbound_reservations WHERE request_key=?",key);
      assertEquals(uid,((Number)row.get("user_id")).longValue());assertNull(row.get("operation_id"));assertEquals(execution,row.get("execution_id"));
    } finally {
      org.springframework.test.util.ReflectionTestUtils.setField(governance,"singleInstance",original);
      db.update("UPDATE trip_tasks SET status='cancelled' WHERE id=?",task);
    }
  }
}
