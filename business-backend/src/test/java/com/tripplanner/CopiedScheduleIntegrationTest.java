package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.*;
import com.tripplanner.security.TokenService;
import java.net.URI;
import java.net.http.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={"trip.jwt-secret=integration-secret-only-01234567890123456789","trip.internal-key=integration-internal-only-01234567890123456789","spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip","spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class CopiedScheduleIntegrationTest {
  @Value("${local.server.port}") int port;
  @Autowired JdbcTemplate db;@Autowired JsonMapper json;@Autowired PlanningInputService planning;@Autowired ScheduleDependencies schedules;@Autowired TransactionTemplate tx;@Autowired TokenService tokens;
  @MockitoBean AgentClient agent;
  final HttpClient http=HttpClient.newHttpClient();
  long user(){return db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'test-only') RETURNING id",Long.class,"copy_"+UUID.randomUUID());}
  String credential(long user){return tokens.issue(Map.of("id",user,"token_version",0));}
  JsonNode request(String method,String path,String body,String token,int expected)throws Exception{
    var request=HttpRequest.newBuilder(URI.create("http://127.0.0.1:"+port+path));
    if(token!=null)request.header("Authorization","Bearer "+token);
    if(body!=null)request.header("Content-Type","application/json");
    if(path.contains("/activities/"))request.header("If-Match","1");
    request.method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofString(body));
    var response=http.send(request.build(),HttpResponse.BodyHandlers.ofString());assertEquals(expected,response.statusCode(),response.body());return json.readTree(response.body());
  }
  Map<String,Object> snapshot(long id){return db.queryForMap("SELECT planning_input::text,planning_revision,version,plan_json,quality_json FROM trip_records WHERE id=?",id);}
  @Test void communityAndShareCopiesInvalidateBindingsEvenWhenEffectiveInputsAreIdenticalAndNeverChangeSource()throws Exception{
    long owner=user(),receiver=user();String ownerToken=credential(owner),receiverToken=credential(receiver);
    var plan=(ObjectNode)json.readTree("{\"city\":\"北京\",\"start_date\":\"2026-11-01\",\"end_date\":\"2026-11-01\",\"days\":[{\"day_index\":0,\"date\":\"2026-11-01\",\"city\":\"北京\",\"attractions\":[{\"activity_id\":\"poi:0:p0\",\"poi_id\":\"p0\",\"name\":\"待保留景点\",\"visit_duration\":60}],\"meals\":[],\"feasibility\":{\"status\":\"feasible\"},\"timeline\":[{\"activity_id\":\"poi:0:p0\",\"kind\":\"attraction\",\"start_at\":\"2026-11-01T18:00:00+08:00\",\"end_at\":\"2026-11-01T19:00:00+08:00\",\"status\":\"confirmed\"}]}]}");
    PlanningInputService.stableIds(plan);
    long source=db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,travel_days,plan_json,quality_json) VALUES(?,'北京','2026-11-01','2026-11-01',1,?,'{\"outcome\":\"complete\"}') RETURNING id",Long.class,owner,json.writeValueAsString(plan));
    tx.executeWithoutResult(status->{planning.bindPersisted(owner,source,plan,null);planning.storeBoundPlan(source,plan);});
    var original=snapshot(source);var sourceInput=planning.input(planning.row(owner,source,false));var sourceDay=plan.path("days").get(0);
    assertTrue(schedules.current(sourceDay,sourceInput,Instant.now()));
    var share=request("POST","/api/trips/"+source+"/shares","{}",ownerToken,200);long shareId=share.path("id").asLong();String shareToken=share.path("token").asText();
    var submitted=request("POST","/api/community/cards","{\"record_id\":"+source+",\"title\":\"隔离复制反例\"}",ownerToken,200);long cardId=submitted.path("id").asLong();
    // Publication is fixture setup here; the browser test exercises real admin review.
    db.update("UPDATE community_trip_cards SET status='published',published_at=timezone('UTC',now()) WHERE id=?",cardId);
    String shareSnapshot=db.queryForObject("SELECT snapshot_json FROM trip_shares WHERE id=?",String.class,shareId);
    String communitySnapshot=db.queryForObject("SELECT snapshot_json FROM community_trip_cards WHERE id=?",String.class,cardId);
    for(String path:List.of("/api/community/cards/"+cardId+"/copy","/api/shared-trips/"+shareToken+"/copy")){
      long copied=request("POST",path,"{}",receiverToken,200).path("id").asLong();
      var row=planning.row(receiver,copied,false);var copiedInput=planning.input(row);var copiedPlan=(ObjectNode)json.readTree(row.get("plan_json").toString());var copiedDay=copiedPlan.path("days").get(0);
      assertEquals(schedules.fingerprint(sourceInput,sourceDay),schedules.fingerprint(copiedInput,sourceDay),"Counterexample must have identical effective planning inputs");
      assertFalse(copiedDay.has("schedule_dependencies"));assertTrue(copiedDay.path("schedule_stale").asBoolean(false));assertFalse(schedules.current(copiedDay,copiedInput,Instant.now()));
      var quality=json.readTree(row.get("quality_json").toString());assertEquals("draft",quality.path("outcome").asText());
      assertTrue(quality.path("data_gaps").valueStream().anyMatch(gap->gap.asText().equals("copied_plan_requires_reverification")));
      assertEquals(sourceDay.path("attractions"),copiedDay.path("attractions"));
      var before=snapshot(copied);
      for(String action:List.of("\"locked\":true,\"lock_mode\":\"time\"","\"completed\":true")){
        var denied=request("POST","/api/trips/"+copied+"/activities/poi:0:p0/state","{\"planning_revision\":1,"+action+"}",receiverToken,409);
        assertEquals("SCHEDULE_STALE",denied.path("code").asText());
      }
      assertEquals(before,snapshot(copied));assertEquals(original,snapshot(source));
      assertEquals(shareSnapshot,db.queryForObject("SELECT snapshot_json FROM trip_shares WHERE id=?",String.class,shareId));
      assertEquals(communitySnapshot,db.queryForObject("SELECT snapshot_json FROM community_trip_cards WHERE id=?",String.class,cardId));
    }
  }
}
