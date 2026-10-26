package com.tripplanner.domain;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Per-day dependencies make input changes and fact expiry explicit without silently replanning. */
@Component
public class ScheduleDependencies {
  private final JsonMapper json;
  private static final List<String> GLOBAL = List.of("timezone", "day_start", "day_end", "arrival_at", "departure_at", "budget_mode", "budget_cents");
  private static final List<String> FACTS = List.of("opening_windows", "reservation_facts", "hotel_stays", "transport_buffers", "poi_relations", "meal_resolutions");
  public ScheduleDependencies(JsonMapper json) { this.json = json; }

  public ObjectNode effective(JsonNode input, JsonNode day) {
    var out = json.createObjectNode();
    for (String field : GLOBAL) if (input.has(field)) out.set(field, input.path(field));
    int index = day.path("day_index").asInt();
    Set<String> pois = new HashSet<>(), commitments = new HashSet<>();
    for (String group : List.of("attractions", "meals")) for (var a : day.path(group)) pois.add(a.path("poi_id").asText(""));
    for (var c : input.path("commitments")) if (commitmentApplies(input,day,c)) {
      for(String field:List.of("id","input_commitment_id")) {
        String identity=c.path(field).asText("").strip();if(!identity.isBlank())commitments.add(identity);
        String persisted=c.path("details_json").path(field).asText("").strip();if(!persisted.isBlank())commitments.add(persisted);
      }
    }
    for (String field : List.of("day_windows", "city_segments", "commitments", "meal_windows", "opening_windows", "reservation_facts", "hotel_stays", "transport_buffers", "poi_relations", "meal_resolutions")) {
      var selected = out.putArray(field);
      for (var item : input.path(field)) {
        boolean applies = switch (field) {
          case "city_segments" -> item.path("start_day").asInt() <= index && item.path("end_day").asInt(-1) >= index;
          case "opening_windows", "reservation_facts" -> item.path("date").asText("").equals(day.path("date").asText("")) && pois.contains(item.path("poi_id").asText(""));
          case "transport_buffers" -> commitments.contains(item.path("commitment_id").asText("").strip());
          case "commitments" -> commitmentApplies(input,day,item);
          case "poi_relations" -> pois.contains(item.path("parent_poi_id").asText("")) || pois.contains(item.path("child_poi_id").asText(""));
          default -> !item.has("day_index") || item.path("day_index").asInt(-1) == index || field.equals("hotel_stays") && item.path("day_index").asInt(-1) == index - 1;
        };
        if (applies) selected.add(item);
      }
    }
    return out;
  }

  /** A persisted booking ID does not erase its inline alias or actual travel dates. */
  private boolean commitmentApplies(JsonNode input,JsonNode day,JsonNode commitment) {
    int index=day.path("day_index").asInt(-1);
    if(commitment.path("day_index").asInt(-1)==index)return true;
    if(!Set.of("transport","train","flight").contains(commitment.path("category").asText("")))return false;
    try{
      var zone=java.time.ZoneId.of(input.path("timezone").asText("Asia/Shanghai"));
      var start=java.time.OffsetDateTime.parse(commitment.path("start_at").asText("")).atZoneSameInstant(zone).toLocalDate();
      var end=java.time.OffsetDateTime.parse(commitment.path("end_at").asText("")).atZoneSameInstant(zone).toLocalDate();
      var date=java.time.LocalDate.parse(day.path("date").asText(""));
      return !date.isBefore(start)&&!date.isAfter(end);
    }catch(Exception ex){return false;}
  }

  public String fingerprint(JsonNode input, JsonNode day) {
    try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical(effective(input, day)).getBytes(StandardCharsets.UTF_8))); }
    catch (Exception ex) { throw new IllegalStateException("Cannot fingerprint schedule dependencies", ex); }
  }
  private String canonical(JsonNode n) {
    if (n.isObject()) { var names = new TreeSet<>(n.propertyNames()); var parts = new ArrayList<String>(); for (String name : names) parts.add(json.writeValueAsString(name) + ":" + canonical(n.path(name))); return "{" + String.join(",", parts) + "}"; }
    if (n.isArray()) { var parts = new ArrayList<String>(); n.forEach(x -> parts.add(canonical(x))); return "[" + String.join(",", parts) + "]"; }
    return n.isNumber() ? new java.math.BigDecimal(n.asText("")).stripTrailingZeros().toPlainString() : n.toString();
  }

  public void stamp(ObjectNode plan, JsonNode input, int revision, Set<Integer> days) {
    for (var raw : plan.path("days")) if (raw instanceof ObjectNode day && (days == null || days.contains(day.path("day_index").asInt())) && !day.path("schedule_stale").asBoolean(false) && !day.path("timeline").isEmpty()) {
      var binding = day.putObject("schedule_dependencies");
      binding.put("planning_revision", revision).put("input_hash", fingerprint(input, day)).put("fact_hash",factHash(day)).put("computed_at", Instant.now().toString());
      String earliest = earliestExpiry(effective(input, day));
      // Routes are volatile even when the upstream format has no declared validity.
      Instant routeExpiry=Instant.now().plusSeconds(1800);
      if(earliest!=null&&Instant.parse(earliest).isBefore(routeExpiry))routeExpiry=Instant.parse(earliest);
      if(day.has("route_fact_expires_at")){
        try{Instant actual=Instant.parse(day.path("route_fact_expires_at").asText(""));if(actual.isBefore(routeExpiry))routeExpiry=actual;}
        catch(Exception ex){routeExpiry=Instant.EPOCH;}
      }
      binding.put("expires_at",routeExpiry.toString());
    }
  }
  private String earliestExpiry(JsonNode effective) {
    Instant earliest = null;
    for (String field : FACTS) for (var fact : effective.path(field)) {
      if(!fact.path("confirmed").asBoolean(false)&&!fact.hasNonNull("expires_at"))continue;
      try {
        String kind=field.equals("opening_windows")?"opening":"verification";
        Instant observed=java.time.OffsetDateTime.parse(fact.path("observed_at").asText("")).toInstant();
        Instant expiry=observed.plusSeconds(PlanningSemantics.CONTRACT.path("fact_ttl_seconds").path(kind).asLong(21600));
        if(fact.hasNonNull("expires_at")){Instant declared=java.time.OffsetDateTime.parse(fact.path("expires_at").asText("")).toInstant();if(declared.isBefore(expiry))expiry=declared;}
        if(observed.isAfter(Instant.now()))return Instant.EPOCH.toString();
        if (earliest == null || expiry.isBefore(earliest)) earliest = expiry;
      } catch (Exception ignored) { return Instant.EPOCH.toString(); }
    }
    return earliest == null ? null : earliest.toString();
  }
  public boolean current(JsonNode day, JsonNode input, Instant now) {
    var deps = day.path("schedule_dependencies");
    if (day.path("schedule_stale").asBoolean(false) || !deps.path("input_hash").asText("").equals(fingerprint(input, day)) || !deps.path("fact_hash").asText("").equals(factHash(day))) return false;
    try { return deps.hasNonNull("expires_at") && Instant.parse(deps.path("expires_at").asText("")).isAfter(now); }
    catch (Exception ex) { return false; }
  }
  private String factHash(JsonNode day){
    var facts=json.createObjectNode();
    for(String field:List.of("city","date","transportation","timeline","attractions","meals","hotel","route_legs","route_fact_expires_at"))facts.set(field,day.path(field));
    try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical(facts).getBytes(StandardCharsets.UTF_8)));}
    catch(Exception ex){throw new IllegalStateException("Cannot fingerprint schedule facts",ex);}
  }
  public void refresh(ObjectNode plan, ObjectNode quality, JsonNode input) {
    boolean stale = false;
    for (var raw : plan.path("days")) if (raw instanceof ObjectNode day && !day.path("timeline").isEmpty() && !current(day, input, Instant.now())) { invalidate(day, "SCHEDULE_DEPENDENCIES_CHANGED"); stale = true; }
    if (stale) degrade(quality);
  }
  /** Copies preserve itinerary content, never another record's solver authorization. */
  public ObjectNode invalidateCopy(JsonNode source,ObjectNode quality) {
    ObjectNode copied=((ObjectNode)source).deepCopy();
    for(var raw:copied.path("days"))if(raw instanceof ObjectNode day){
      day.remove("schedule_dependencies");
      invalidate(day,"COPIED_PLAN_REQUIRES_REVERIFICATION");
    }
    quality.put("outcome","draft");degrade(quality);
    var gaps=quality.withArray("data_gaps");
    if(gaps.valueStream().noneMatch(gap->"copied_plan_requires_reverification".equals(gap.asText(""))))gaps.add("copied_plan_requires_reverification");
    return copied;
  }
  public void changed(ObjectNode plan, ObjectNode quality, JsonNode before, JsonNode after) {
    boolean stale = false;
    for (var raw : plan.path("days")) if (raw instanceof ObjectNode day && !fingerprint(before, day).equals(fingerprint(after, day))) { invalidate(day, "PLANNING_INPUT_CHANGED"); stale = true; }
    if (stale) degrade(quality);
  }
  private void invalidate(ObjectNode day, String reason) {
    day.put("schedule_stale", true);
    var feasibility = day.path("feasibility") instanceof ObjectNode f ? f : day.putObject("feasibility");
    feasibility.put("status", "unverified").put("solver_status", reason);
    var unknown = feasibility.withArray("unknown");
    if (unknown.valueStream().noneMatch(n -> n.asText("").equals("schedule_unknown_or_stale"))) unknown.add("schedule_unknown_or_stale");
    for (var item : day.path("timeline")) if (item instanceof ObjectNode entry) entry.put("status", "unverified");
  }
  private void degrade(ObjectNode quality) {
    quality.put("feasibility_status", "unverified").put("facts_complete", false).put("schedule_stale", true);
    if (quality.path("outcome").asText("").equals("complete")) quality.put("outcome", "degraded");
    var gaps = quality.withArray("data_gaps");
    if (gaps.valueStream().noneMatch(n -> n.asText("").equals("schedule_unknown_or_stale"))) gaps.add("schedule_unknown_or_stale");
  }
}
