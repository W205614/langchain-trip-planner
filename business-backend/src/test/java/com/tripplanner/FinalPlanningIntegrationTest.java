package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.tripplanner.api.ApiException;
import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={"trip.jwt-secret=integration-secret-only-01234567890123456789","trip.internal-key=integration-internal-only-01234567890123456789","spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip","spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class FinalPlanningIntegrationTest {
  @org.springframework.beans.factory.annotation.Value("${local.server.port}") int port;
  @Autowired com.tripplanner.security.TokenService tokens;
  @Autowired JdbcTemplate db;@Autowired PlanningInputService planning;@Autowired ExecutionGovernanceService governance;
  @Autowired TransactionTemplate tx;@Autowired JsonMapper json;@Autowired TaskService tasks;@MockitoBean AgentClient agent;
  long user(){return db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?, 'test-only') RETURNING id",Long.class,"final_"+UUID.randomUUID());}
  ObjectNode plan(){var p=json.createObjectNode().put("city","北京").put("start_date","2026-11-01").put("end_date","2026-11-02");var ds=p.putArray("days");
    for(int i=0;i<2;i++){var d=ds.addObject().put("day_index",i).put("date","2026-11-0"+(i+1));var a=d.putArray("attractions").addObject().put("poi_id","p"+i).put("name","景点"+i).put("visit_duration",60);d.putArray("meals");d.putArray("timeline").addObject().put("activity_id","poi:"+i+":p"+i).put("start_at","2026-11-0"+(i+1)+"T10:00:00+08:00").put("end_at","2026-11-0"+(i+1)+"T11:00:00+08:00");}return PlanningInputService.stableIds(p);}
  long trip(long uid){return db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,travel_days,plan_json,quality_json) VALUES(?,'北京','2026-11-01','2026-11-02',2,?,'{}') RETURNING id",Long.class,uid,json.writeValueAsString(plan()));}
  ObjectNode input(){return PlanningInputService.normalize(json.createObjectNode(),2);}
  ObjectNode scope(int day){var s=json.createObjectNode();s.putArray("day_indices").add(day);return s;}

  @Test void ownerOnlyAndConcurrentPlanningInputCompareAndSwap()throws Exception{
    long uid=user(),id=trip(uid),stranger=user();
    assertEquals(404,assertThrows(ApiException.class,()->planning.get(stranger,id)).status);
    var start=new CountDownLatch(1);try(var pool=Executors.newFixedThreadPool(6)){var results=new ArrayList<Future<Boolean>>();for(int i=0;i<6;i++)results.add(pool.submit(()->{start.await();try{planning.put(uid,id,1,1,input());return true;}catch(ApiException e){assertEquals(409,e.status);return false;}}));start.countDown();int wins=0;for(var result:results)if(result.get(10,TimeUnit.SECONDS))wins++;assertEquals(1,wins);}
    assertEquals(2,db.queryForObject("SELECT planning_revision FROM trip_records WHERE id=?",Integer.class,id));
    db.update("UPDATE trip_records SET quality_json='{\"assistant_proposal_status\":\"pending\"}' WHERE id=?",id);
    assertEquals(409,assertThrows(ApiException.class,()->planning.put(uid,id,1,2,input())).status);
  }
  @Test void protectsNonTargetDaysLockedContentPlacementAndTimeWindows(){
    long uid=user(),id=trip(uid);var bad=plan();((ObjectNode)bad.path("days").get(1)).put("description","changed");
    assertEquals("REVISION_SCOPE_VIOLATION",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,bad,scope(0),true))).code);
    planning.state(uid,id,1,"poi:0:p0",json.createObjectNode().put("planning_revision",1).put("locked",true).put("lock_mode","time"));
    var moved=plan();((ObjectNode)moved.path("days").get(0).path("timeline").get(0)).put("start_at","2026-11-01T12:00:00+08:00");
    assertEquals("PROTECTED_ACTIVITY_CHANGED",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,moved,scope(0),true))).code);
    var edited=plan();((ObjectNode)edited.path("days").get(0).path("attractions").get(0)).put("visit_duration",1);
    assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,edited,scope(0),true)));
    var movedDay=plan();var a=movedDay.path("days").get(0).withArray("attractions").remove(0);movedDay.path("days").get(1).withArray("attractions").add(a);
    assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,movedDay,scope(0),true)));
    long id2=trip(uid);var timeScope=scope(0).put("start_at","2026-11-01T09:00:00+08:00").put("end_at","2026-11-01T10:30:00+08:00");
    assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id2,edited,timeScope,true)));
    tx.executeWithoutResult(s->planning.protect(uid,id2,plan(),scope(0),true));
  }
  @Test void durableBudgetRejectsThirteenthConcurrentSendAndDuplicateReservation()throws Exception{
    String run="test:"+UUID.randomUUID();var latch=new CountDownLatch(1);
    try(var pool=Executors.newFixedThreadPool(8)){var fs=new ArrayList<Future<Boolean>>();for(int i=0;i<16;i++)fs.add(pool.submit(()->{latch.await();try{governance.reserve(json.createObjectNode().put("kind","text").put("run_id",run).put("request_key",UUID.randomUUID().toString()));return true;}catch(ApiException ex){assertEquals(429,ex.status);return false;}}));latch.countDown();int sent=0;for(var f:fs)if(f.get(15,TimeUnit.SECONDS))sent++;assertEquals(12,sent);}
    assertEquals(12L,db.queryForObject("SELECT count(*) FROM outbound_reservations WHERE run_id=?",Long.class,run));
    var reservation=json.createObjectNode().put("kind","amap").put("run_id",run).put("request_key","duplicate");governance.reserve(reservation);assertEquals(409,assertThrows(ApiException.class,()->governance.reserve(reservation)).status);
    assertEquals(1L,db.queryForObject("SELECT used FROM outbound_budget_counters WHERE scope=? AND kind='amap'",Long.class,"acceptance:"+run));
  }
  @Test void checkpointsRejectLateExecutionAndResumeKeepsOriginalTerminal(){
    when(agent.available()).thenReturn(true);long uid=user();String task=UUID.randomUUID().toString(),exec=UUID.randomUUID().toString();
    String request="{\"city\":\"北京\",\"start_date\":\"2026-11-01\",\"end_date\":\"2026-11-02\",\"travel_days\":2,\"transportation\":\"步行\",\"accommodation\":\"经济\",\"planning_input\":{}}";
    db.update("INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,status,execution_id,deadline_at) VALUES(?,?,?,'test',?,'running',?,timezone('UTC',now())+interval '5 minutes')",task,uid,task,request,exec);
    var checkpoint=json.createObjectNode().put("stage","candidates").put("input_hash","a".repeat(64)).put("expires_at",Instant.now().plusSeconds(300).toString());checkpoint.putObject("payload");checkpoint.putObject("compatibility");
    governance.save(exec,checkpoint);assertTrue(json.valueToTree(governance.get(exec)).path("checkpoints").size()>0);
    db.update("UPDATE trip_tasks SET status='failed',error_code='PROCESS_INTERRUPTED' WHERE id=?",task);
    assertEquals(409,assertThrows(ApiException.class,()->governance.save(exec,checkpoint)).status);
    var resumed=tasks.resume(uid,task,"resume:"+UUID.randomUUID(),"test");String child=resumed.path("data").path("id").asText();
    assertNotEquals(task,child);assertEquals("failed",db.queryForObject("SELECT status FROM trip_tasks WHERE id=?",String.class,task));assertEquals(task,db.queryForObject("SELECT resumed_from FROM trip_tasks WHERE id=?",String.class,child));
    assertEquals("unknown",db.queryForObject("SELECT column_default FROM information_schema.columns WHERE table_name='outbound_reservations' AND column_name='outcome'",String.class).replace("'","").replace("::character varying",""));
  }
  @Test void inlineCommitmentsBecomeEditableLedgerAndExpiredFactCannotConfirm(){
    long uid=user(),id=trip(uid);var in=input();
    in.withArray("commitments").addObject().put("id","user-booking").put("day_index",0).put("category","ticket").put("title","预约").put("status","confirmed").put("start_at","2026-11-01T10:00:00+08:00").put("end_at","2026-11-01T11:00:00+08:00");
    tx.executeWithoutResult(s->planning.saveInput(id,in));
    var saved=planning.input(planning.row(uid,id,false));assertEquals(1,saved.path("commitments").size());
    assertTrue(saved.path("commitments").get(0).path("id").isIntegralNumber());
    assertEquals(Instant.parse("2026-11-01T02:00:00Z"),Instant.parse(saved.path("commitments").get(0).path("start_at").asText()));
    var quality=json.createObjectNode();quality.putObject("planning_dependencies").putArray("facts").addObject().put("expires_at",Instant.now().minusSeconds(1).toString());
    assertEquals("FACTS_EXPIRED",assertThrows(ApiException.class,()->planning.validateDependencies(quality)).code);
  }
  @Test void updatedConfirmedAppointmentConstrainsNewScheduleRatherThanOldTimeline(){
    long uid=user(),id=trip(uid);
    db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,created_by,activity_id,start_at,end_at) VALUES(?,0,'ticket','预约','confirmed',?,'poi:0:p0','2026-11-01T12:00:00+08:00','2026-11-01T13:00:00+08:00')",id,uid);
    assertEquals("COMMITMENT_TIME_VIOLATION",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,plan(),scope(0),true))).code);
    var updated=plan();var timeline=(ObjectNode)updated.path("days").get(0).path("timeline").get(0);
    timeline.put("start_at","2026-11-01T12:00:00+08:00").put("end_at","2026-11-01T13:00:00+08:00");
    tx.executeWithoutResult(s->planning.protect(uid,id,updated,scope(0),true));
    assertEquals(1,planning.differences(plan(),updated).path("days").size());
  }
  @Test void admissionWindowAllowsLaterVisitEndButRejectsLateEntryAndSemanticOutsideChange(){
    long uid=user(),id=trip(uid);
    db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,created_by,activity_id,start_at,end_at,details_json) VALUES(?,0,'ticket','入场预约','confirmed',?,'poi:0:p0','2026-11-01T12:00:00+08:00','2026-11-01T13:00:00+08:00','{\"time_mode\":\"entry_window\"}')",id,uid);
    var proposed=plan();var timeline=(ObjectNode)proposed.path("days").get(0).path("timeline").get(0);
    timeline.put("start_at","2026-11-01T12:30:00+08:00").put("end_at","2026-11-01T14:30:00+08:00");
    tx.executeWithoutResult(s->planning.protect(uid,id,proposed,scope(0),true));
    timeline.put("start_at","2026-11-01T13:01:00+08:00");
    assertEquals("COMMITMENT_TIME_VIOLATION",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,proposed,scope(0),true))).code);
    timeline.put("start_at","2026-11-01T12:30:00+08:00");
    ((ObjectNode)proposed.path("days").get(1)).put("generation_mode","fallback");
    assertEquals("REVISION_SCOPE_VIOLATION",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->planning.protect(uid,id,proposed,scope(0),true))).code);
  }
  @Test void manualScheduleInvalidationPreservesUntouchedDayAndRejectsStaleTimeActions(){
    long uid=user(),id=trip(uid);var original=plan();var changed=plan();
    ((ObjectNode)changed.path("days").get(0).path("attractions").get(0)).put("visit_duration",120);
    var quality=json.createObjectNode().put("outcome","complete");
    planning.invalidateManualSchedule(original,changed,quality,Set.of(0));
    assertEquals(original.path("days").get(1),changed.path("days").get(1));
    assertTrue(changed.path("days").get(0).path("schedule_stale").asBoolean());
    assertEquals("unverified",changed.path("days").get(0).path("timeline").get(0).path("status").asText());
    assertEquals("degraded",quality.path("outcome").asText());
    db.update("UPDATE trip_records SET plan_json=? WHERE id=?",json.writeValueAsString(changed),id);
    var action=json.createObjectNode().put("planning_revision",1).put("locked",true).put("lock_mode","time");
    assertEquals("SCHEDULE_STALE",assertThrows(ApiException.class,()->planning.state(uid,id,1,"poi:0:p0",action)).code);
    assertEquals("SCHEDULE_STALE",assertThrows(ApiException.class,()->planning.state(uid,id,1,"poi:0:p0",json.createObjectNode().put("planning_revision",1).put("completed",true))).code);
  }
  @Test void unverifiedOverallScheduleStillBindsUsedOpeningFactsAndDuplicateIdsHaveCode(){
    var p=plan();var entry=(ObjectNode)p.path("days").get(0).path("timeline").get(0);
    entry.put("kind","attraction").put("status","unverified");entry.putArray("evidence_refs").add("p0");
    var in=input();var fact=in.withArray("opening_windows").addObject().put("poi_id","p0").put("date","2026-11-01")
        .put("source","test-official").put("confirmed",true).put("observed_at",Instant.now().toString()).put("expires_at",Instant.now().plusSeconds(60).toString());
    fact.putArray("windows").addObject().put("start","08:00").put("end","20:00");
    assertEquals(1,planning.dependencies(p,in).path("facts").size());
    var duplicate=((ObjectNode)p.path("days").get(0).path("attractions").get(0)).deepCopy();((ObjectNode)p.path("days").get(0)).withArray("attractions").add(duplicate);
    assertEquals("DUPLICATE_ACTIVITY_ID",assertThrows(ApiException.class,()->PlanningInputService.stableIds(p)).code);
  }
  @Test void publicPlanningInputRejectsMalformedNestedDataWithoutChangingRevision()throws Exception{
    long uid=user(),id=trip(uid);String token=tokens.issue(Map.of("id",uid,"token_version",0));
    var cases=List.of(
        "{\"city_segments\":[{\"city\":\"北京\",\"start_day\":0,\"end_day\":1},{\"city\":\"天津\",\"start_day\":1,\"end_day\":1}]}",
        "{\"arrival_at\":\"2026-11-01T09:00:00\"}",
        "{\"meal_windows\":[{\"type\":\"lunch\",\"start\":\"12:00\",\"end\":\"13:00\",\"duration_minutes\":-1}]}",
        "{\"opening_windows\":[{\"poi_id\":\"p0\",\"date\":\"2026-11-01\",\"windows\":[{\"start\":\"18:00\",\"end\":\"09:00\"}]}]}",
        "{\"commitments\":[{\"id\":\"a\",\"day_index\":0,\"start_at\":\"2026-11-01T12:00:00+08:00\",\"end_at\":\"2026-11-01T11:00:00+08:00\"}]}"
    );
    var http=java.net.http.HttpClient.newHttpClient();
    for(String input:cases){var body=json.createObjectNode().put("planning_revision",1);body.set("planning_input",json.readTree(input));
      var req=java.net.http.HttpRequest.newBuilder(java.net.URI.create("http://localhost:"+port+"/api/trips/"+id+"/planning-input")).header("Authorization","Bearer "+token).header("If-Match","1").header("Content-Type","application/json").PUT(java.net.http.HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
      var res=http.send(req,java.net.http.HttpResponse.BodyHandlers.ofString());assertEquals(422,res.statusCode(),res.body());
    }
    assertEquals(1,db.queryForObject("SELECT planning_revision FROM trip_records WHERE id=?",Integer.class,id));
  }

  @Test void publicRoundTripWithLedgerNumbersAllowsInputEditButRejectsCommitmentTampering()throws Exception{
    long uid=user(),id=trip(uid);String token=tokens.issue(Map.of("id",uid,"token_version",0));
    db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,created_by,amount_cents,location_json) VALUES(?,0,'ticket','预约','confirmed',?,55000,'{\"longitude\":116.0,\"latitude\":39.0}')",id,uid);
    var http=java.net.http.HttpClient.newHttpClient();String url="http://localhost:"+port+"/api/trips/"+id+"/planning-input";
    var get=java.net.http.HttpRequest.newBuilder(java.net.URI.create(url)).header("Authorization","Bearer "+token).GET().build();
    var actual=json.readTree(http.send(get,java.net.http.HttpResponse.BodyHandlers.ofString()).body()).path("data");
    var in=(ObjectNode)actual.path("planning_input").deepCopy();in.put("day_end","21:00");
    ((ObjectNode)in.path("commitments").get(0).path("location")).put("longitude",116).put("latitude",39);
    var body=json.createObjectNode().put("planning_revision",actual.path("planning_revision").asInt());body.set("planning_input",in);
    var put=java.net.http.HttpRequest.newBuilder(java.net.URI.create(url)).header("Authorization","Bearer "+token).header("If-Match","1").header("Content-Type","application/json").PUT(java.net.http.HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
    var accepted=http.send(put,java.net.http.HttpResponse.BodyHandlers.ofString());assertEquals(200,accepted.statusCode(),accepted.body());
    body.put("planning_revision",json.readTree(accepted.body()).path("data").path("planning_revision").asInt());
    ((ObjectNode)in.path("commitments").get(0)).put("amount_cents",55001);
    var malicious=java.net.http.HttpRequest.newBuilder(java.net.URI.create(url)).header("Authorization","Bearer "+token).header("If-Match","1").header("Content-Type","application/json").PUT(java.net.http.HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
    assertEquals(422,http.send(malicious,java.net.http.HttpResponse.BodyHandlers.ofString()).statusCode());
    assertEquals(55000L,db.queryForObject("SELECT amount_cents FROM trip_commitments WHERE trip_id=?",Long.class,id));
  }

}
