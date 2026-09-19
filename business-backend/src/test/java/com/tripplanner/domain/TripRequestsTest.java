package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.api.ApiException;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class TripRequestsTest {
  final JsonMapper json = new JsonMapper();

  ObjectNode request() {
    return (ObjectNode)
        json.readTree(
            """
            {"city":"北京","start_date":"2026-10-01","end_date":"2026-10-02","travel_days":2,
            "transportation":"公共交通","accommodation":"经济型酒店"}
            """);
  }

  @Test
  void rejectsImpossibleDatesAndMismatchedDayCounts() {
    var r = request();
    r.put("end_date", "2026-02-30");
    assertThrows(ApiException.class, () -> TripRequests.normalize(r));
    var s = request();
    s.put("travel_days", 3);
    assertThrows(ApiException.class, () -> TripRequests.normalize(s));
  }

  @Test
  void splitsDeduplicatesAndRejectsConflictingNames() {
    var r = request();
    r.putObject("constraints").put("must_visit", "故宫，天坛、故宫");
    assertEquals(2, TripRequests.normalize(r).path("constraints").path("must_visit").size());
    ((ObjectNode) r.path("constraints")).put("avoid", "天坛");
    assertThrows(ApiException.class, () -> TripRequests.normalize(r));
  }

  @Test
  void publicInputCannotSmuggleExecutionMetadata() {
    var r = request();
    r.putObject("_revision").put("record_id", 1).put("version", 1);
    r.put("user_id", 999);
    var normalized = TripRequests.normalize(r);
    assertFalse(normalized.has("_revision"));
    assertFalse(normalized.has("user_id"));
  }

  @Test
  void normalizesPlanningFactsAndRejectsTooManyRooms() {
    var r = request();
    r.put("departure_city", "上海").put("traveler_count", 4).put("room_count", 2).put("budget_total", 5000);
    var normalized = TripRequests.normalize(r);
    assertEquals("上海", normalized.path("departure_city").asText());
    assertEquals(4, normalized.path("traveler_count").asInt());
    assertEquals(5000, normalized.path("budget_total").asInt());

    r.put("room_count", 5);
    assertThrows(ApiException.class, () -> TripRequests.normalize(r));
  }
}
