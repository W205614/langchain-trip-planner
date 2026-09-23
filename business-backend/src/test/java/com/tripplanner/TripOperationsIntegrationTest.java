package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

import com.tripplanner.api.ApiException;
import com.tripplanner.domain.AmapGateway;
import com.tripplanner.domain.TripOperationsService;
import com.tripplanner.persistence.UserMapper;
import com.tripplanner.security.TokenService;
import java.net.URI;
import java.net.http.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.json.JsonMapper;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
    "trip.jwt-secret=integration-secret-only-01234567890123456789",
    "trip.internal-key=integration-internal-only-01234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}",
    "spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}",
    "WORKERS_ENABLED=false"
})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class TripOperationsIntegrationTest {
  @Autowired JdbcTemplate db;
  @Autowired JsonMapper json;
  @Autowired TripOperationsService operations;
  @Autowired TokenService tokens;
  @Autowired UserMapper users;
  @Value("${local.server.port}") int port;
  @MockitoBean AmapGateway amap;

  long user(String name) {
    return db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,?) RETURNING id",
        Long.class,name+UUID.randomUUID().toString().substring(0,8),"test-only");
  }

  long trip(long owner) {
    var plan=json.createObjectNode().put("city","北京").put("start_date","2026-10-01").put("end_date","2026-10-01");
    var day=plan.putArray("days").addObject().put("day_index",0).put("date","2026-10-01")
        .put("description","北京一天").put("transportation","步行").put("accommodation","经济型酒店");
    var attractions=day.putArray("attractions");
    for(String id:List.of("A123","B456")) {
      var attraction=attractions.addObject().put("poi_id",id).put("name",id)
          .put("opening_hours","09:00-17:00").put("visit_duration",120)
          .put("fact_source","amap_rest");
      attraction.putObject("location").put("longitude",116.4).put("latitude",39.9);
    }
    day.putArray("meals");
    long id=db.queryForObject("INSERT INTO trip_records(user_id,city,start_date,end_date,travel_days," +
        "transportation,accommodation,preferences,free_text_input,plan_json,quality_json,budget_total) " +
        "VALUES(?,'北京','2026-10-01','2026-10-01',1,'步行','经济','[]','',?,'{}',1000) RETURNING id",
        Long.class,owner,json.writeValueAsString(plan));
    db.update("INSERT INTO trip_record_versions(record_id,user_id,record_version,change_type,title,source,plan_json,quality_json) " +
        "SELECT id,user_id,version,'migration_baseline',title,source,plan_json,quality_json FROM trip_records WHERE id=?",id);
    return id;
  }

  @Test void checksRecordUncertaintyWithoutMutatingPlanAndRespectOwnership() {
    long owner=user("opcheck"), stranger=user("stranger"), id=trip(owner);
    when(amap.detail(anyString())).thenAnswer(call -> json.createObjectNode()
        .put("name",call.getArgument(0).toString()).put("opening_hours","10:00-18:00"));
    var forecast=json.createArrayNode();
    forecast.addObject().put("date","2026-10-01").put("day_weather","晴").put("night_weather","晴");
    when(amap.weather(anyString())).thenReturn(forecast);
    assertEquals(404,assertThrows(ApiException.class,()->operations.runCheck(stranger,id,1)).status);
    var result=operations.runCheck(owner,id,1);
    assertEquals("uncertain",result.get("status"));
    assertEquals(2,db.queryForObject("SELECT count(*) FROM trip_risks WHERE trip_id=?",Integer.class,id));
    assertEquals(1,db.queryForObject("SELECT version FROM trip_records WHERE id=?",Integer.class,id));
    assertTrue(db.queryForObject("SELECT plan_json FROM trip_records WHERE id=?",String.class,id)
        .contains("09:00-17:00"));
    assertNotNull(((Map<?,?>)operations.checks(owner,id)).get("data"));
    assertTrue(json.writeValueAsString(result).contains("hours_changed"));
    assertTrue(json.writeValueAsString(operations.checks(owner,id)).contains("hours_changed"));
  }

  @Test void invitedEditorMustAcceptAndReorderUsesTripVersion() {
    long owner=user("owner"), editor=user("editor"), viewer=user("viewer"), stranger=user("outsider"), id=trip(owner);
    String username=db.queryForObject("SELECT username FROM users WHERE id=?",String.class,editor);
    var invite=json.createObjectNode().put("username",username).put("role","editor");
    operations.invite(owner,id,invite,"test");
    assertEquals(404,assertThrows(ApiException.class,()->operations.workspace(editor,id)).status);
    operations.accept(editor,id,"test");
    String viewerName=db.queryForObject("SELECT username FROM users WHERE id=?",String.class,viewer);
    operations.invite(owner,id,json.createObjectNode().put("username",viewerName).put("role","viewer"),"test");
    operations.accept(viewer,id,"test");
    assertEquals("viewer",((Map<?,?>)((Map<?,?>)operations.workspace(viewer,id)).get("data")).get("role"));
    assertEquals("editor",((Map<?,?>)((Map<?,?>)operations.workspace(editor,id)).get("data")).get("role"));
    var order=json.createObjectNode().put("day_index",0).put("description","同行人调整的主题");
    order.putArray("poi_ids").add("B456").add("A123");
    assertEquals(404,assertThrows(ApiException.class,()->operations.reorder(stranger,id,1,order,"test")).status);
    assertEquals(404,assertThrows(ApiException.class,()->operations.reorder(viewer,id,1,order,"test")).status);
    operations.reorder(editor,id,1,order,"test");
    assertEquals(409,assertThrows(ApiException.class,()->operations.reorder(editor,id,1,order,"test")).status);
    assertEquals(2,db.queryForObject("SELECT version FROM trip_records WHERE id=?",Integer.class,id));
    assertTrue(db.queryForObject("SELECT plan_json FROM trip_records WHERE id=?",String.class,id)
        .contains("同行人调整的主题"));
    assertEquals(editor,db.queryForObject("SELECT actor_id FROM business_audit_events " +
        "WHERE resource_type='trip' AND resource_id=? AND action='trip.user_edit' ORDER BY id DESC LIMIT 1",
        Long.class,Long.toString(id)));
  }

  @Test void changingMemberRoleRequiresAcceptanceAndReinviteSendsFreshNotice() {
    long owner=user("roleowner"), member=user("rolemember"), id=trip(owner);
    String username=db.queryForObject("SELECT username FROM users WHERE id=?",String.class,member);
    var viewer=json.createObjectNode().put("username",username).put("role","viewer");
    var editor=json.createObjectNode().put("username",username).put("role","editor");
    operations.invite(owner,id,viewer,"test");
    operations.accept(member,id,"test");
    assertEquals("viewer",((Map<?,?>)((Map<?,?>)operations.workspace(member,id)).get("data")).get("role"));
    operations.invite(owner,id,editor,"test");
    assertEquals(404,assertThrows(ApiException.class,()->operations.workspace(member,id)).status);
    assertEquals("pending",db.queryForObject("SELECT status FROM trip_members WHERE trip_id=? AND user_id=?",
        String.class,id,member));
    operations.accept(member,id,"test");
    assertEquals("editor",((Map<?,?>)((Map<?,?>)operations.workspace(member,id)).get("data")).get("role"));
    operations.removeMember(owner,id,member,"test");
    operations.invite(owner,id,viewer,"test");
    assertEquals(3,db.queryForObject("SELECT count(*) FROM trip_notifications WHERE user_id=? AND kind='invitation'",
        Integer.class,member));
    assertEquals(404,assertThrows(ApiException.class,()->operations.workspace(member,id)).status);
  }

  @Test void reservationsExpensesIdempotencyAndVoidsKeepAuditTrail() {
    long owner=user("money"), other=user("other"), id=trip(owner);
    var commitment=json.createObjectNode().put("day_index",0).put("category","ticket")
        .put("title","景点门票").put("status","planned").put("amount",120.50).put("note","");
    String key="commitment-"+UUID.randomUUID();
    operations.addCommitment(owner,id,commitment,key,"test");
    operations.addCommitment(owner,id,commitment,key,"test");
    assertEquals(1,db.queryForObject("SELECT count(*) FROM trip_commitments WHERE trip_id=?",Integer.class,id));
    assertEquals(409,assertThrows(ApiException.class,()->operations.addCommitment(owner,id,
        commitment.deepCopy().put("title","不同门票"),key,"test")).status);
    assertEquals(404,assertThrows(ApiException.class,()->operations.commitments(other,id)).status);
    long commitmentId=db.queryForObject("SELECT id FROM trip_commitments WHERE trip_id=?",Long.class,id);
    operations.updateCommitment(owner,id,commitmentId,1,json.createObjectNode()
        .put("status","confirmed").put("amount",120.50).put("note","已预订"),"test");
    var expense=json.createObjectNode().put("day_index",0).put("category","ticket")
        .put("amount",120.50).put("note","实际付款").put("commitment_id",commitmentId);
    operations.addExpense(owner,id,expense,"expense-"+UUID.randomUUID(),"test");
    assertEquals(12050L,((Map<?,?>)((Map<?,?>)operations.expenses(owner,id)).get("summary")).get("actual_cents"));
    long expenseId=db.queryForObject("SELECT id FROM trip_expenses WHERE trip_id=?",Long.class,id);
    operations.voidExpense(owner,id,expenseId,json.createObjectNode().put("reason","录入错误"),"test");
    assertEquals(0L,((Map<?,?>)((Map<?,?>)operations.expenses(owner,id)).get("summary")).get("actual_cents"));
    assertNotNull(db.queryForObject("SELECT voided_at FROM trip_expenses WHERE id=?",java.sql.Timestamp.class,expenseId));
  }

  @Test void usagePolicyAndNotificationsAreUserScoped() {
    long first=user("usage"), second=user("usage"), id=trip(first);
    operations.runCheck(first,id,1);
    operations.usagePolicy(first,json.createObjectNode().put("monthly_token_warning_limit",1000));
    assertEquals(1000L,((Number)((Map<?,?>)((Map<?,?>)operations.usage(first)).get("data"))
        .get("warning_limit")).longValue());
    assertEquals(0,((List<?>)((Map<?,?>)operations.notifications(second)).get("data")).size());
    assertEquals(404,assertThrows(ApiException.class,()->operations.checks(second,id)).status);
  }

  @Test void usageWarningAndDepartureReminderAreDeduplicated() {
    long owner=user("reminder"), id=trip(owner);
    operations.usagePolicy(owner,json.createObjectNode().put("monthly_token_warning_limit",1000));
    db.update("INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,usage_json,deadline_at) " +
        "VALUES(?,?,?,?,?,?,timezone('UTC',now())+interval '1 hour')",
        UUID.randomUUID().toString(),owner,UUID.randomUUID().toString(),"fixture","{}",
        "{\"calls\":1,\"input_tokens\":1000,\"output_tokens\":100}");
    operations.usageWarnings();
    operations.usageWarnings();
    assertEquals(1,db.queryForObject("SELECT count(*) FROM trip_notifications WHERE user_id=? AND kind='usage_warning'",
        Integer.class,owner));
    String tomorrow=java.time.LocalDate.now(java.time.ZoneId.of("Asia/Shanghai")).plusDays(1).toString();
    db.update("UPDATE trip_records SET start_date=?,end_date=? WHERE id=?",tomorrow,tomorrow,id);
    operations.departureReminders();
    operations.departureReminders();
    assertEquals(1,db.queryForObject("SELECT count(*) FROM trip_notifications WHERE user_id=? AND kind='departure'",
        Integer.class,owner));
  }

  @Test void httpEndpointsKeepAuthenticationAndSerializeCheckEvidence() throws Exception {
    long owner=user("http"), outsider=user("httpother"), id=trip(owner);
    String token=tokens.issue(users.byId(owner)), other=tokens.issue(users.byId(outsider));
    var http=HttpClient.newHttpClient();
    URI workspace=URI.create("http://localhost:"+port+"/api/trips/"+id+"/workspace");
    var visible=http.send(HttpRequest.newBuilder(workspace).header("Authorization","Bearer "+token).GET().build(),
        HttpResponse.BodyHandlers.ofString());
    assertEquals(200,visible.statusCode());
    assertEquals("owner",json.readTree(visible.body()).path("data").path("role").asText());
    assertEquals(404,http.send(HttpRequest.newBuilder(workspace).header("Authorization","Bearer "+other).GET().build(),
        HttpResponse.BodyHandlers.ofString()).statusCode());
    URI check=URI.create("http://localhost:"+port+"/api/trips/"+id+"/checks?day_index=0");
    var created=http.send(HttpRequest.newBuilder(check).header("Authorization","Bearer "+token)
        .header("If-Match","1").POST(HttpRequest.BodyPublishers.ofString("{}"))
        .header("Content-Type","application/json").build(),HttpResponse.BodyHandlers.ofString());
    assertEquals(200,created.statusCode(),created.body());
    URI latest=URI.create("http://localhost:"+port+"/api/trips/"+id+"/checks/latest?day_index=0");
    var response=http.send(HttpRequest.newBuilder(latest).header("Authorization","Bearer "+token).GET().build(),
        HttpResponse.BodyHandlers.ofString());
    assertEquals(200,response.statusCode(),response.body());
    assertTrue(json.readTree(response.body()).path("data").path("risks").isArray());
    URI decline=URI.create("http://localhost:"+port+"/api/trips/"+id+"/members/me");
    assertEquals(404,http.send(HttpRequest.newBuilder(decline).header("Authorization","Bearer "+other)
        .DELETE().build(),HttpResponse.BodyHandlers.ofString()).statusCode());
  }
}
