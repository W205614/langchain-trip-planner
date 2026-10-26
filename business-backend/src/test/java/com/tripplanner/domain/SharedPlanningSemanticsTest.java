package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class SharedPlanningSemanticsTest {
  final JsonMapper json=new JsonMapper();
  @Test void packagedContractMatchesPythonContract()throws Exception{
    assertEquals(PlanningSemantics.CONTRACT,json.readTree(Files.readString(Path.of("../backend/app/contracts/planning-semantics.json"))));
  }
  @Test void recheckUsesDayCityAndModeRatherThanRootCity(){
    var request=TripRequests.normalize((ObjectNode)json.readTree("{\"city\":\"北京\",\"start_date\":\"2026-10-01\",\"end_date\":\"2026-10-01\",\"travel_days\":1,\"transportation\":\"公交\",\"accommodation\":\"酒店\"}"));
    var plan=(ObjectNode)json.readTree("{\"city\":\"北京\",\"start_date\":\"2026-10-01\",\"end_date\":\"2026-10-01\",\"days\":[{\"day_index\":0,\"date\":\"2026-10-01\",\"city\":\"上海\",\"transportation\":\"自驾\",\"attractions\":[{\"poi_id\":\"a\",\"name\":\"甲\",\"location\":{\"longitude\":121,\"latitude\":31},\"visit_duration\":60},{\"poi_id\":\"b\",\"name\":\"乙\",\"location\":{\"longitude\":121.1,\"latitude\":31},\"visit_duration\":60}]}]}");
    var calls=new ArrayList<String>();new PlanRules(json).finish(plan,request,(l,r,t,c)->{calls.add(c+":"+t);return json.readTree("{\"duration\":600,\"distance\":1000}");},false);
    assertEquals(List.of("上海:driving"),calls);
  }
  @Test void missingRouteOrUnverifiedTimelineDoesNotProveConflict(){
    var plan=(ObjectNode)json.readTree("{\"days\":[{\"day_index\":0,\"date\":\"2026-10-01\",\"timeline\":[{\"activity_id\":\"a\",\"start_at\":\"2026-10-01T08:00:00+08:00\",\"end_at\":\"2026-10-01T18:00:00+08:00\",\"status\":\"estimated\"}]}]}");
    var request=(ObjectNode)json.readTree("{\"city\":\"北京\",\"planning_input\":{},\"constraints\":{\"daily_minutes\":120}}");var quality=json.createObjectNode().put("outcome","complete");
    new TimelineVerificationService(json).apply(plan,request,null,quality);assertEquals("unverified",quality.path("time_reverification").get(0).path("status").asText());assertNotEquals("draft",quality.path("outcome").asText());
  }
  @Test void confirmedTransportAndRecordedLocalActivityShareOneDailyBudgetEvenWithMissingHotel(){
    var plan=(ObjectNode)json.readTree("{\"days\":[{\"day_index\":0,\"date\":\"2026-10-01\",\"city\":\"上海\",\"timeline\":[{\"activity_id\":\"a\",\"kind\":\"attraction\",\"start_at\":\"2026-10-01T14:00:00+08:00\",\"end_at\":\"2026-10-01T16:00:00+08:00\",\"status\":\"confirmed\"}]}]}");
    var request=(ObjectNode)json.readTree("{\"city\":\"上海\",\"start_date\":\"2026-10-01\",\"transportation\":\"公共交通\",\"constraints\":{\"daily_minutes\":360},\"planning_input\":{\"day_start\":\"07:00\",\"day_end\":\"20:00\",\"commitments\":[{\"id\":\"train\",\"category\":\"transport\",\"status\":\"confirmed\",\"origin_city\":\"北京\",\"destination_city\":\"上海\",\"start_at\":\"2026-10-01T08:00:00+08:00\",\"end_at\":\"2026-10-01T13:00:00+08:00\"}]}}");
    var quality=json.createObjectNode().put("outcome","complete");new TimelineVerificationService(json).apply(plan,request,null,quality);
    assertEquals("infeasible",quality.path("time_reverification").get(0).path("status").asText());
    assertEquals(480,quality.path("time_reverification").get(0).path("known_whole_day_minutes").asLong());
  }
}
