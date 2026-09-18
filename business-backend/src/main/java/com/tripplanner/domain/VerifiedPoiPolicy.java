package com.tripplanner.domain;

import java.util.*;
import java.util.function.Function;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Reconciles Agent selections against Java-fetched AMap facts before persistence. */
final class VerifiedPoiPolicy {
  private static final int MAX_REPAIR_LOOKUPS = 24;

  record Replacement(int dayIndex, String removedPoiId, String addedPoiId) {}

  record Result(List<String> rejected, List<Replacement> replacements, int unfilledSlots) {
    boolean fullyRepaired() {
      return unfilledSlots == 0;
    }
  }

  private VerifiedPoiPolicy() {}

  static Result reconcile(
      ObjectNode plan,
      JsonNode candidates,
      JsonNode request,
      String requestedCity,
      Function<String, ObjectNode> detail,
      JsonMapper json) {
    var rejected = new ArrayList<String>();
    var replacements = new ArrayList<Replacement>();
    var missingByDay = new LinkedHashMap<ObjectNode, ArrayDeque<String>>();
    var used = new LinkedHashSet<String>();
    var unusable = new HashSet<String>();
    var cache = new HashMap<String, Optional<ObjectNode>>();
    var avoided = normalizedStrings(request.path("constraints").path("avoid"));

    for (var rawDay : plan.path("days")) {
      if (!(rawDay instanceof ObjectNode day)) continue;
      ArrayNode attractions =
          day.path("attractions") instanceof ArrayNode array
              ? array
              : day.putArray("attractions");
      int targetSize = Math.max(1, attractions.size());
      var removed = new ArrayDeque<String>();
      for (int index = attractions.size() - 1; index >= 0; index--) {
        JsonNode current = attractions.get(index);
        String poiId = current.path("poi_id").asText("");
        try {
          if (poiId.isBlank() || used.contains(poiId))
            throw new IllegalArgumentException("missing or duplicate POI");
          ObjectNode canonical = verified(poiId, requestedCity, detail, cache);
          if (avoided(current, canonical, avoided))
            throw new IllegalArgumentException("excluded POI");
          attractions.set(index, normalized(current, canonical, poiId, json));
          used.add(poiId);
        } catch (Exception ex) {
          String rejectedId = poiId.isBlank() ? "missing-poi-id" : poiId;
          rejected.add(rejectedId);
          removed.addFirst(rejectedId);
          attractions.remove(index);
        }
      }
      while (removed.size() < targetSize - attractions.size()) removed.addLast("");
      missingByDay.put(day, removed);
    }

    int lookups = 0;
    for (var entry : missingByDay.entrySet()) {
      ObjectNode day = entry.getKey();
      ArrayNode attractions = (ArrayNode) day.path("attractions");
      var missing = entry.getValue();
      while (!missing.isEmpty()) {
        ObjectNode replacement = null;
        String replacementId = "";
        for (var candidate : candidates) {
          String candidateId = candidate.path("id").asText("");
          if (candidateId.isBlank()
              || used.contains(candidateId)
              || unusable.contains(candidateId)
              || rejected.contains(candidateId)
              || avoided(candidate, candidate, avoided)) continue;
          if (++lookups > MAX_REPAIR_LOOKUPS) break;
          try {
            ObjectNode canonical = verified(candidateId, requestedCity, detail, cache);
            if (avoided(candidate, canonical, avoided)) continue;
            replacement = fallback(candidate, canonical, candidateId, json);
            replacementId = candidateId;
            break;
          } catch (Exception ignored) {
            // A candidate is only a hint until Java AMap REST confirms it.
            unusable.add(candidateId);
          }
        }
        if (replacement == null) break;
        String removedId = missing.removeFirst();
        attractions.add(replacement);
        used.add(replacementId);
        int dayIndex = day.path("day_index").asInt(0);
        replacements.add(new Replacement(dayIndex, removedId, replacementId));
        day.put("generation_mode", "fallback");
        day.put("fallback_reason", "java_rest_poi_repair");
      }
    }

    int unfilled = missingByDay.values().stream().mapToInt(ArrayDeque::size).sum();
    return new Result(List.copyOf(rejected), List.copyOf(replacements), unfilled);
  }

  private static ObjectNode verified(
      String id,
      String requestedCity,
      Function<String, ObjectNode> detail,
      Map<String, Optional<ObjectNode>> cache) {
    Optional<ObjectNode> cached = cache.get(id);
    if (cached != null) return cached.orElseThrow(() -> new IllegalArgumentException("unverified POI"));
    try {
      ObjectNode canonical = detail.apply(id);
      if (canonical == null
          || !cityMatches(requestedCity, canonical.path("city").asText(""))
          || canonical.path("location").path("longitude").asDouble(0) == 0
          || canonical.path("location").path("latitude").asDouble(0) == 0)
        throw new IllegalArgumentException("POI city or coordinates invalid");
      cache.put(id, Optional.of(canonical));
      return canonical;
    } catch (Exception ex) {
      cache.put(id, Optional.empty());
      throw ex;
    }
  }

  private static ObjectNode normalized(
      JsonNode source, ObjectNode canonical, String poiId, JsonMapper json) {
    var result = source instanceof ObjectNode object ? object.deepCopy() : json.createObjectNode();
    result.put("poi_id", poiId);
    result.put("name", canonical.path("name").asText(""));
    result.put("address", canonical.path("address").asText(""));
    result.set("location", canonical.path("location").deepCopy());
    result.put("opening_hours", canonical.path("opening_hours").asText(""));
    result.put("fact_source", "amap_rest");
    result.remove("rating");
    result.remove("ticket_price");
    result.put("price_source", "unknown");
    ArrayNode photos = result.putArray("photos");
    canonical.path("photos").forEach(photo -> photos.add(photo.asText("")));
    if (!photos.isEmpty()) result.put("image_url", photos.get(0).asText(""));
    else result.remove("image_url");
    return result;
  }

  private static ObjectNode fallback(
      JsonNode candidate, ObjectNode canonical, String poiId, JsonMapper json) {
    var result = json.createObjectNode();
    result.put("visit_duration", 120);
    result.put("description", "Java 高德 REST 复核后的同城可信候选，可在具体行程中继续调整");
    String category = canonical.path("type").asText(candidate.path("type").asText("景点"));
    result.put("category", category.isBlank() ? "景点" : category);
    if (candidate.path("requested_names").isArray())
      result.set("requested_names", candidate.path("requested_names").deepCopy());
    else result.putArray("requested_names");
    return normalized(result, canonical, poiId, json);
  }

  private static boolean avoided(JsonNode source, JsonNode canonical, Set<String> avoided) {
    if (avoided.isEmpty()) return false;
    var names = new LinkedHashSet<String>();
    names.add(key(source.path("name").asText("")));
    names.add(key(canonical.path("name").asText("")));
    source.path("requested_names").forEach(name -> names.add(key(name.asText(""))));
    return names.stream().anyMatch(avoided::contains);
  }

  private static Set<String> normalizedStrings(JsonNode values) {
    var result = new LinkedHashSet<String>();
    values.forEach(value -> result.add(key(value.asText(""))));
    result.remove("");
    return result;
  }

  private static String key(String value) {
    return Objects.toString(value, "").strip().toLowerCase(Locale.ROOT);
  }

  private static String cityKey(String city) {
    return Objects.toString(city, "")
        .strip()
        .replaceAll("(特别行政区|自治区|自治州|地区|盟|省|市)$", "");
  }

  private static boolean cityMatches(String requested, String actual) {
    String left = cityKey(requested), right = cityKey(actual);
    return !left.isBlank()
        && !right.isBlank()
        && (left.equals(right) || left.contains(right) || right.contains(left));
  }
}
