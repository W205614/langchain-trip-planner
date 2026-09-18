package com.tripplanner.domain;

import java.time.LocalDate;
import java.util.*;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.*;

/** Deterministic checks for user-edited plans and explicit route re-verification. */
@Service
public class PlanRules {
  @FunctionalInterface
  public interface Routes {
    JsonNode get(JsonNode left, JsonNode right, String type, String city);
  }

  private final JsonMapper json;

  public PlanRules(JsonMapper json) {
    this.json = json;
  }

  public static String routeType(String transport) {
    String t = transport.strip().toLowerCase(Locale.ROOT);
    if (t.matches(".*(自驾|驾车|driving).*")) return "driving";
    if (t.matches(".*(公交|地铁|公共交通|transit).*")) return "transit";
    return "walking";
  }

  private static double round(double n, int digits) {
    double f = Math.pow(10, digits);
    return Math.rint(n * f) / f;
  }

  private static String key(String s) {
    return s.strip().toLowerCase(Locale.ROOT);
  }

  private static List<JsonNode> list(JsonNode n) {
    return n.isArray() ? n.valueStream().toList() : List.of();
  }

  private static Set<String> strings(JsonNode n) {
    var r = new LinkedHashSet<String>();
    n.forEach(v -> r.add(v.asText("")));
    return r;
  }

  public ObjectNode finish(ObjectNode plan, ObjectNode request, Routes routes, boolean repair) {
    var constraints = request.path("constraints");
    plan.set("constraints", constraints.deepCopy());
    String city = plan.path("city").asText(""),
        type = routeType(request.path("transportation").asText(""));
    var required = new HashSet<String>();
    constraints.path("must_visit").forEach(n -> required.add(key(n.asText(""))));
    var avoided = new HashSet<String>();
    constraints.path("avoid").forEach(n -> avoided.add(key(n.asText(""))));
    var days = list(plan.path("days"));
    var all = days.stream().flatMap(d -> list(d.path("attractions")).stream()).toList();
    for (var n : constraints.path("must_visit")) {
      var matched = AttractionNames.resolve(n.asText(""), all, city);
      if (matched instanceof ObjectNode obj) {
        var aliases = strings(obj.path("requested_names"));
        aliases.add(n.asText(""));
        obj.set("requested_names", json.valueToTree(aliases));
      }
    }
    var avoidedIds = new HashSet<String>();
    for (var n : constraints.path("avoid")) {
      var match = AttractionNames.resolve(n.asText(""), all, city);
      if (match != null) avoidedIds.add(match.path("poi_id").asText(""));
    }
    var repairs = json.createArrayNode();
    var violations = new ArrayList<String>();
    var warnings = new ArrayList<String>();
    var checks = json.createArrayNode();
    var degraded = json.createArrayNode();
    var seen = new HashSet<String>();
    var gaps =
        new TreeSet<>(
            Set.of(
                "opening_hours_unavailable",
                "reservation_unverified",
                "in_attraction_walking_unknown",
                "hotel_and_meal_routes_unverified"));
    Map<String, JsonNode> cache = new HashMap<>();
    int count = 0;
    double straight = 0, actualDistance = 0, actualMinutes = 0;
    boolean routeChecked = true;
    for (int index = 0; index < days.size(); index++) {
      var day = (ObjectNode) days.get(index);
      var attrs = new ArrayList<>(list(day.path("attractions")));
      int dayIndex = day.path("day_index").asInt(0);
      for (var a : new ArrayList<>(attrs)) {
        String id = a.path("poi_id").asText("");
        String reason =
            avoided.contains(key(a.path("name").asText(""))) || avoidedIds.contains(id)
                ? "excluded_attraction"
                : seen.contains(id) ? "duplicate_poi" : null;
        if (reason != null && repair) {
          attrs.remove(a);
          repairs
              .addObject()
              .put("day_index", dayIndex)
              .put("removed_poi_id", id)
              .put("reason", reason);
        } else {
          if (reason != null)
            violations.add(
                "第" + (dayIndex + 1) + "天：" + reason + " (" + a.path("name").asText("") + ")");
          seen.add(id);
        }
      }
      while (true) {
        var legs = json.createArrayNode();
        double minutes = 0, distance = 0, walking = 0;
        boolean complete = true, walkKnown = true;
        for (int i = 1; i < attrs.size(); i++) {
          var left = attrs.get(i - 1);
          var right = attrs.get(i);
          String pair =
              left.path("poi_id")
                  + ":"
                  + left.path("location")
                  + ":"
                  + right.path("poi_id")
                  + ":"
                  + right.path("location")
                  + ":"
                  + type;
          if (!cache.containsKey(pair)) {
            JsonNode route = null;
            try {
              route =
                  routes == null
                      ? null
                      : routes.get(left.path("location"), right.path("location"), type, city);
              if (route == null
                  || !quantity(route.path("duration"))
                  || !quantity(route.path("distance"))
                  || (route.hasNonNull("walking_distance")
                      && !quantity(route.path("walking_distance")))) route = null;
            } catch (Exception ex) {
              route = null;
            }
            cache.put(pair, route);
          }
          var route = cache.get(pair);
          var leg =
              legs.addObject()
                  .put("from", left.path("name").asText(""))
                  .put("to", right.path("name").asText(""))
                  .put("route_type", route == null ? type : route.path("route_type").asText(type));
          if (route == null) {
            complete = false;
            leg.putNull("minutes").putNull("distance_km").putNull("walking_km");
            gaps.add("route_duration_unavailable_fallback_to_straight_line");
          } else {
            double m = route.path("duration").asDouble() / 60,
                dist = route.path("distance").asDouble();
            minutes += m;
            distance += dist;
            leg.put("minutes", round(m, 1)).put("distance_km", round(dist / 1000, 2));
            if (type.equals("walking")) {
              walking += dist;
              leg.put("walking_km", round(dist / 1000, 2));
            } else if (route.hasNonNull("walking_distance")) {
              double w = route.path("walking_distance").asDouble();
              walking += w;
              leg.put("walking_km", round(w / 1000, 2));
            } else {
              walkKnown = false;
              leg.putNull("walking_km");
            }
          }
        }
        int visit =
            attrs.stream().mapToInt(a -> Math.max(0, a.path("visit_duration").asInt(0))).sum();
        boolean overTime =
            (visit + 120 + (complete ? minutes : 0)) > constraints.path("daily_minutes").asInt(600);
        boolean overWalk =
            complete
                && walkKnown
                && constraints.hasNonNull("max_inter_stop_walking_km")
                && walking / 1000 > constraints.path("max_inter_stop_walking_km").asDouble();
        boolean overRoute = complete && minutes > 120;
        var removable =
            attrs.stream()
                .filter(
                    a -> {
                      var names = strings(a.path("requested_names"));
                      names.add(a.path("name").asText(""));
                      return names.stream().noneMatch(n -> required.contains(key(n)));
                    })
                .toList();
        if (repair
            && (overTime || overWalk || overRoute)
            && attrs.size() > 1
            && !removable.isEmpty()) {
          var removed = removable.getLast();
          attrs.remove(removed);
          repairs
              .addObject()
              .put("day_index", dayIndex)
              .put("removed_poi_id", removed.path("poi_id").asText(""))
              .put(
                  "reason",
                  overTime
                      ? "daily_time_exceeded"
                      : overWalk ? "walking_limit_exceeded" : "route_duration_exceeded");
          continue;
        }
        if (overTime)
          violations.add(
              "第"
                  + (dayIndex + 1)
                  + "天：安排超过每日"
                  + constraints.path("daily_minutes").asInt(600)
                  + "分钟上限");
        if (overWalk)
          violations.add(
              "第"
                  + (dayIndex + 1)
                  + "天：景点间步行超过"
                  + constraints.path("max_inter_stop_walking_km").asText("")
                  + "公里上限");
        if (overRoute) violations.add("第" + (dayIndex + 1) + "天：景点间交通超过120分钟");
        if ((!complete || !walkKnown) && constraints.hasNonNull("max_inter_stop_walking_km"))
          gaps.add("inter_stop_walking_unverified");
        var check =
            checks
                .addObject()
                .put("day_index", dayIndex)
                .put("visit_minutes", visit)
                .put("meal_allowance_minutes", 90)
                .put("buffer_minutes", 30);
        check.set("routes", legs);
        boolean hasInterStopLeg = attrs.size() > 1;
        check.put(
            "walking_status",
            attrs.isEmpty()
                ? "no_attractions"
                : !hasInterStopLeg
                    ? "single_stop"
                    : type.equals("driving")
                        ? "not_applicable"
                        : complete && walkKnown ? "available" : "unavailable");
        if (complete) {
          check
              .put("route_minutes", round(minutes, 1))
              .put("planned_minutes", round(visit + 120 + minutes, 1))
              .put("route_distance_km", distance / 1000);
          actualMinutes += round(minutes, 1);
          actualDistance += distance / 1000;
        } else {
          check.putNull("route_minutes").putNull("planned_minutes").putNull("route_distance_km");
          routeChecked = false;
        }
        if (hasInterStopLeg && complete && walkKnown)
          check.put("inter_stop_walking_km", round(walking / 1000, 2));
        else check.putNull("inter_stop_walking_km");
        break;
      }
      day.set("attractions", json.valueToTree(attrs));
      count += attrs.size();
      if (attrs.isEmpty()) warnings.add("第 " + (index + 1) + " 天没有可验证的景点");
      var missing = new TreeSet<>(Set.of("breakfast", "lunch", "dinner"));
      day.path("meals").forEach(m -> missing.remove(m.path("type").asText("")));
      if (!missing.isEmpty())
        warnings.add("第 " + (index + 1) + " 天缺少餐饮安排：" + String.join(",", missing));
      int visit =
          attrs.stream().mapToInt(a -> Math.max(0, a.path("visit_duration").asInt(0))).sum();
      if (visit > 480) warnings.add("第 " + (index + 1) + " 天游览时长 " + visit + " 分钟，超过 480 分钟");
      if (day.path("generation_mode").asText("").equals("fallback")) degraded.add(dayIndex);
      if (dayIndex != index
          || !day.path("date")
              .asText("")
              .equals(
                  LocalDate.parse(request.path("start_date").asText(""))
                      .plusDays(index)
                      .toString())) violations.add("第" + (index + 1) + "天：日期或天序号错误");
      if (attrs.stream().anyMatch(a -> a.path("visit_duration").asInt(0) <= 0))
        violations.add("第" + (index + 1) + "天：游览时长必须为正数");
      for (int i = 1; i < attrs.size(); i++) straight += haversine(attrs.get(i - 1), attrs.get(i));
    }
    if (days.size() != request.path("travel_days").asInt(0))
      warnings.add(
          0, "行程天数为 " + days.size() + "，与请求的 " + request.path("travel_days").asInt(0) + " 天不一致");
    var names = new HashSet<String>();
    for (var d : days)
      for (var a : d.path("attractions")) {
        names.add(key(a.path("name").asText("")));
        a.path("requested_names").forEach(n -> names.add(key(n.asText(""))));
      }
    for (var n : constraints.path("must_visit"))
      if (!names.contains(key(n.asText(""))))
        violations.add("未满足必去景点：" + n.asText("") + "（当前行程中未找到唯一对应景点，可补充所在区域或具体名称）");
    for (String field : List.of("city", "start_date", "end_date"))
      if (!plan.path(field).equals(request.path(field))) {
        violations.add("行程目的地或日期与原始请求不一致");
        break;
      }
    warnings.addAll(violations);
    if (days.stream()
        .flatMap(d -> list(d.path("attractions")).stream())
        .allMatch(a -> !a.path("opening_hours").asText("").isEmpty())) {
      gaps.remove("opening_hours_unavailable");
      gaps.add("opening_hours_travel_date_unverified");
    }
    if (routeChecked) gaps.remove("route_duration_unavailable_fallback_to_straight_line");
    var result =
        json.createObjectNode()
            .put("score", Math.max(0, 100 - 15 * warnings.size()))
            .put("passed", warnings.isEmpty())
            .put("rules_passed", warnings.isEmpty())
            .put("facts_complete", false)
            .put("feasibility_status", warnings.isEmpty() ? "needs_verification" : "violated")
            .put("policy_version", "constraints-v2")
            .put("days_checked", days.size())
            .put("attractions_checked", count)
            .put("duplicate_attractions_removed", 0)
            .put("estimated_intra_day_distance_km", round(straight, 2))
            .put("route_checked", routeChecked)
            .put("actual_route_minutes", Math.rint(actualMinutes))
            .put("actual_route_distance_km", round(actualDistance, 2));
    result.set("warnings", json.valueToTree(warnings));
    result.set("day_checks", checks);
    result.set("repairs", repairs);
    result.set("data_gaps", json.valueToTree(gaps));
    result.set("degraded_days", degraded);
    budget(plan);
    classify(plan, request, result);
    return result;
  }

  private static boolean quantity(JsonNode n) {
    return n.isNumber() && Double.isFinite(n.asDouble()) && n.asDouble() >= 0;
  }

  private static double haversine(JsonNode a, JsonNode b) {
    double lat1 = Math.toRadians(a.path("location").path("latitude").asDouble()),
        lat2 = Math.toRadians(b.path("location").path("latitude").asDouble());
    double dlat = lat2 - lat1,
        dlon =
            Math.toRadians(
                b.path("location").path("longitude").asDouble()
                    - a.path("location").path("longitude").asDouble());
    return 6371
        * 2
        * Math.asin(
            Math.sqrt(
                Math.pow(Math.sin(dlat / 2), 2)
                    + Math.cos(lat1) * Math.cos(lat2) * Math.pow(Math.sin(dlon / 2), 2)));
  }

  private void budget(ObjectNode plan) {
    var days = list(plan.path("days"));
    var attrs = days.stream().flatMap(d -> list(d.path("attractions")).stream()).toList();
    var meals = days.stream().flatMap(d -> list(d.path("meals")).stream()).toList();
    var hotels =
        days.stream()
            .limit(Math.max(0, days.size() - 1))
            .map(d -> d.path("hotel"))
            .filter(JsonNode::isObject)
            .toList();
    var unknown = new TreeSet<>(strings(plan.path("budget").path("unknown_items")));
    unknown.add("transportation_prices");
    if (attrs.stream().anyMatch(a -> !a.has("ticket_price"))) unknown.add("attraction_prices");
    if (meals.stream().anyMatch(a -> !a.has("estimated_cost"))) unknown.add("meal_prices");
    if (hotels.size() < Math.max(0, days.size() - 1)
        || hotels.stream().anyMatch(h -> !h.has("estimated_cost"))) unknown.add("hotel_prices");
    int tickets = attrs.stream().mapToInt(a -> a.path("ticket_price").asInt(0)).sum(),
        meal = meals.stream().mapToInt(m -> m.path("estimated_cost").asInt(0)).sum(),
        hotel = hotels.stream().mapToInt(h -> h.path("estimated_cost").asInt(0)).sum();
    long missing =
        attrs.stream()
            .filter(
                a ->
                    a.path("ticket_price").asInt(0) == 0
                        && a.path("price_source").asText("unknown").equals("unknown"))
            .count();
    var assumptions = json.createArrayNode();
    tickets += 80 * (int) missing;
    if (missing > 0) {
      unknown.add("attraction_prices");
      assumptions.add(missing + "个景点缺少票价，暂按80元/人/景点预留；不代表实际售价或收费。");
    }
    int nights =
        Math.max(0, days.size() - 1)
            - (int) hotels.stream().filter(h -> h.path("estimated_cost").asInt(0) > 0).count();
    if (nights > 0) {
      String preference = days.isEmpty() ? "" : days.getFirst().path("accommodation").asText("");
      int nightly = preference.matches(".*(豪华|五星).*") ? 600 : preference.contains("舒适") ? 350 : 250;
      hotel += nights * nightly;
      unknown.add("hotel_prices");
      assumptions.add(
          "住宿按" + (days.size() - 1) + "晚、1间房计算，缺少报价的" + nights + "晚按" + nightly + "元/晚预留。");
    }
    int transport =
        days.stream()
            .mapToInt(
                d ->
                    switch (routeType(d.path("transportation").asText(""))) {
                      case "walking" -> 0;
                      case "driving" -> 100;
                      default -> 30;
                    })
            .sum();
    assumptions.add("市内交通按步行0元、公共交通30元/人/天、自驾100元/车/天预留；不含往返目的地的大交通。");
    assumptions.add("门票与餐饮按1人计算，所有金额仅作预算预留，出行前确认价格。");
    var budget =
        plan.putObject("budget")
            .put("total_attractions", tickets)
            .put("total_meals", meal)
            .put("total_hotels", hotel)
            .put("total_transportation", transport)
            .put("total", tickets + meal + hotel + transport)
            .put("estimated", true);
    budget.set("unknown_items", json.valueToTree(unknown));
    budget.set("assumptions", assumptions);
  }

  private static void issue(
      ArrayNode issues,
      String code,
      String scope,
      String reason,
      String action,
      boolean blocking,
      boolean retryable) {
    issues
        .addObject()
        .put("code", code)
        .put("scope", scope)
        .put("reason", reason)
        .put("action", action)
        .put("blocking", blocking)
        .put("retryable", retryable);
  }

  private void classify(ObjectNode plan, ObjectNode request, ObjectNode report) {
    var issues = report.putArray("issues");
    report
        .path("warnings")
        .forEach(
            w ->
                issue(
                    issues,
                    "CONSTRAINT_UNSATISFIED",
                    "plan",
                    w.asText(""),
                    "修改要求或调整行程后重新检查",
                    true,
                    false));
    for (var day : plan.path("days"))
      for (var a : day.path("attractions"))
        if (!AttractionNames.valid(a))
          issue(
              issues,
              "INVALID_POI",
              "day:" + day.path("day_index").asInt(0),
              "景点身份或坐标无效",
              "重新选择可信景点",
              true,
              false);
    var routeMissingDays = new ArrayList<Integer>();
    for (var day : report.path("day_checks")) {
      String scope = "day:" + day.path("day_index").asInt(0);
      if (day.path("route_minutes").isNull())
        routeMissingDays.add(day.path("day_index").asInt(0) + 1);
      if (request.path("constraints").hasNonNull("max_inter_stop_walking_km")
          && day.path("inter_stop_walking_km").isNull())
        issue(issues, "WALKING_LIMIT_UNVERIFIED", scope, "无法核验指定步行上限", "稍后重试或调整交通要求", true, true);
    }
    if (!routeMissingDays.isEmpty())
      issue(
          issues,
          "ROUTE_UNAVAILABLE",
          "plan",
          routeMissingDays.stream().map(i -> "第" + i + "天").collect(java.util.stream.Collectors.joining("、"))
              + "部分景点间路线暂不可用，已保留可继续调整的行程",
          "可直接查看行程；出发前确认交通，或在具体行程中调整景点",
          false,
          true);
    report
        .path("degraded_days")
        .forEach(
            d ->
                issue(
                    issues,
                    "RULE_FALLBACK",
                    "day:" + d.asInt(0),
                    "模型未完成，本日使用可信景点规则安排",
                    "核对安排或重新规划",
                    false,
                    true));
    report
        .path("data_gaps")
        .forEach(
            g ->
                issue(issues, "DATA_UNVERIFIED", "plan", g.asText(""), "出行前向官方来源确认", false, false));
    if (!plan.path("weather_notice").asText("").isEmpty())
      issue(
          issues,
          "WEATHER_UNAVAILABLE",
          "plan",
          plan.path("weather_notice").asText(""),
          "出行前查询天气",
          false,
          true);
    plan.path("enrichment_notices")
        .forEach(
            n ->
                issue(
                    issues,
                    "RAG_UNAVAILABLE",
                    "plan",
                    n.asText(""),
                    "可继续查看行程，攻略资料请稍后查询",
                    false,
                    true));
    boolean blocking = issues.valueStream().anyMatch(i -> i.path("blocking").asBoolean(false));
    boolean degraded =
        issues
            .valueStream()
            .anyMatch(
                i ->
                    Set.of("RULE_FALLBACK", "RAG_UNAVAILABLE", "WEATHER_UNAVAILABLE", "ROUTE_UNAVAILABLE")
                        .contains(i.path("code").asText("")));
    report
        .put("completion_policy", "reliability-v1")
        .put("outcome", blocking ? "draft" : degraded ? "degraded" : "complete");
  }
}
