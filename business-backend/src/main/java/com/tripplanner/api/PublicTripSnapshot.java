package com.tripplanner.api;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

final class PublicTripSnapshot {
  private PublicTripSnapshot() {}

  static ObjectNode project(JsonMapper json, JsonNode source) {
    var snapshot = json.createObjectNode();
    copyValues(source, snapshot, "title", "city", "start_date", "end_date", "travel_days", "transportation", "outcome");
    var sourcePlan = source.path("plan");
    var plan = snapshot.putObject("plan");
    copyValues(sourcePlan, plan, "city", "start_date", "end_date");
    plan.put("overall_suggestions", "公开行程仅供参考，出行前请重新核验。");
    plan.putArray("weather_info");
    var days = plan.putArray("days");
    for (var rawDay : sourcePlan.path("days")) {
      var day = days.addObject();
      copyValues(rawDay, day, "day_index", "date", "city", "transportation");
      day.put("description", "").put("accommodation", "");
      day.putArray("activities");
      day.putArray("meals");
      day.put("schedule_stale", true);
      var attractions = day.putArray("attractions");
      for (var rawAttraction : rawDay.path("attractions")) {
        var attraction = attractions.addObject();
        copyValues(rawAttraction, attraction, "activity_id", "poi_id", "name", "address",
            "category", "visit_duration", "opening_hours", "fact_source", "optional", "priority");
        attraction.put("description", "");
        if (rawAttraction.path("location").isObject()) {
          copyValues(rawAttraction.path("location"), attraction.putObject("location"), "longitude", "latitude");
        }
      }
      var timeline = day.putArray("timeline");
      for (var rawItem : rawDay.path("timeline")) {
        var item = timeline.addObject();
        copyValues(rawItem, item, "kind", "start_at", "end_at", "duration_minutes");
        item.put("status", "unverified");
        String kind = rawItem.path("kind").asText("");
        String title = switch (kind) {
          case "attraction" -> "游览";
          case "meal" -> "用餐";
          case "commitment" -> "固定安排";
          case "hotel" -> "住宿";
          case "travel" -> "交通";
          case "waiting" -> "等待";
          default -> "活动";
        };
        if ("attraction".equals(kind)) {
          String activity = rawItem.path("activity_id").asText("");
          for (var attraction : attractions) {
            if (!activity.isEmpty() && activity.equals(attraction.path("activity_id").asText(""))) {
              item.put("activity_id", activity);
              title = attraction.path("name").asText(title);
              break;
            }
          }
        }
        item.put("title", title);
      }
    }
    return snapshot;
  }

  private static void copyValues(JsonNode source, ObjectNode target, String... fields) {
    for (String field : fields) {
      var value = source.path(field);
      if (value.isValueNode()) target.set(field, value);
    }
  }
}
