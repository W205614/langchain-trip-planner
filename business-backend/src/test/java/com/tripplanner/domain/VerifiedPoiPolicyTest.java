package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class VerifiedPoiPolicyTest {
  final JsonMapper json = JsonMapper.builder().build();

  private ObjectNode plan(String... attractionIds) {
    var plan = json.createObjectNode().put("city", "北京");
    var days = plan.putArray("days");
    for (int dayIndex = 0; dayIndex < attractionIds.length; dayIndex++) {
      var day = days.addObject().put("day_index", dayIndex);
      var attractions = day.putArray("attractions");
      if (!attractionIds[dayIndex].isBlank()) {
        var attraction = attractions.addObject();
        attraction.put("poi_id", attractionIds[dayIndex]).put("name", attractionIds[dayIndex]);
        attraction.putObject("location").put("longitude", 116.4).put("latitude", 39.9);
      }
    }
    return plan;
  }

  private ObjectNode poi(String id) {
    var poi =
        json.createObjectNode()
            .put("id", id)
            .put("name", "景点-" + id)
            .put("type", "风景名胜")
            .put("city", "北京市")
            .put("address", "可信地址")
            .put("opening_hours", "");
    poi.putObject("location").put("longitude", 116.4).put("latitude", 39.9);
    poi.putArray("photos");
    return poi;
  }

  private ObjectNode request() {
    var request = json.createObjectNode();
    request.putObject("constraints").putArray("avoid");
    return request;
  }

  @Test
  void replacesJavaRejectedSelectionWithAnotherVerifiedCandidate() {
    var plan = plan("good", "bad");
    var candidates =
        json.readTree(
            """
            [{"id":"bad","name":"坏候选"},{"id":"replacement","name":"可信候选"}]
            """);
    Map<String, ObjectNode> facts = Map.of("good", poi("good"), "replacement", poi("replacement"));

    var result =
        VerifiedPoiPolicy.reconcile(
            plan,
            candidates,
            request(),
            "北京",
            id -> {
              if (!facts.containsKey(id)) throw new IllegalArgumentException("not found");
              return facts.get(id);
            },
            json);

    assertTrue(result.fullyRepaired());
    assertEquals(1, result.replacements().size());
    var repairedDay = plan.path("days").get(1);
    assertEquals("replacement", repairedDay.path("attractions").get(0).path("poi_id").asText());
    assertEquals("amap_rest", repairedDay.path("attractions").get(0).path("fact_source").asText());
    assertEquals("fallback", repairedDay.path("generation_mode").asText());
  }

  @Test
  void fillsAnAgentEmptyDayWithoutReusingAnotherDaysPoi() {
    var plan = plan("used", "");
    var candidates =
        json.readTree(
            """
            [{"id":"used","name":"已使用"},{"id":"spare","name":"备用"}]
            """);

    var result =
        VerifiedPoiPolicy.reconcile(
            plan, candidates, request(), "北京", this::poi, json);

    assertTrue(result.fullyRepaired());
    assertEquals("spare", plan.path("days").get(1).path("attractions").get(0).path("poi_id").asText());
  }

  @Test
  void reportsUnfilledDayWhenNoCandidateCanBeVerified() {
    var plan = plan("");
    var candidates = json.readTree("[{\"id\":\"bad\",\"name\":\"坏候选\"}]");

    var result =
        VerifiedPoiPolicy.reconcile(
            plan,
            candidates,
            request(),
            "北京",
            id -> {
              throw new IllegalArgumentException("not found");
            },
            json);

    assertFalse(result.fullyRepaired());
    assertFalse(result.safeToPersist());
    assertEquals(1, result.unfilledSlots());
    assertTrue(plan.path("days").get(0).path("attractions").isEmpty());
  }

  @Test
  void doesNotUseAnAvoidedCandidateForRepair() {
    var plan = plan("");
    var request = request();
    ((ObjectNode) request.path("constraints")).withArray("avoid").add("景点-blocked");
    var candidates = json.readTree("[{\"id\":\"blocked\",\"name\":\"景点-blocked\"}]");

    var result =
        VerifiedPoiPolicy.reconcile(plan, candidates, request, "北京", this::poi, json);

    assertFalse(result.fullyRepaired());
    assertFalse(result.safeToPersist());
    assertTrue(plan.path("days").get(0).path("attractions").isEmpty());
  }

  @Test
  void boundsJavaRestLookupsWhenAReportedCandidatePoolIsBad() {
    var plan = plan("");
    var candidates = json.createArrayNode();
    for (int index = 0; index < 100; index++)
      candidates.addObject().put("id", "bad-" + index).put("name", "坏候选" + index);
    var calls = new AtomicInteger();

    var result =
        VerifiedPoiPolicy.reconcile(
            plan,
            candidates,
            request(),
            "北京",
            id -> {
              calls.incrementAndGet();
              throw new IllegalArgumentException("not found");
            },
            json);

    assertFalse(result.fullyRepaired());
    assertFalse(result.safeToPersist());
    assertEquals(24, calls.get());
  }

  @Test
  void partialReductionIsPersistableWhenEveryDayStillHasAVerifiedPoi() {
    var plan = json.createObjectNode().put("city", "北京");
    var day = plan.putArray("days").addObject().put("day_index", 0);
    var attractions = day.putArray("attractions");
    for (String id : new String[] {"good", "bad"}) {
      var attraction = attractions.addObject().put("poi_id", id).put("name", id);
      attraction.putObject("location").put("longitude", 116.4).put("latitude", 39.9);
    }
    var candidates = json.readTree("[{\"id\":\"good\",\"name\":\"good\"},{\"id\":\"bad\",\"name\":\"bad\"}]");

    var result =
        VerifiedPoiPolicy.reconcile(
            plan,
            candidates,
            request(),
            "北京",
            id -> {
              if (id.equals("bad")) throw new IllegalArgumentException("not found");
              return poi(id);
            },
            json);

    assertFalse(result.fullyRepaired());
    assertTrue(result.safeToPersist());
    assertEquals(1, result.unfilledSlots());
    assertEquals(1, day.path("attractions").size());
    assertEquals("fallback", day.path("generation_mode").asText());
  }
}
