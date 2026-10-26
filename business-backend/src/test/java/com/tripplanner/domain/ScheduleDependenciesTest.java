package com.tripplanner.domain;
import static org.junit.jupiter.api.Assertions.*;
import java.time.Instant;
import java.util.Set;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class ScheduleDependenciesTest {
  final JsonMapper json=JsonMapper.builder().build();
  final ScheduleDependencies rules=new ScheduleDependencies(json);
  ObjectNode input(){return PlanningInputService.normalize(json.createObjectNode(),2);}
  ObjectNode plan(){var p=json.createObjectNode();var days=p.putArray("days");for(int i=0;i<2;i++){
    var day=days.addObject().put("day_index",i).put("date","2026-11-0"+(i+1)).put("city",i==0?"北京":"上海");
    day.putArray("attractions").addObject().put("poi_id","p"+i);day.putArray("meals");day.putArray("timeline").addObject().put("kind","attraction");day.putObject("feasibility").put("status","feasible");}return p;}
  @Test void dayWindowChangeInvalidatesOnlyDependentDay(){var input=input();var p=plan();rules.stamp(p,input,1,null);var untouched=p.path("days").get(0).deepCopy();
    var changed=input.deepCopy();changed.withArray("day_windows").addObject().put("day_index",1).put("start","09:00").put("end","16:00");
    var quality=json.createObjectNode().put("outcome","complete");rules.changed(p,quality,input,changed);
    assertEquals(untouched,p.path("days").get(0));assertTrue(p.path("days").get(1).path("schedule_stale").asBoolean());assertEquals("degraded",quality.path("outcome").asText());}
  @Test void changingFactsOrExpiringThemRejectsPriorBinding(){var input=input();var p=plan();rules.stamp(p,input,3,null);var day=(ObjectNode)p.path("days").get(0);
    assertTrue(rules.current(day,input,Instant.now()));((ObjectNode)day.path("attractions").get(0)).put("opening_hours","changed");assertFalse(rules.current(day,input,Instant.now()));
    p=plan();rules.stamp(p,input,3,null);day=(ObjectNode)p.path("days").get(0);((ObjectNode)day.path("schedule_dependencies")).put("expires_at",Instant.EPOCH.toString());assertFalse(rules.current(day,input,Instant.now()));}
  @Test void metadataAndContentLocksDoNotInvalidateOtherDays(){var input=input();var p=plan();rules.stamp(p,input,1,null);var original=p.deepCopy();
    var changed=input.deepCopy();changed.withArray("locks").addObject().put("activity_id","poi:0:p0").put("mode","content");changed.withArray("completed_activity_ids").add("poi:0:p0");
    rules.changed(p,json.createObjectNode(),input,changed);assertEquals(original,p);assertTrue(rules.current(p.path("days").get(1),changed,Instant.now()));}
  @Test void legacyUnboundTimelineIsExplicitlyUnverified(){var p=plan();var quality=json.createObjectNode().put("outcome","complete");rules.refresh(p,quality,input());assertTrue(quality.path("schedule_stale").asBoolean());assertEquals("unverified",p.path("days").get(0).path("feasibility").path("status").asText());}
  @Test void partialReplanDoesNotRestampUnchangedDates(){var input=input();var p=plan();rules.stamp(p,input,1,null);var original=p.path("days").get(1).deepCopy();rules.stamp(p,input,2,Set.of(0));assertEquals(original,p.path("days").get(1));}
  @Test void changingOnlyTheTimelineCannotRetainAValidBinding(){var input=input();var p=plan();rules.stamp(p,input,1,null);var day=(ObjectNode)p.path("days").get(0);((ObjectNode)day.path("timeline").get(0)).put("start_at","2026-11-01T23:00:00Z");assertFalse(rules.current(day,input,Instant.now()));}
  @Test void aDeclaredLongExpiryCannotExtendTheSharedFactTtl(){var input=input();var p=plan();var now=Instant.now();
    input.withArray("reservation_facts").addObject().put("poi_id","p0").put("date","2026-11-01").put("confirmed",true).put("source","user notice").put("observed_at",now.minusSeconds(21000).toString()).put("expires_at",now.plusSeconds(86400).toString());
    rules.stamp(p,input,1,null);var expiry=Instant.parse(p.path("days").get(0).path("schedule_dependencies").path("expires_at").asText());assertFalse(expiry.isAfter(now.plusSeconds(600)));}
  @Test void aNearExpiryRouteCheckpointDoesNotReceiveAnotherThirtyMinutes(){var input=input();var p=plan();String expiry=Instant.now().plusSeconds(5).toString();((ObjectNode)p.path("days").get(0)).put("route_fact_expires_at",expiry);rules.stamp(p,input,1,null);assertEquals(expiry,p.path("days").get(0).path("schedule_dependencies").path("expires_at").asText());}
  @Test void manualClientCannotRenewBindingsOrChangeOnlyTheTimeline(){var original=PlanningInputService.stableIds(plan());rules.stamp(original,input(),1,null);
    var service=new PlanningInputService(org.mockito.Mockito.mock(org.springframework.jdbc.core.JdbcTemplate.class),json,org.mockito.Mockito.mock(org.springframework.transaction.support.TransactionTemplate.class),rules);
    var client=original.deepCopy();((ObjectNode)client.path("days").get(0).path("schedule_dependencies")).put("expires_at",Instant.now().plusSeconds(999999).toString());
    service.invalidateManualSchedule(original,client,json.createObjectNode(),null);assertEquals(original.path("days").get(0).path("schedule_dependencies"),client.path("days").get(0).path("schedule_dependencies"));
    ((ObjectNode)client.path("days").get(0).path("timeline").get(0)).put("start_at","2026-11-01T23:00:00Z");
    service.invalidateManualSchedule(original,client,json.createObjectNode(),null);assertTrue(client.path("days").get(0).path("schedule_stale").asBoolean());assertFalse(client.path("days").get(0).has("schedule_dependencies"));}
  @Test void inlineAndNumericPersistedTransportAliasesBindTheirBoardingFactsToTheAffectedDay(){
    var input=input();var p=plan();
    input.withArray("commitments").addObject().put("id",123).put("input_commitment_id","inline-booking-uuid").put("day_index",0).put("category","transport");
    input.withArray("transport_buffers").addObject().put("commitment_id","inline-booking-uuid").put("minutes",30);
    input.withArray("transport_buffers").addObject().put("commitment_id","123").put("minutes",30);
    assertEquals(2,rules.effective(input,p.path("days").get(0)).path("transport_buffers").size());
    rules.stamp(p,input,1,null);var untouched=p.path("days").get(1).deepCopy();
    var changed=input.deepCopy();((ObjectNode)changed.path("transport_buffers").get(0)).put("minutes",60);
    rules.changed(p,json.createObjectNode().put("outcome","complete"),input,changed);
    assertTrue(p.path("days").get(0).path("schedule_stale").asBoolean(false));assertEquals(untouched,p.path("days").get(1));
  }
  @Test void aCrossMidnightTransportBufferAlsoBindsTheActualArrivalDate(){
    var input=input();var p=plan();
    input.withArray("commitments").addObject().put("id","persisted-string-id").put("input_commitment_id","inline-overnight").put("day_index",0).put("category","transport")
        .put("start_at","2026-11-01T23:00:00+08:00").put("end_at","2026-11-02T08:00:00+08:00");
    input.withArray("transport_buffers").addObject().put("commitment_id","inline-overnight").put("minutes",30);
    assertEquals(1,rules.effective(input,p.path("days").get(1)).path("transport_buffers").size());
    rules.stamp(p,input,1,null);var changed=input.deepCopy();((ObjectNode)changed.path("transport_buffers").get(0)).put("minutes",45);
    rules.changed(p,json.createObjectNode().put("outcome","complete"),input,changed);
    assertTrue(p.path("days").get(0).path("schedule_stale").asBoolean(false));assertTrue(p.path("days").get(1).path("schedule_stale").asBoolean(false));
  }
}
