package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.util.*;
import tools.jackson.databind.JsonNode;

/** The final business decision must be grounded in fetched facts, not model names. */
public final class TrustedCandidates {
  private TrustedCandidates() {}

  public static void verify(JsonNode plan, JsonNode candidates, JsonNode original) {
    if (!candidates.isArray() || candidates.size() > 5000)
      throw new ApiException(503, "可信候选证据缺失", "AGENT_PROTOCOL_ERROR");
    var facts = new HashMap<String, JsonNode>();
    if (original != null)
      original
          .path("days")
          .forEach(
              d -> d.path("attractions").forEach(a -> facts.put(a.path("poi_id").asText(""), a)));
    candidates.forEach(p -> facts.put(p.path("id").asText(""), p));
    for (var day : plan.path("days"))
      for (var attraction : day.path("attractions")) {
        var fact = facts.get(attraction.path("poi_id").asText(""));
        if (fact == null
            || !fact.path("name").equals(attraction.path("name"))
            || !coordinates(fact.path("location"), attraction.path("location")))
          throw new ApiException(503, "结果景点与可信候选不一致", "TRUSTED_POI_UNAVAILABLE");
      }
  }

  private static boolean coordinates(JsonNode a, JsonNode b) {
    for (String field : List.of("latitude", "longitude"))
      if (!a.path(field).isNumber()
          || !b.path(field).isNumber()
          || Math.abs(a.path(field).asDouble() - b.path(field).asDouble()) > 0.0000001)
        return false;
    return true;
  }
}
