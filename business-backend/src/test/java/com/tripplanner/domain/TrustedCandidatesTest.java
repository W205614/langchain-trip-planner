package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.api.ApiException;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class TrustedCandidatesTest {
  final JsonMapper json = new JsonMapper();

  @Test
  void rejectsFabricatedNameEvenWithRealId() {
    var evidence =
        json.readTree(
            "[{\"id\":\"real\",\"name\":\"真实景点\",\"location\":{\"latitude\":39,\"longitude\":116}}]");
    var plan =
        json.readTree(
            "{\"days\":[{\"attractions\":[{\"poi_id\":\"real\",\"name\":\"编造景点\",\"location\":{\"latitude\":39,\"longitude\":116}}]}]}");
    assertThrows(ApiException.class, () -> TrustedCandidates.verify(plan, evidence, null));
  }

  @Test
  void acceptsOnlyMatchingFetchedFacts() {
    var evidence =
        json.readTree(
            "[{\"id\":\"real\",\"name\":\"真实景点\",\"location\":{\"latitude\":39,\"longitude\":116}}]");
    var plan =
        json.readTree(
            "{\"days\":[{\"attractions\":[{\"poi_id\":\"real\",\"name\":\"真实景点\",\"location\":{\"latitude\":39,\"longitude\":116}}]}]}");
    assertDoesNotThrow(() -> TrustedCandidates.verify(plan, evidence, null));
    assertThrows(
        ApiException.class, () -> TrustedCandidates.verify(plan, json.createArrayNode(), null));
  }
}
