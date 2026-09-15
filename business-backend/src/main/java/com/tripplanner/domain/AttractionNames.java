package com.tripplanner.domain;

import java.text.Normalizer;
import java.util.*;
import tools.jackson.databind.JsonNode;

public final class AttractionNames {
  private AttractionNames() {}

  private static final Map<String, List<Set<String>>> ALIASES =
      Map.of(
          "北京",
              List.of(
                  Set.of("故宫", "故宫博物院", "故宫博物馆", "紫禁城"),
                  Set.of("天安门", "天安门广场"),
                  Set.of("天坛", "天坛公园"),
                  Set.of("圆明园", "圆明园遗址公园"),
                  Set.of("八达岭", "八达岭长城")),
          "上海", List.of(Set.of("迪士尼", "迪士尼公园", "迪士尼乐园")));

  public static boolean valid(JsonNode p) {
    double lon = p.path("location").path("longitude").asDouble(Double.NaN),
        lat = p.path("location").path("latitude").asDouble(Double.NaN);
    return !p.path("poi_id").asText("").isBlank()
        && !p.path("name").asText("").isBlank()
        && Double.isFinite(lon)
        && Double.isFinite(lat)
        && Math.abs(lon) <= 180
        && Math.abs(lat) <= 90
        && (lon != 0 || lat != 0)
        && !(p.path("name").asText("") + " " + p.path("type").asText(""))
            .matches(".*(售票处|售票口|停车场|游客中心|服务中心|地铁站|公交站|酒店|宾馆|餐厅|银行|超市|足疗|洗浴).*");
  }

  public static String normalized(String name, String city) {
    String value =
        Normalizer.normalize(name, Normalizer.Form.NFKC)
            .replaceAll("\\s+", "")
            .toLowerCase(Locale.ROOT);
    city = city.replaceFirst("市$", "").strip().toLowerCase(Locale.ROOT);
    if (!city.isEmpty())
      for (String prefix : List.of(city + "市", city))
        if (value.startsWith(prefix) && value.length() > prefix.length() + 1)
          return value.substring(prefix.length());
    return value;
  }

  private static String variant(String s) {
    return s.replaceFirst("\\([^)]*\\)$", "")
        .replace("博物院", "博物馆")
        .replaceFirst("(?:公园|乐园|景区|风景区)$", "");
  }

  public static JsonNode resolve(String name, List<JsonNode> input, String city) {
    var unique = new LinkedHashMap<String, JsonNode>();
    input.forEach(p -> unique.put(p.path("poi_id").asText(""), p));
    var candidates = new ArrayList<>(unique.values());
    var exact =
        candidates.stream()
            .filter(p -> normalized(p.path("name").asText(""), "").equals(normalized(name, "")))
            .toList();
    if (!exact.isEmpty()) return exact.size() == 1 ? exact.getFirst() : null;
    String key = normalized(name, city);
    var aliases =
        ALIASES.getOrDefault(city.replaceFirst("市$", ""), List.of()).stream()
            .filter(g -> g.contains(key))
            .findFirst()
            .orElse(Set.of(key));
    int best = 99;
    JsonNode found = null;
    boolean ambiguous = false;
    for (var p : candidates) {
      String value = normalized(p.path("name").asText(""), city);
      int rank = 99;
      if (value.equals(key)) rank = 0;
      else if (aliases.contains(value)) rank = 1;
      else if (!value.matches(".*(售票|停车|服务中心|游客中心|商店|[出入]口|地铁|公交站|[东西南北]门|暂停|关闭).*")
          && variant(key).length() >= 2
          && variant(value).equals(variant(key))) rank = 2;
      if (rank < best) {
        best = rank;
        found = p;
        ambiguous = false;
      } else if (rank == best && rank < 99) ambiguous = true;
    }
    return ambiguous ? null : found;
  }
}
