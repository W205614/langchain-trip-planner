package com.tripplanner.domain;

import java.time.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Shared definitions: absence and estimates never prove a schedule feasible. */
public final class PlanningSemantics {
  public static final JsonNode CONTRACT;
  static {
    try (var stream=PlanningSemantics.class.getResourceAsStream("/contracts/planning-semantics.json")) {
      if(stream==null)throw new IllegalStateException("Planning contract missing");
      CONTRACT=new JsonMapper().readTree(stream);
    } catch(Exception ex) {throw new ExceptionInInitializerError(ex);}
  }
  private PlanningSemantics() {}
  public static String city(JsonNode request,JsonNode day) {
    int index=day.path("day_index").asInt(0);
    for(var segment:request.path("planning_input").path("city_segments"))
      if(index>=segment.path("start_day").asInt(0)&&index<=segment.path("end_day").asInt(0))return segment.path("city").asText("");
    return day.path("city").asText("").isBlank()?request.path("city").asText(""):day.path("city").asText("");
  }
  public static boolean current(JsonNode fact,String kind) {
    try {
      var now=Instant.now();var observed=OffsetDateTime.parse(fact.path("observed_at").asText("")).toInstant();
      var expiry=observed.plusSeconds(CONTRACT.path("fact_ttl_seconds").path(kind).asLong(86400));
      if(fact.hasNonNull("expires_at")){var declared=OffsetDateTime.parse(fact.path("expires_at").asText("")).toInstant();if(declared.isBefore(expiry))expiry=declared;}
      return fact.path("confirmed").asBoolean(false)&&!fact.path("source").asText("").isBlank()&&!observed.isAfter(now)&&expiry.isAfter(now);
    }catch(Exception ex){return false;}
  }
}
