package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import com.tripplanner.api.ApiException;
import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={"trip.jwt-secret=integration-secret-only-01234567890123456789","trip.internal-key=integration-internal-only-01234567890123456789","spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip","spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class ScheduleValidityIntegrationTest {
  @Autowired JdbcTemplate db;@Autowired PlanningInputService planning;@Autowired JsonMapper json;@Autowired TransactionTemplate tx;
  @MockitoBean AgentClient agent;
  record Seed(long uid,long id){}

  Seed seed(boolean expiredFact){
    long uid=db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'test-only') RETURNING id",Long.class,"schedule_"+UUID.randomUUID());
    var input=PlanningInputService.normalize(json.createObjectNode(),2);
    if(expiredFact){
      var fact=input.withArray("opening_windows").addObject().put("poi_id","p0").put("date","2026-11-01").put("source","user-provided-venue-notice").put("confirmed",true)
          .put("observed_at",Instant.now().minusSeconds(7200).toString()).put("expires_at",Instant.now().minusSeconds(60).toString());
      fact.putArray("windows").addObject().put("start","09:00").put("end","20:00");
    }
    var plan=json.createObjectNode().put("city","北京").put("start_date","2026-11-01").put("end_date","2026-11-02");
    var days=plan.putArray("days");
    for(int i=0;i<2;i++){
      String date="2026-11-0"+(i+1),hour=i==0?"18":"10",end=i==0?"19":"11";
      var day=days.addObject().put("day_index",i).put("date",date).put("city","北京");
      day.putArray("attractions").addObject().put("activity_id","poi:"+i+":p"+i).put("poi_id","p"+i).put("name","保留景点"+i).put("visit_duration",60)
          .putObject("location").put("longitude",116.4+i*.01).put("latitude",39.9);
      day.putArray("meals");day.putObject("feasibility").put("status","feasible");
      day.putArray("timeline").addObject().put("activity_id","poi:"+i+":p"+i).put("kind","attraction").put("title","保留景点"+i)
          .put("start_at",date+"T"+hour+":00:00+08:00").put("end_at",date+"T"+end+":00:00+08:00").put("status","confirmed");
    }
    PlanningInputService.stableIds(plan);
    long id=db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,travel_days,plan_json,quality_json,planning_input) VALUES(?,'北京','2026-11-01','2026-11-02',2,?,'{\"outcome\":\"complete\",\"feasibility_status\":\"feasible\",\"data_gaps\":[]}',?::jsonb) RETURNING id",Long.class,uid,json.writeValueAsString(plan),json.writeValueAsString(input));
    tx.executeWithoutResult(s->{planning.bindPersisted(uid,id,plan,null);planning.storeBoundPlan(id,plan);});
    return new Seed(uid,id);
  }
  ObjectNode readPlan(Seed seed){return (ObjectNode)json.valueToTree(planning.get(seed.uid,seed.id)).path("data").path("plan");}
  Map<String,Object> snapshot(Seed seed){return db.queryForMap("SELECT planning_input::text,planning_revision,version,plan_json,quality_json FROM trip_records WHERE id=?",seed.id);}
  void assertRejectsWithoutDatabaseChange(Seed seed,int revision){
    var before=snapshot(seed);
    var lock=json.createObjectNode().put("planning_revision",revision).put("locked",true).put("lock_mode","time");
    assertEquals("SCHEDULE_STALE",assertThrows(ApiException.class,()->planning.state(seed.uid,seed.id,1,"poi:0:p0",lock)).code);
    assertEquals(before,snapshot(seed));
    var complete=json.createObjectNode().put("planning_revision",revision).put("completed",true);
    assertEquals("SCHEDULE_STALE",assertThrows(ApiException.class,()->planning.state(seed.uid,seed.id,1,"poi:0:p0",complete)).code);
    assertEquals(before,snapshot(seed));
  }

  @Test void aDayWindowChangeInvalidatesOnlyThatDayAndRejectsOldEveningLocksAtomically(){
    var seed=seed(false);var before=readPlan(seed);var untouched=before.path("days").get(1).deepCopy();
    var input=planning.input(planning.row(seed.uid,seed.id,false));input.withArray("day_windows").addObject().put("day_index",0).put("start","09:00").put("end","16:00");
    planning.put(seed.uid,seed.id,1,1,input);
    var after=readPlan(seed);assertTrue(after.path("days").get(0).path("schedule_stale").asBoolean(false));
    assertEquals("unverified",after.path("days").get(0).path("feasibility").path("status").asText());
    assertEquals(untouched,after.path("days").get(1));
    assertEquals(before.path("days").get(0).path("attractions"),after.path("days").get(0).path("attractions"));
    var projected=planning.view(planning.row(seed.uid,seed.id,false));assertEquals("degraded",json.readTree(projected.get("quality_json").toString()).path("outcome").asText());
    assertRejectsWithoutDatabaseChange(seed,2);
  }

  @Test void bookingTriggerInvalidatesEffectiveDependenciesWithoutRewritingStoredPlan(){
    var seed=seed(false);var unchanged=readPlan(seed).path("days").get(1).deepCopy();
    db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,created_by,start_at,end_at) VALUES(?,0,'ticket','新预约','confirmed',?,'2026-11-01T17:00:00+08:00','2026-11-01T18:00:00+08:00')",seed.id,seed.uid);
    int revision=db.queryForObject("SELECT planning_revision FROM trip_records WHERE id=?",Integer.class,seed.id);assertEquals(2,revision);
    var before=snapshot(seed);var projected=readPlan(seed);
    assertTrue(projected.path("days").get(0).path("schedule_stale").asBoolean(false));assertEquals(unchanged,projected.path("days").get(1));
    assertEquals(before,snapshot(seed));assertRejectsWithoutDatabaseChange(seed,revision);
  }

  @Test void expiredFactBindingIsProjectedStaleAndCannotBeTimeLockedOrCompleted(){
    var seed=seed(true);var before=snapshot(seed);var plan=readPlan(seed);
    assertTrue(plan.path("days").get(0).path("schedule_stale").asBoolean(false));
    assertFalse(plan.path("days").get(1).path("schedule_stale").asBoolean(false));
    assertEquals(before,snapshot(seed));assertRejectsWithoutDatabaseChange(seed,1);
  }
}
