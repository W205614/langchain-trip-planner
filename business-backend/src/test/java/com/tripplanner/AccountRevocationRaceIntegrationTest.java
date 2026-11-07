package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.api.*;
import com.tripplanner.domain.*;
import com.tripplanner.security.Passwords;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.*;
import org.springframework.test.context.bean.override.mockito.*;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
    "trip.jwt-secret=revocation-test-only-01234567890123456789",
    "trip.internal-key=revocation-internal-only-01234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false",
    "PUBLIC_REGISTRATION_MODE=legacy","UPLOAD_STORAGE_BACKEND=local","ADMIN_MFA_REQUIRED=false",
    "UPLOAD_DIR=${java.io.tmpdir}/trip-revocation-${random.uuid}",
    "ACCOUNT_EXPORT_DIR=${java.io.tmpdir}/trip-revocation-exports-${random.uuid}"
})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class AccountRevocationRaceIntegrationTest {
  @Autowired JdbcTemplate db;@Autowired TransactionTemplate tx;@Autowired JsonMapper json;
  @Autowired PublicOperationsService operations;@Autowired AccountDataService data;
  @Autowired OutboxWorker outbox;@Autowired CommunityController community;
  @MockitoSpyBean KnowledgeService knowledge;@MockitoSpyBean ObjectStorageService objects;
  @MockitoBean AgentClient agent;
  final ExecutorService pool=Executors.newFixedThreadPool(3);
  static final String PASSWORD="Revocation-integration-42!";
  static final byte[] PNG=Base64.getDecoder().decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV5kAAAAASUVORK5CYII=");

  @AfterEach void stopPool(){pool.shutdownNow();}
  long user(){return db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,?) RETURNING id",Long.class,"race_"+UUID.randomUUID().toString().replace("-",""),Passwords.hash(PASSWORD));}
  long trip(long uid){return db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,title,plan_json) VALUES(?,'北京','2026-12-01','2026-12-01','revocation-race','{}') RETURNING id",Long.class,uid);}
  long upload(long uid){return ((Number)knowledge.upload(uid,"北京","竞态原件",new MockMultipartFile("file","race.png","image/png",PNG)).get("id")).longValue();}
  MockHttpServletRequest request(long uid){var req=new MockHttpServletRequest();req.setAttribute("user",Map.of("id",uid));return req;}
  static void await(CountDownLatch signal){try{assertTrue(signal.await(10,TimeUnit.SECONDS),"deterministic I/O barrier timed out");}catch(InterruptedException ex){Thread.currentThread().interrupt();throw new IllegalStateException(ex);}}
  record Revocation(Future<?> future,CountDownLatch release){}
  Revocation holdDisabled(long uid)throws Exception {
    var locked=new CountDownLatch(1);var release=new CountDownLatch(1);
    Future<?> future=pool.submit(()->tx.executeWithoutResult(s->{operations.state(uid,uid,"disabled","late-write regression","race");locked.countDown();await(release);}));
    await(locked);return new Revocation(future,release);
  }
  void waitUntilFinishedOrBlocked(Future<?> future)throws Exception {
    long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(8);
    while(!future.isDone()&&db.queryForObject("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()",Long.class)==0L){
      assertTrue(System.nanoTime()<deadline,"operation did not reach a PostgreSQL commit/lock");Thread.sleep(20);
    }
  }
  void assertInactive(Future<?> future)throws Exception {
    var ex=assertThrows(ExecutionException.class,()->future.get(10,TimeUnit.SECONDS));
    assertInstanceOf(ApiException.class,ex.getCause());assertEquals("ACCOUNT_NOT_ACTIVE",((ApiException)ex.getCause()).code);
  }

  @ParameterizedTest @ValueSource(strings={"publish","parse"})
  void disabledSubmitterCannotCommitACompletedDocumentCapability(String phase)throws Exception {
    long uid=user(),id=upload(uid);String state=phase.equals("publish")?"publishing":"queued";
    db.update("UPDATE knowledge_documents SET status=?,source_text='reviewed text' WHERE id=?",state,id);
    long jobId=db.queryForObject("INSERT INTO knowledge_ingest_jobs(document_id,document_version,phase) VALUES(?,1,?) RETURNING id",Long.class,id,phase);
    var received=new CountDownLatch(1);var release=new CountDownLatch(1);
    when(agent.post(anyString(),any())).thenAnswer(call->{received.countDown();await(release);return json.createObjectNode().set("pages",json.createArrayNode().add("late extracted private text"));});
    var work=pool.submit(()->ReflectionTestUtils.invokeMethod(outbox,"run",db.queryForMap("SELECT * FROM knowledge_ingest_jobs WHERE id=?",jobId),true));
    try {await(received);operations.state(uid,uid,"disabled","已开始能力拒绝晚写","race");}
    finally {release.countDown();}
    work.get(10,TimeUnit.SECONDS);
    assertEquals(state,knowledge.get(id).get("status"));
    assertFalse(knowledge.get(id).get("source_text").toString().contains("late extracted"));
    assertEquals("retry",db.queryForObject("SELECT status FROM knowledge_ingest_jobs WHERE id=?",String.class,jobId));
  }

  @Test void exportCannotPublishAnArchiveAcrossACommittedRevocation()throws Exception {
    long uid=user();upload(uid);
    @SuppressWarnings("unchecked") UUID id=(UUID)((Map<String,Object>)data.requestExport(uid,"race-export")).get("id");
    db.update("UPDATE account_data_jobs SET status='running' WHERE id=?",id);
    var reading=new CountDownLatch(1);var releaseRead=new CountDownLatch(1);
    doAnswer(call->{InputStream actual=(InputStream)call.callRealMethod();return new FilterInputStream(actual){
      boolean first=true;
      @Override public int read(byte[] bytes,int offset,int length)throws IOException {if(first){first=false;reading.countDown();await(releaseRead);}return super.read(bytes,offset,length);}
    };}).when(knowledge).openOriginal(anyMap());
    var work=pool.submit(()->ReflectionTestUtils.invokeMethod(data,"export",uid,id));await(reading);
    var revocation=holdDisabled(uid);
    try {releaseRead.countDown();waitUntilFinishedOrBlocked(work);}
    finally {revocation.release().countDown();}
    revocation.future().get(10,TimeUnit.SECONDS);assertInactive(work);
    assertNotEquals("succeeded",db.queryForObject("SELECT status FROM account_data_jobs WHERE id=?",String.class,id));
    Path directory=(Path)ReflectionTestUtils.getField(data,"exports");assertFalse(Files.exists(directory.resolve(id+".zip")));
  }

  @ParameterizedTest @ValueSource(strings={"submit","copy"})
  void communityCommitRechecksAnAccountWhoseRevocationWasAlreadyInProgress(String operation)throws Exception {
    long uid=user(),owner=user(),record=trip(operation.equals("submit")?uid:owner);
    long card=0;
    if(operation.equals("copy"))card=db.queryForObject("INSERT INTO community_trip_cards(record_id,owner_id,record_version,title,city,snapshot_json,status) VALUES(?,?,1,'published','北京','{\"city\":\"北京\",\"start_date\":\"2026-12-01\",\"end_date\":\"2026-12-01\",\"plan\":{}}','published') RETURNING id",Long.class,record,owner);
    long publicCard=card;var revocation=holdDisabled(uid);
    var work=pool.submit(()->operation.equals("submit")?community.submit(request(uid),json.createObjectNode().put("record_id",record)):community.copy(request(uid),publicCard));
    try {waitUntilFinishedOrBlocked(work);}
    finally {revocation.release().countDown();}
    revocation.future().get(10,TimeUnit.SECONDS);assertInactive(work);
    if(operation.equals("submit"))assertEquals(0L,db.queryForObject("SELECT count(*) FROM community_trip_cards WHERE owner_id=?",Long.class,uid));
    else assertEquals(0L,db.queryForObject("SELECT count(*) FROM trip_records WHERE user_id=?",Long.class,uid));
  }

  @ParameterizedTest @ValueSource(strings={"disabled","erasure"})
  void reservedUploadCannotCreateAnUncapturedDocumentAfterRevocation(String mode)throws Exception {
    long uid=user();var stored=new CountDownLatch(1);var release=new CountDownLatch(1);
    doAnswer(call->{call.callRealMethod();stored.countDown();await(release);return null;}).when(objects).put(any(),any(),anyLong(),anyString(),anyString());
    var work=pool.submit(()->upload(uid));await(stored);
    var reservation=db.queryForMap("SELECT * FROM upload_reservations WHERE user_id=?",uid);
    UUID erasure=null;
    try {
      if(mode.equals("disabled"))operations.state(uid,uid,"disabled","上传提交前撤权","race");
      else {
        erasure=(UUID)((Map<?,?>)data.erase(uid,PASSWORD,"","race-erasure")).get("id");
        ReflectionTestUtils.invokeMethod(data,"eraseStep",uid,erasure,false);
        UUID job=erasure;var busy=assertThrows(ApiException.class,()->ReflectionTestUtils.invokeMethod(data,"eraseStep",uid,job,true));
        assertEquals("UPLOAD_IN_PROGRESS",busy.code);assertEquals("deleting",db.queryForObject("SELECT account_state FROM users WHERE id=?",String.class,uid));
      }
    } finally {release.countDown();}
    var failure=assertThrows(ExecutionException.class,()->work.get(10,TimeUnit.SECONDS));assertInstanceOf(ApiException.class,failure.getCause());
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM knowledge_documents WHERE submitted_by=?",Long.class,uid));
    if(erasure!=null){ReflectionTestUtils.invokeMethod(data,"eraseStep",uid,erasure,true);assertEquals("deleted",db.queryForObject("SELECT account_state FROM users WHERE id=?",String.class,uid));assertNull(objects.stat(ObjectStorageService.location(reservation)));assertEquals(0L,db.queryForObject("SELECT count(*) FROM upload_reservations WHERE user_id=?",Long.class,uid));}
  }

  @Test void erasureTargetVersionCannotBeOvertakenByAnAdminExtractionEdit()throws Exception {
    long uid=user(),reviewer=user(),id=upload(uid);
    db.update("UPDATE knowledge_documents SET status='awaiting_review',page_count=1,extracted_pages_json='[\"initial text\"]',source_text='initial text' WHERE id=?",id);
    var captured=new CountDownLatch(1);var releaseDelete=new CountDownLatch(1);
    doAnswer(call->{captured.countDown();await(releaseDelete);return call.callRealMethod();})
        .when(knowledge).review(eq(id),eq(uid),eq("delete"),any(),anyString());
    var erasing=pool.submit(()->data.erase(uid,PASSWORD,"","race-targets"));await(captured);
    var body=json.createObjectNode().put("version",1);body.putArray("pages").add("concurrent edited private text");
    var editing=pool.submit(()->knowledge.review(id,reviewer,"extraction",body,"race-edit"));
    try {waitUntilFinishedOrBlocked(editing);}
    finally {releaseDelete.countDown();}
    UUID job=(UUID)((Map<?,?>)erasing.get(10,TimeUnit.SECONDS)).get("id");
    assertInactive(editing);
    assertEquals(db.queryForObject("SELECT version FROM knowledge_documents WHERE id=?",Integer.class,id),
        db.queryForObject("SELECT version FROM account_erasure_targets WHERE job_id=? AND kind='document'",Integer.class,job));
    ReflectionTestUtils.invokeMethod(data,"eraseStep",uid,job,false);
    knowledge.removeDeletedOriginal(knowledge.get(id));db.update("UPDATE knowledge_ingest_jobs SET status='succeeded' WHERE document_id=? AND phase='delete'",id);
    ReflectionTestUtils.invokeMethod(data,"eraseStep",uid,job,true);
    assertEquals("deleted",db.queryForObject("SELECT account_state FROM users WHERE id=?",String.class,uid));
  }
}
