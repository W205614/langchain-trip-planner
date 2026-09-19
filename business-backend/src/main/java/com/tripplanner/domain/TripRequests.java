package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** Canonical input used by both durable fingerprinting and the agent contract. */
public final class TripRequests {
  private TripRequests() {}

  public static ObjectNode normalize(ObjectNode input) {
    ObjectNode body = input.deepCopy();
    // Match Pydantic's public request whitelist; execution metadata is never client-controlled.
    body.retain(
        Set.of(
            "city",
            "departure_city",
            "start_date",
            "end_date",
            "travel_days",
            "transportation",
            "accommodation",
            "traveler_count",
            "room_count",
            "budget_total",
            "preferences",
            "constraints",
            "free_text_input"));
    text(body, "city", 0, 32);
    if (!body.hasNonNull("departure_city")) body.put("departure_city", "");
    text(body, "departure_city", 0, 32);
    text(body, "transportation", 0, 32);
    text(body, "accommodation", 0, 64);
    if (!body.has("traveler_count")) body.put("traveler_count", 1);
    if (!body.has("room_count")) body.put("room_count", 1);
    int travelers = integer(body, "traveler_count", 1, 20);
    int rooms = integer(body, "room_count", 1, 10);
    if (rooms > travelers) throw new ApiException(422, "房间数不能大于同行人数");
    if (!body.has("budget_total")) body.putNull("budget_total");
    if (!body.path("budget_total").isNull()) integer(body, "budget_total", 100, 10_000_000);
    try {
      String start = body.path("start_date").asText(""), end = body.path("end_date").asText("");
      if (!start.matches("\\d{4}-\\d{2}-\\d{2}") || !end.matches("\\d{4}-\\d{2}-\\d{2}"))
        throw new IllegalArgumentException();
      int days = integer(body, "travel_days", 1, 30);
      if (ChronoUnit.DAYS.between(LocalDate.parse(start), LocalDate.parse(end)) + 1 != days)
        throw new IllegalArgumentException();
    } catch (Exception ex) {
      throw new ApiException(422, "日期或旅行天数无效");
    }
    if (!body.has("preferences")) body.putArray("preferences");
    if (!body.path("preferences").isArray() || body.path("preferences").size() > 12)
      throw new ApiException(422, "旅行偏好无效");
    for (var p : body.path("preferences"))
      if (!p.isString() || p.asText("").length() > 64) throw new ApiException(422, "旅行偏好无效");
    if (!body.hasNonNull("free_text_input")) body.put("free_text_input", "");
    text(body, "free_text_input", 0, 500);
    if (!body.has("constraints")) body.putObject("constraints");
    if (!(body.get("constraints") instanceof ObjectNode constraints))
      throw new ApiException(422, "行程约束无效");
    var must = names(constraints, "must_visit");
    var avoid = names(constraints, "avoid");
    if (must.stream().anyMatch(n -> avoid.stream().anyMatch(n::equalsIgnoreCase)))
      throw new ApiException(422, "同一景点不能同时必去和不去");
    if (!constraints.has("daily_minutes")) constraints.put("daily_minutes", 600);
    integer(constraints, "daily_minutes", 120, 900);
    if (!constraints.has("max_inter_stop_walking_km"))
      constraints.putNull("max_inter_stop_walking_km");
    var walk = constraints.path("max_inter_stop_walking_km");
    if (!walk.isNull()
        && (!walk.isNumber()
            || !Double.isFinite(walk.asDouble())
            || walk.asDouble() <= 0
            || walk.asDouble() > 30)) throw new ApiException(422, "步行限制无效");
    return body;
  }

  private static List<String> names(ObjectNode body, String field) {
    var raw = body.get(field);
    var names = new LinkedHashSet<String>();
    var items =
        raw == null
            ? List.<JsonNode>of()
            : raw.isString() ? List.of(raw) : raw.isArray() ? raw.valueStream().toList() : null;
    if (items == null) throw new ApiException(422, "景点名称列表无效");
    for (var item : items) {
      if (!item.isString() || item.asText("").isBlank()) throw new ApiException(422, "景点名称不能为空");
      for (var name : item.asText("").split("[,，、;；\\n\\r]+"))
        if (!name.isBlank()) names.add(name.strip());
    }
    if (names.size() > 8 || names.stream().anyMatch(s -> s.length() > 64))
      throw new ApiException(422, "景点名称过长或过多");
    var array = body.putArray(field);
    names.forEach(array::add);
    return List.copyOf(names);
  }

  public static String text(ObjectNode body, String field, int min, int max) {
    var node = body.get(field);
    if (node == null
        || !node.isString()
        || node.asText("").length() < min
        || node.asText("").length() > max) throw new ApiException(422, field + " 无效");
    return node.asText("");
  }

  public static int integer(ObjectNode body, String field, int min, int max) {
    var n = body.get(field);
    if (n == null
        || !n.isIntegralNumber()
        || !n.canConvertToInt()
        || n.asInt(0) < min
        || n.asInt(0) > max) throw new ApiException(422, field + " 无效");
    return n.asInt(0);
  }
}
