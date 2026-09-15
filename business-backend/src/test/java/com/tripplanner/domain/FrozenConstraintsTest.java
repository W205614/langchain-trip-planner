package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Path;
import java.time.LocalDate;
import java.util.stream.Stream;
import org.junit.jupiter.api.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class FrozenConstraintsTest {
  final JsonMapper json = new JsonMapper();
  final PlanRules rules = new PlanRules(json);

  @TestFactory
  Stream<DynamicTest> frozenCases() {
    var corpus = json.readTree(Path.of("../backend/evals/constraint_cases.json"));
    return corpus
        .path("cases")
        .valueStream()
        .map(c -> DynamicTest.dynamicTest(c.path("id").asText(), () -> verify(c)));
  }

  void verify(JsonNode c) {
    int dayCount = c.path("days").size();
    String transport = c.path("transportation").asText("步行");
    var request =
        json.createObjectNode()
            .put("city", "合成测试城市")
            .put("start_date", "2026-09-11")
            .put("end_date", LocalDate.of(2026, 9, 11).plusDays(dayCount - 1).toString())
            .put("travel_days", dayCount)
            .put("transportation", transport)
            .put("accommodation", "测试");
    if (c.has("constraints")) request.set("constraints", c.path("constraints"));
    request = TripRequests.normalize(request);
    var plan =
        json.createObjectNode()
            .put("city", "合成测试城市")
            .put("start_date", "2026-09-11")
            .put("end_date", request.path("end_date").asText());
    var days = plan.putArray("days");
    int index = 0;
    for (var names : c.path("days")) {
      var day =
          days.addObject()
              .put("date", LocalDate.of(2026, 9, 11).plusDays(index).toString())
              .put("day_index", index++)
              .put("transportation", transport)
              .put("accommodation", "测试");
      var attrs = day.putArray("attractions");
      for (var name : names) {
        var a =
            attrs
                .addObject()
                .put("name", name.asText())
                .put("poi_id", name.asText())
                .put("visit_duration", 120);
        a.putObject("location")
            .put("longitude", 116 + (name.asText().charAt(0) - 65) / 100.0)
            .put("latitude", 39);
      }
      var meals = day.putArray("meals");
      for (String type : new String[] {"breakfast", "lunch", "dinner"})
        meals.addObject().put("type", type).put("name", type);
    }
    var result =
        rules.finish(
            plan,
            request,
            (l, r, t, city) ->
                c.path("route").asText("").equals("unavailable")
                    ? json.createObjectNode()
                    : json.createObjectNode().put("duration", 1200).put("distance", 2000),
            true);
    var selected = json.createArrayNode();
    for (var d : plan.path("days")) {
      var names = selected.addArray();
      d.path("attractions").forEach(a -> names.add(a.path("poi_id").asText()));
    }
    assertEquals(c.path("expected"), selected);
    assertEquals(c.path("passed").asBoolean(), result.path("passed").asBoolean());
    assertFalse(result.path("facts_complete").asBoolean());
    if (c.path("unknown_route").asBoolean(false))
      assertTrue(result.path("day_checks").get(0).path("route_minutes").isNull());
    if (c.path("unknown_walking").asBoolean(false))
      assertTrue(result.path("day_checks").get(0).path("inter_stop_walking_km").isNull());
  }
}
