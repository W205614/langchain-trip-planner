package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class PlanRulesTest {
  final JsonMapper json = new JsonMapper();
  final PlanRules rules = new PlanRules(json);

  ObjectNode request() {
    return TripRequests.normalize(
        (ObjectNode)
            json.readTree(
                """
                {"city":"北京","start_date":"2026-10-01","end_date":"2026-10-01","travel_days":1,
                 "transportation":"步行","accommodation":"经济型酒店"}
                """));
  }

  ObjectNode plan() {
    return (ObjectNode)
        json.readTree(
            """
{"city":"北京","start_date":"2026-10-01","end_date":"2026-10-01","days":[
{"date":"2026-10-01","day_index":0,"transportation":"步行","accommodation":"经济型酒店","attractions":[
{"poi_id":"a","name":"故宫","location":{"longitude":116.4,"latitude":39.9},"visit_duration":120,"ticket_price":0,"price_source":"explicit_free"},
{"poi_id":"b","name":"天坛","location":{"longitude":116.4,"latitude":39.8},"visit_duration":120,"ticket_price":0,"price_source":"unknown"}],
"meals":[{"type":"breakfast","estimated_cost":20},{"type":"lunch","estimated_cost":30},{"type":"dinner","estimated_cost":30}]}]}
""");
  }

  @Test
  void missingRouteIsNonBlockingDegradationAndExplicitFreeIsNotCharged() {
    var plan = plan();
    var quality = rules.finish(plan, request(), null, false);
    assertEquals("degraded", quality.path("outcome").asText());
    assertEquals("ROUTE_UNAVAILABLE", quality.path("issues").get(0).path("code").asText());
    assertFalse(quality.path("issues").get(0).path("blocking").asBoolean(true));
    assertTrue(quality.path("day_checks").get(0).path("route_minutes").isNull());
    assertEquals(80, plan.path("budget").path("total_attractions").asInt());
    assertEquals(0, plan.path("budget").path("total_hotels").asInt());
  }

  @Test
  void aZeroDurationRouteRemainsKnown() {
    var quality =
        rules.finish(
            plan(),
            request(),
            (l, r, t, c) -> json.readTree("{\"duration\":0,\"distance\":0}"),
            true);
    assertEquals("complete", quality.path("outcome").asText());
    assertTrue(quality.path("route_checked").asBoolean());
    assertEquals(0, quality.path("day_checks").get(0).path("route_minutes").asDouble());
  }

  @Test
  void longRouteRepairsOptionalButPreservesRequiredAttraction() {
    var request = request();
    ((ObjectNode) request.path("constraints")).putArray("must_visit").add("故宫");
    var plan = plan();
    var quality =
        rules.finish(
            plan,
            request,
            (l, r, t, c) -> json.readTree("{\"duration\":15000,\"distance\":30000}"),
            true);
    assertEquals(1, plan.path("days").get(0).path("attractions").size());
    assertEquals("a", plan.path("days").get(0).path("attractions").get(0).path("poi_id").asText());
    // 240 visit + 120 allowances + 250 route exceeds the 600-minute day limit first.
    assertEquals("daily_time_exceeded", quality.path("repairs").get(0).path("reason").asText());
  }

  @Test
  void aSingleAttractionDoesNotPretendThereWasZeroWalking() {
    var plan = plan();
    ((tools.jackson.databind.node.ArrayNode) plan.path("days").get(0).path("attractions")).remove(1);
    var quality = rules.finish(plan, request(), null, false);
    var check = quality.path("day_checks").get(0);
    assertEquals("single_stop", check.path("walking_status").asText());
    assertTrue(check.path("inter_stop_walking_km").isNull());
    assertEquals(0, check.path("routes").size());
  }
}
