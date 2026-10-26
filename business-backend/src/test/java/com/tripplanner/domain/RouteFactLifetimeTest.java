package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import com.github.benmanes.caffeine.cache.Cache;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class RouteFactLifetimeTest {
  @Test @SuppressWarnings("unchecked") void nearExpiryCachedRouteKeepsItsOriginalObservationAndDeadline(){
    var json=new JsonMapper();var gateway=new AmapGateway("http://localhost:1","isolated-test-key",900,300,3600,100,"validation","yes",json);
    var left=json.createObjectNode().put("longitude",116.1).put("latitude",39.9);var right=json.createObjectNode().put("longitude",116.2).put("latitude",39.9);
    var observed=Instant.now().minusSeconds(1795);var expiry=observed.plusSeconds(1800);
    var fact=json.createObjectNode().put("duration",600).put("distance",1000).put("route_type","driving").put("observed_at",observed.toString()).put("expires_at",expiry.toString());
    var cache=(Cache<String,JsonNode>)ReflectionTestUtils.getField(gateway,"routeCache");
    cache.put("116.1,39.9\n116.2,39.9\ndriving\n北京\n北京",fact);
    var first=gateway.routeBetween(left,right,"driving","北京");var again=gateway.routeBetween(left,right,"driving","北京");
    assertEquals(observed.toString(),first.path("observed_at").asText());assertEquals(expiry.toString(),again.path("expires_at").asText());
    assertEquals(first,again);assertEquals(0L,gateway.metrics().get("requests"));
    assertTrue(Instant.parse(again.path("expires_at").asText()).isBefore(Instant.now().plusSeconds(10)));
  }
}
