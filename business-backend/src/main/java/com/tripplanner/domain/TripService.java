package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.HistoryMapper;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.*;

@Service
public class TripService {
  private final HistoryMapper history;
  private final AmapGateway amap;
  private final PlanRules rules;
  private final TransactionTemplate tx;
  private final JsonMapper json;
  private final TripLedgerService ledger;

  public TripService(HistoryMapper history, AmapGateway amap, PlanRules rules,
      TransactionTemplate tx, JsonMapper json, TripLedgerService ledger) {
    this.history = history; this.amap = amap; this.rules = rules; this.tx = tx; this.json = json;
    this.ledger = ledger;
  }

  public Map<String, Object> createManual(long uid, ObjectNode input, String requestId) {
    JsonNode submittedDays = input.path("days").deepCopy();
    String title = input.path("title").asText("").strip();
    if (title.length() > 160) throw new ApiException(422, "行程标题过长");
    ObjectNode request = TripRequests.normalize(input);
    int count = request.path("travel_days").asInt();
    if (!submittedDays.isArray() || submittedDays.size() != count)
      throw new ApiException(422, "手工行程天数不一致");
    var plan = json.createObjectNode()
        .put("city", request.path("city").asText()).put("start_date", request.path("start_date").asText())
        .put("end_date", request.path("end_date").asText()).put("overall_suggestions", "手工行程，请在出发前再次确认营业与预约信息");
    try { plan.set("weather_info", amap.weather(request.path("city").asText())); }
    catch (Exception ex) { plan.putArray("weather_info"); plan.put("weather_notice", "天气暂不可用，请出行前再次确认。"); }
    var days = plan.putArray("days");
    var seen = new HashSet<String>();
    var invalid = new ArrayList<String>();
    LocalDate start = LocalDate.parse(request.path("start_date").asText());
    for (int index = 0; index < count; index++) {
      JsonNode submitted = submittedDays.get(index);
      var day = days.addObject().put("day_index", index).put("date", start.plusDays(index).toString())
          .put("description", "手工编排第" + (index + 1) + "天")
          .put("transportation", request.path("transportation").asText())
          .put("accommodation", request.path("accommodation").asText());
      var attractions = day.putArray("attractions"); day.putArray("meals");
      JsonNode ids = submitted.path("poi_ids");
      if (!ids.isArray() || ids.size() > 20) throw new ApiException(422, "每日景点列表无效");
      for (JsonNode node : ids) {
        String id = node.asText("");
        if (!id.matches("[A-Za-z0-9_-]{1,64}") || !seen.add(id)) { invalid.add(id); continue; }
        try {
          ObjectNode poi = amap.detail(id);
          if (!cityMatches(request.path("city").asText(), poi.path("city").asText())) { invalid.add(id); continue; }
          var attraction = attractions.addObject().put("poi_id", id).put("name", poi.path("name").asText())
              .put("address", poi.path("address").asText()).put("visit_duration", 120)
              .put("description", "用户手工选择的可信高德景点").put("fact_source", "amap_rest")
              .put("price_source", "unknown").put("opening_hours", poi.path("opening_hours").asText(""));
          attraction.set("location", poi.path("location").deepCopy());
          attraction.putArray("requested_names");
        } catch (Exception ex) { invalid.add(id); }
      }
    }
    ObjectNode quality = rules.finish(plan, request, (left, right, type, city) -> amap.routeBetween(left, right, type, city), false);
    if (!invalid.isEmpty()) {
      quality.put("outcome", "draft");
      quality.withArray("data_gaps").add("manual_pois_unverified:" + String.join(",", invalid));
    }
    var record = new HashMap<String, Object>();
    record.put("user_id", uid); record.put("title", title);
    record.put("source", BusinessTypes.TripSource.MANUAL.wire());
    for (String field : List.of("departure_city","city","start_date","end_date","travel_days","transportation","accommodation","traveler_count","room_count","free_text_input"))
      record.put(field, request.path(field).isNumber() ? request.path(field).asInt() : request.path(field).asText(""));
    record.put("budget_total", request.path("budget_total").isNull() ? null : request.path("budget_total").asInt());
    record.put("preferences", json.writeValueAsString(request.path("preferences")));
    record.put("plan_json", json.writeValueAsString(plan)); record.put("quality_json", json.writeValueAsString(quality));
    record.put("last_verified_at", new Timestamp(System.currentTimeMillis()));
    tx.executeWithoutResult(s -> {
      history.insert(record);
      ledger.capture(uid, ((Number) record.get("id")).longValue(), "manual_create", requestId);
      if (!quality.path("outcome").asText("").equals("draft"))
        history.outbox(((Number) record.get("id")).longValue(), uid, "upsert");
    });
    return Map.of("success", true, "id", record.get("id"), "version", 1,
        "data", plan, "quality", quality, "saved", true);
  }

  public Map<String, Object> reverify(long uid, long id, int version, String requestId) {
    var row = history.owned(uid, id);
    if (row == null) throw new ApiException(404, "行程不存在");
    if (((Number) row.get("version")).intValue() != version) throw new ApiException(409, "行程版本冲突", "VERSION_CONFLICT");
    ObjectNode plan = (ObjectNode) json.readTree(row.get("plan_json").toString());
    ObjectNode request = request(row, plan);
    var invalid = new ArrayList<String>();
    plan.path("days").forEach(day -> {
      if (!(day.path("attractions") instanceof ArrayNode attractions)) return;
      for (int index = attractions.size() - 1; index >= 0; index--) {
        String poiId = attractions.get(index).path("poi_id").asText("");
        try {
          var poi = amap.detail(poiId); var target = (ObjectNode) attractions.get(index);
          target.put("name", poi.path("name").asText()).put("address", poi.path("address").asText())
              .put("opening_hours", poi.path("opening_hours").asText("")).put("fact_source", "amap_rest");
          target.set("location", poi.path("location").deepCopy());
        } catch (Exception ex) { invalid.add(poiId); attractions.remove(index); }
      }
    });
    ObjectNode quality = rules.finish(plan, request, (left,right,type,city) -> amap.routeBetween(left,right,type,city), false);
    if (!invalid.isEmpty()) { quality.put("outcome", "draft"); quality.withArray("data_gaps").add("pois_unverified:" + String.join(",", invalid)); }
    tx.executeWithoutResult(s -> {
      if (history.update(Map.of("id", id, "user_id", uid, "version", version,
          "plan_json", json.writeValueAsString(plan), "quality_json", json.writeValueAsString(quality))) != 1)
        throw new ApiException(409, "行程版本冲突", "VERSION_CONFLICT");
      history.markVerified(uid, id);
      history.outbox(id, uid, quality.path("outcome").asText("").equals("draft") ? "delete" : "upsert");
      ledger.capture(uid, id, "reverify", requestId);
    });
    return Map.of("success", true, "id", id, "version", version + 1, "data", plan, "quality", quality);
  }

  private ObjectNode request(Map<String,Object> row, ObjectNode plan) {
    var body = json.createObjectNode();
    for (String field : List.of("departure_city","city","start_date","end_date","travel_days","transportation","accommodation","traveler_count","room_count","budget_total","free_text_input"))
      body.set(field, json.valueToTree(row.get(field)));
    body.set("preferences", json.readTree(row.get("preferences").toString()));
    body.set("constraints", plan.path("constraints").isObject() ? plan.path("constraints").deepCopy() : json.createObjectNode());
    return TripRequests.normalize(body);
  }

  private static boolean cityMatches(String left, String right) {
    String a = left.replaceAll("(省|市)$", ""), b = right.replaceAll("(省|市)$", "");
    return !a.isBlank() && !b.isBlank() && (a.equals(b) || a.contains(b) || b.contains(a));
  }
}
