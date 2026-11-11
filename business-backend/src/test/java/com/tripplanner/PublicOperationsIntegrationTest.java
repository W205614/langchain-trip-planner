package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.when;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.api.ApiException;
import com.tripplanner.domain.*;
import com.tripplanner.persistence.UserMapper;
import com.tripplanner.security.*;
import java.io.*;
import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.*;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
    "trip.jwt-secret=operations-test-only-01234567890123456789",
    "trip.internal-key=operations-internal-only-01234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false",
    "PUBLIC_REGISTRATION_MODE=legacy","STORAGE_BACKEND=local","ADMIN_MFA_REQUIRED=true",
    "UPLOAD_DIR=${java.io.tmpdir}/trip-operations-${random.uuid}",
    "ACCOUNT_EXPORT_DIR=${java.io.tmpdir}/trip-exports-${random.uuid}"
})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class PublicOperationsIntegrationTest {
  @Autowired JdbcTemplate db;@Autowired TransactionTemplate tx;@Autowired UserMapper users;
  @Autowired PublicOperationsService operations;@Autowired UserQuotaService quotas;@Autowired AccountDataService data;
  @Autowired KnowledgeService knowledge;@Autowired AccountAccess access;@Autowired TokenService tokens;
  @Autowired TaskService tasks;@Autowired ExecutionGovernanceService governance;@Autowired JsonMapper json;
  @Value("${local.server.port}")int port;@MockitoBean AgentClient agent;
  static final String PASSWORD="Operations-integration-42!";
  long user(boolean admin) {
    return db.queryForObject("INSERT INTO users(username,hashed_password,is_admin) VALUES(?,?,?) RETURNING id",Long.class,"ops_"+UUID.randomUUID().toString().replace("-",""),Passwords.hash(PASSWORD),admin);
  }
  long trip(long uid,String title) {
    return db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,title,plan_json) VALUES(?,'北京','2026-12-01','2026-12-01',?,'{}') RETURNING id",Long.class,uid,title);
  }
  Map<String,Integer> quota(Integer task,Integer text) {
    var values=new HashMap<String,Integer>();values.put("daily_tasks",task);values.put("active_tasks",null);values.put("daily_text",text);values.put("daily_embedding",null);values.put("daily_amap",null);return values;
  }
  @BeforeEach void available(){when(agent.available()).thenReturn(true);}

  @Test void disablingRevokesTokensAndProtectsLastAdmin() {
    long admin=user(true),uid=user(false);String token=tokens.issue(users.byId(uid));
    operations.state(admin,uid,"disabled","隔离测试停用","operations");
    assertEquals("ACCOUNT_INACTIVE",assertThrows(ApiException.class,()->access.requireActive(uid)).code);
    assertThrows(ApiException.class,()->tokens.verify(token));
    operations.state(admin,uid,"active","隔离测试启用","operations");
    access.requireActive(uid);assertThrows(ApiException.class,()->tokens.verify(token));
    tx.executeWithoutResult(s->{
      db.update("UPDATE users SET is_admin=false WHERE id<>?",admin);
      assertEquals("LAST_ADMIN_REQUIRED",assertThrows(ApiException.class,()->operations.state(admin,admin,"disabled","不能停用最后管理员","operations")).code);
      s.setRollbackOnly();
    });
    assertEquals("active",users.byId(admin).get("account_state"));
  }

  @Test void disabledOwnerCannotCommitALateAgentResult() {
    long admin=user(true),uid=user(false);operations.state(admin,uid,"disabled","阻止迟到结果","late");
    var task=new HashMap<String,Object>();task.put("id",UUID.randomUUID().toString());task.put("user_id",uid);task.put("request_id","late");
    var body=json.createObjectNode().put("city","北京");
    var plan=json.createObjectNode();plan.putArray("days").addObject().put("day_index",0).putArray("attractions").addObject().put("poi_id","B123").put("name","真实候选占位");
    var quality=json.createObjectNode().put("outcome","complete");
    var failure=assertThrows(ApiException.class,()->org.springframework.test.util.ReflectionTestUtils.invokeMethod(tasks,"complete",task,body,plan,quality,null));
    assertEquals("ACCOUNT_NOT_ACTIVE",failure.code);
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM trip_records WHERE user_id=?",Long.class,uid));
  }

  @Test void soleBootstrapAdminCannotDisableOrEraseBeforeMfaEnrollment() {
    long bootstrap=user(true);
    tx.executeWithoutResult(s->{
      db.update("UPDATE users SET is_admin=false WHERE id<>?",bootstrap);
      assertFalse(db.queryForObject("SELECT EXISTS(SELECT 1 FROM account_security WHERE user_id=? AND totp_enabled)",Boolean.class,bootstrap));
      assertEquals("LAST_ADMIN_REQUIRED",assertThrows(ApiException.class,()->operations.state(bootstrap,bootstrap,"disabled","初始管理员尚未设置MFA","bootstrap")).code);
      assertEquals("LAST_ADMIN_REQUIRED",assertThrows(ApiException.class,()->data.erase(bootstrap,PASSWORD,"","bootstrap")).code);
      assertEquals("active",users.byId(bootstrap).get("account_state"));assertEquals(Boolean.TRUE,users.byId(bootstrap).get("is_admin"));
      assertEquals(0L,db.queryForObject("SELECT count(*) FROM account_data_jobs WHERE user_id=? AND kind='erasure'",Long.class,bootstrap));
      s.setRollbackOnly();
    });
  }

  @Test void perUserReservationsAreAtomicAndTaskOverrideIsEnforced() throws Exception {
    long uid=user(false);tx.executeWithoutResult(s->quotas.update(uid,quota(0,1)));
    var request=json.createObjectNode().put("city","北京").put("start_date","2026-12-01").put("end_date","2026-12-01").put("travel_days",1).put("transportation","步行").put("accommodation","经济");
    assertEquals("DAILY_TASK_LIMIT",assertThrows(ApiException.class,()->tasks.submit(uid,request,UUID.randomUUID().toString(),"quota",null)).code);
    var ready=new CountDownLatch(2);var go=new CountDownLatch(1);var pool=Executors.newFixedThreadPool(2);
    Callable<Boolean> reserve=()->{ready.countDown();go.await();try{tx.executeWithoutResult(s->quotas.reserve(uid,"text",20,true));return true;}catch(ApiException ex){assertEquals("USER_OUTBOUND_QUOTA",ex.code);return false;}};
    try {
      var first=pool.submit(reserve);var second=pool.submit(reserve);assertTrue(ready.await(5,TimeUnit.SECONDS));go.countDown();
      assertNotEquals(first.get(10,TimeUnit.SECONDS),second.get(10,TimeUnit.SECONDS));
      assertEquals(1L,db.queryForObject("SELECT reserved_calls FROM outbound_user_usage WHERE user_id=? AND kind='text'",Long.class,uid));
    } finally {pool.shutdownNow();}
    long other=user(false);tx.executeWithoutResult(s->quotas.reserve(other,"text",10,true));
  }

  @Test void tasklessActorContextIsAttributedAndCannotUseMapIdentityForText() {
    long uid=user(false);UUID operation=UUID.randomUUID();
    db.update("INSERT INTO outbound_operations(id,actor_kind,actor_id,purpose,expires_at) VALUES(?,'user',?,'model',timezone('UTC',now())+interval '1 hour')",operation,uid);
    var body=json.createObjectNode().put("kind","text").put("request_key",UUID.randomUUID().toString()).put("operation_id",operation.toString()).put("reserved_tokens",50);
    governance.reserve(body);
    assertEquals(uid,db.queryForObject("SELECT user_id FROM outbound_reservations WHERE request_key=?",Long.class,body.path("request_key").asText()));
    db.update("UPDATE outbound_operations SET purpose='map' WHERE id=?",operation);
    body.put("request_key",UUID.randomUUID().toString());
    assertEquals("OUTBOUND_CONTEXT_INVALID",assertThrows(ApiException.class,()->governance.reserve(body)).code);
  }

  @Test void feedbackValidatesOwnershipAndUsesVersionsAndHttpAdminBoundary() throws Exception {
    long uid=user(false),other=user(false),admin=user(true),foreign=trip(other,"他人反馈关联不能泄露");
    assertEquals(404,assertThrows(ApiException.class,()->operations.createFeedback(uid,"planning","主题","内容",foreign,null,"")).status);
    @SuppressWarnings("unchecked") var created=(Map<String,Object>)operations.createFeedback(uid,"planning","主题","内容",null,null,"request-42");
    long id=((Number)created.get("id")).longValue();
    @SuppressWarnings("unchecked") var updated=(Map<String,Object>)operations.respond(admin,id,1,"closed","已处理","ops");
    assertEquals(2,updated.get("version"));
    assertEquals(2,db.queryForObject("SELECT version FROM user_feedback WHERE id=?",Integer.class,id));
    assertEquals("VERSION_CONFLICT",assertThrows(ApiException.class,()->operations.respond(admin,id,1,"processing","旧版本","ops")).code);
    String credential=tokens.issue(users.byId(uid));var client=HttpClient.newHttpClient();
    var response=client.send(HttpRequest.newBuilder(URI.create("http://localhost:"+port+"/api/admin/operations/users")).header("Authorization","Bearer "+credential).GET().build(),HttpResponse.BodyHandlers.ofString());
    assertEquals(403,response.statusCode());
    response=client.send(HttpRequest.newBuilder(URI.create("http://localhost:"+port+"/api/admin/operations/users/"+uid+"/quotas")).header("Authorization","Bearer "+credential).GET().build(),HttpResponse.BodyHandlers.ofString());
    assertEquals(403,response.statusCode());
    response=client.send(HttpRequest.newBuilder(URI.create("http://localhost:"+port+"/api/feedback")).header("Authorization","Bearer "+credential).GET().build(),HttpResponse.BodyHandlers.ofString());
    assertEquals(200,response.statusCode());assertTrue(response.body().contains("已处理"));assertFalse(response.body().contains("他人反馈关联不能泄露"));
  }

  @Test void feedbackVersionLimitRejectsWithoutChangingDataOrAudit() {
    long uid=user(false),admin=user(true);
    @SuppressWarnings("unchecked") var created=(Map<String,Object>)operations.createFeedback(uid,"planning","版本边界","隔离测试",null,null,"version-boundary");
    long id=((Number)created.get("id")).longValue();
    db.update("UPDATE user_feedback SET version=? WHERE id=?",Integer.MAX_VALUE-1,id);
    @SuppressWarnings("unchecked") var updated=(Map<String,Object>)operations.respond(admin,id,Integer.MAX_VALUE-1,"processing","最后可用版本","version-boundary");
    assertEquals(Integer.MAX_VALUE,updated.get("version"));
    assertEquals(Integer.MAX_VALUE,db.queryForObject("SELECT version FROM user_feedback WHERE id=?",Integer.class,id));
    assertEquals(Integer.MAX_VALUE,db.queryForObject("SELECT resource_version FROM business_audit_events WHERE action='feedback.responded' AND resource_id=?",Integer.class,Long.toString(id)));
    var before=db.queryForMap("SELECT status,reply,replied_by,version,updated_at FROM user_feedback WHERE id=?",id);
    long audits=db.queryForObject("SELECT count(*) FROM business_audit_events WHERE resource_type='feedback' AND resource_id=?",Long.class,Long.toString(id));
    var invalid=assertThrows(ApiException.class,()->operations.respond(admin,id,Integer.MAX_VALUE,"closed","不能写入","version-boundary"));
    assertEquals(422,invalid.status);assertEquals("INVALID_VERSION",invalid.code);
    assertEquals(before,db.queryForMap("SELECT status,reply,replied_by,version,updated_at FROM user_feedback WHERE id=?",id));
    assertEquals(audits,db.queryForObject("SELECT count(*) FROM business_audit_events WHERE resource_type='feedback' AND resource_id=?",Long.class,Long.toString(id)));
  }

  @Test void exportContainsOnlyOwnedDataAndActualOriginalBytes() throws Exception {
    long uid=user(false),other=user(false);trip(uid,"owned-export-sentinel");trip(other,"foreign-private-sentinel");
    byte[] png=Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV5kAAAAASUVORK5CYII=");
    knowledge.upload(uid,"北京","本人的资料",new MockMultipartFile("file","sample.png","image/png",png));
    @SuppressWarnings("unchecked") var created=(Map<String,Object>)data.requestExport(uid,"export");UUID id=(UUID)created.get("id");
    assertTrue(data.runOnce());
    var found=new HashMap<String,byte[]>();try(var input=new ZipInputStream(data.download(uid,id).getInputStream(),StandardCharsets.UTF_8)){java.util.zip.ZipEntry entry;while((entry=input.getNextEntry())!=null)found.put(entry.getName(),input.readAllBytes());}
    String content=new String(found.get("account.json"),StandardCharsets.UTF_8);assertTrue(content.contains("owned-export-sentinel"));assertFalse(content.contains("foreign-private-sentinel"));
    assertFalse(content.contains("hashed_password"));assertFalse(content.contains("token_version"));assertFalse(content.contains("storage_bucket"));assertEquals(2,found.size());assertArrayEquals(png,found.entrySet().stream().filter(e->e.getKey().startsWith("originals/")).findFirst().orElseThrow().getValue());
    assertEquals(404,assertThrows(ApiException.class,()->data.download(other,id)).status);
  }

  @Test void erasureRevokesImmediatelyAndWaitsForIndexAcknowledgment() throws Exception {
    long uid=user(false),other=user(false),record=trip(uid,"erasure-private-title"),foreign=trip(other,"remaining-shared-title");
    db.update("INSERT INTO trip_members(trip_id,user_id,role,status,invited_by) VALUES(?,?,'editor','accepted',?)",foreign,uid,other);
    db.update("INSERT INTO trip_expenses(trip_id,day_index,category,amount_cents,created_by,note) VALUES(?,0,'other',100,?,'shared-author-private-note')",foreign,uid);
    byte[] png=Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV5kAAAAASUVORK5CYII=");
    long document=((Number)knowledge.upload(uid,"北京","待删除原件",new MockMultipartFile("file","sample.png","image/png",png)).get("id")).longValue();
    String old=tokens.issue(users.byId(uid));
    @SuppressWarnings("unchecked") var erased=(Map<String,Object>)data.erase(uid,PASSWORD,"","erase");UUID id=(UUID)erased.get("id");String receipt=erased.get("receipt").toString();
    assertEquals("deleting",users.byId(uid).get("account_state"));assertThrows(ApiException.class,()->tokens.verify(old));
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM trip_members WHERE user_id=?",Long.class,uid));
    assertTrue(data.runOnce());assertEquals(0L,db.queryForObject("SELECT count(*) FROM trip_records WHERE id=?",Long.class,record));
    assertTrue(data.runOnce());assertEquals("deleting",users.byId(uid).get("account_state"));
    db.update("UPDATE rag_sync_jobs SET status='succeeded' WHERE user_id=? AND operation='delete'",uid);
    assertTrue(data.runOnce());assertEquals("deleting",users.byId(uid).get("account_state"));
    knowledge.removeDeletedOriginal(knowledge.get(document));
    db.update("UPDATE knowledge_ingest_jobs SET status='succeeded' WHERE document_id=? AND phase='delete'",document);
    assertTrue(data.runOnce());assertEquals("deleted",users.byId(uid).get("account_state"));assertNull(users.byId(uid).get("email"));
    @SuppressWarnings("unchecked") var state=(Map<String,Object>)data.erasureStatus(id,receipt);assertEquals("succeeded",state.get("status"));
    assertEquals(404,assertThrows(ApiException.class,()->data.erasureStatus(id,"x".repeat(43))).status);
    assertEquals(1L,db.queryForObject("SELECT count(*) FROM trip_expenses WHERE trip_id=? AND created_by=?",Long.class,foreign,uid));
    assertEquals("",db.queryForObject("SELECT note FROM trip_expenses WHERE trip_id=? AND created_by=?",String.class,foreign,uid));
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM knowledge_documents WHERE submitted_by=?",Long.class,uid));
    assertTrue(users.byId(uid).get("username").toString().startsWith("deleted_"));
    var client=HttpClient.newHttpClient();
    var response=client.send(HttpRequest.newBuilder(URI.create("http://localhost:"+port+"/api/auth/erasure/"+id)).header("X-Erasure-Receipt",receipt).GET().build(),HttpResponse.BodyHandlers.ofString());
    assertEquals(200,response.statusCode(),response.body());assertTrue(response.body().contains("succeeded"));assertFalse(response.body().contains("username"));
    response=client.send(HttpRequest.newBuilder(URI.create("http://localhost:"+port+"/api/auth/erasure/"+id)).header("X-Erasure-Receipt","x".repeat(43)).GET().build(),HttpResponse.BodyHandlers.ofString());
    assertEquals(404,response.statusCode());
  }

  @Test void erasureReplayWithoutTripsStillRunsProfileCleanup() {
    long uid=user(false),admin=user(true);String conversation=UUID.randomUUID().toString();
    db.update("INSERT INTO assistant_conversations(id,user_id,title) VALUES(?,?,'private-profile-only')",conversation,uid);
    @SuppressWarnings("unchecked") var erased=(Map<String,Object>)data.erase(uid,PASSWORD,"","erase-replay");UUID id=(UUID)erased.get("id");
    db.update("UPDATE account_data_jobs SET status='failed',stage='withdrawing_content',error_code='ISOLATED_INTERRUPTION' WHERE id=?",id);
    data.replay(admin,id,"replay");
    assertEquals("queued",db.queryForObject("SELECT status FROM account_data_jobs WHERE id=?",String.class,id));
    assertTrue(data.runOnce());assertEquals(0L,db.queryForObject("SELECT count(*) FROM assistant_conversations WHERE user_id=?",Long.class,uid));
    assertTrue(data.runOnce());assertEquals("deleted",users.byId(uid).get("account_state"));
  }
}
