package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.AssistantClarificationService;
import com.tripplanner.persistence.AssistantMapper;
import java.net.URI;
import java.net.http.*;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
  "trip.jwt-secret=integration-secret-only-01234567890123456789",
  "trip.internal-key=integration-internal-only-01234567890123456789",
  "spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip",
  "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}",
  "APP_ENV=validation","VALIDATION_ALLOW_FIXTURES=yes","WORKERS_ENABLED=false"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class AssistantClarificationIntegrationTest {
  @Value("${local.server.port}")int port;
  @Autowired JdbcTemplate db;
  @Autowired JsonMapper json;
  @Autowired AssistantMapper conversations;
  @MockitoBean AgentClient agent;
  final HttpClient http=HttpClient.newHttpClient();

  @BeforeEach void fixture(){
    when(agent.available()).thenReturn(true);
    when(agent.post(eq("/capabilities/assistant-intent"),any())).thenAnswer(inv->{
      Map<?,?> body=inv.getArgument(1);String text=body.get("content").toString();
      var result=json.createObjectNode().put("operation","propose_change").put("confirmation_required",true);
      if(text.contains("第2天")){result.put("target_day",1);result.putArray("missing_slots");result.putObject("constraints_patch").put("pace","relaxed");}
      else {result.putNull("target_day");result.putArray("missing_slots").add("target_day");result.put("reply","请补充日期");result.putObject("constraints_patch");}
      var envelope=json.createObjectNode();envelope.set("data",result);return envelope;
    });
  }
  HttpResponse<String> call(String method,String path,String token,String key,String body)throws Exception{
    var builder=HttpRequest.newBuilder(URI.create("http://localhost:"+port+path)).header("Content-Type","application/json");
    if(token!=null)builder.header("Authorization","Bearer "+token);
    if(key!=null)builder.header("Idempotency-Key",key);
    return http.send(builder.method(method,body==null?HttpRequest.BodyPublishers.noBody():HttpRequest.BodyPublishers.ofString(body)).build(),HttpResponse.BodyHandlers.ofString());
  }
  String user(String name)throws Exception{
    var response=call("POST","/api/auth/register",null,null,json.writeValueAsString(Map.of("username",name,"password","Safe!Pass_2026_47")));
    assertEquals(200,response.statusCode(),response.body());return json.readTree(response.body()).path("access_token").asText();
  }
  long trip(long uid){return db.queryForObject("""
    INSERT INTO trip_records(user_id,city,start_date,end_date,travel_days,transportation,accommodation,preferences,
      free_text_input,plan_json,quality_json) VALUES(?,'北京','2026-12-01','2026-12-03',3,'步行','经济','[]','',
      '{"city":"北京","start_date":"2026-12-01","end_date":"2026-12-03","days":[],"constraints":{}}',
      '{"outcome":"complete"}') RETURNING id
    """,Long.class,uid);}
  String conversation(String token,long trip)throws Exception{
    var response=call("POST","/api/assistant/conversations",token,null,"{\"active_trip_id\":"+trip+"}");
    assertEquals(200,response.statusCode(),response.body());return json.readTree(response.body()).path("id").asText();
  }
  HttpResponse<String> message(String token,String id,String key,String content)throws Exception{
    return call("POST","/api/assistant/conversations/"+id+"/messages",token,key,json.writeValueAsString(Map.of("mode","auto","content",content)));}

  @Test void persistsClarificationAcrossServiceInstanceThenCreatesOnlyProposalTask()throws Exception{
    String name="clarify_"+UUID.randomUUID().toString().substring(0,10),token=user(name);
    long uid=db.queryForObject("SELECT id FROM users WHERE username=?",Long.class,name),trip=trip(uid);
    String id=conversation(token,trip);
    var first=message(token,id,"first","当天轻松一些");assertEquals(202,first.statusCode(),first.body());
    assertEquals("needs_clarification",json.readTree(first.body()).path("status").asText());
    var restarted=new AssistantClarificationService(conversations,json,900);
    assertEquals("当天轻松一些",restarted.snapshot(uid,id).get("original_request"));
    var second=message(token,id,"continuation","第2天");assertEquals(202,second.statusCode(),second.body());
    String task=json.readTree(second.body()).path("task_id").asText();assertFalse(task.isBlank());
    JsonNode request=json.readTree(db.queryForObject("SELECT request_json FROM trip_tasks WHERE id=?",String.class,task));
    assertTrue(request.path("_revision").path("instruction").asText().contains("轻松"));
    assertEquals(1,request.path("_revision").path("day_index").asInt());
    assertEquals(id,request.path("_revision").path("assistant_conversation_id").asText());
    assertEquals(1,db.queryForObject("SELECT version FROM trip_records WHERE id=?",Integer.class,trip));
    assertEquals("consumed",restarted.snapshot(uid,id).get("status"));
    var duplicate=message(token,id,"continuation","第2天");assertEquals(task,json.readTree(duplicate.body()).path("task_id").asText());
    assertEquals(1L,db.queryForObject("SELECT count(*) FROM trip_tasks WHERE user_id=?",Long.class,uid));
    assertEquals(409,message(token,id,"continuation","第3天").statusCode());
  }

  @Test void rejectsCrossUserAndExpiresOrCancelsWithoutCreatingTask()throws Exception{
    String name="clarify_"+UUID.randomUUID().toString().substring(0,10),token=user(name),other=user("other_"+UUID.randomUUID().toString().substring(0,10));
    long uid=db.queryForObject("SELECT id FROM users WHERE username=?",Long.class,name);String id=conversation(token,trip(uid));
    assertEquals(202,message(token,id,"first","轻松一点").statusCode());
    assertEquals(404,message(other,id,"other","第2天").statusCode());
    db.update("UPDATE assistant_clarifications SET expires_at=now()-interval '1 second' WHERE conversation_id=?",id);
    var expired=message(token,id,"expired","第2天");assertEquals("needs_clarification",json.readTree(expired.body()).path("status").asText());
    assertTrue(json.readTree(expired.body()).path("message").asText().contains("过期"));
    assertEquals(202,message(token,id,"new","轻松一点").statusCode());
    var cancelled=message(token,id,"cancel","取消");assertEquals("cancelled",json.readTree(cancelled.body()).path("status").asText());
    assertEquals(0L,db.queryForObject("SELECT count(*) FROM trip_tasks WHERE user_id=?",Long.class,uid));
  }
}
