package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import java.util.List;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class AttractionNamesTest {
  JsonNode poi(String id, String name) {
    return new JsonMapper().createObjectNode().put("poi_id", id).put("name", name);
  }

  @Test
  void resolvesCityScopedAliasAndRejectsAmbiguity() {
    var disney = poi("one", "上海迪士尼乐园");
    assertSame(disney, AttractionNames.resolve("上海迪士尼公园", List.of(disney), "上海"));
    assertNull(
        AttractionNames.resolve("博物馆", List.of(poi("a", "博物馆(东馆)"), poi("b", "博物馆(西馆)")), "北京"));
  }

  @Test
  void doesNotMatchTicketOffice() {
    assertNull(AttractionNames.resolve("故宫", List.of(poi("a", "故宫售票处")), "北京"));
  }
}
