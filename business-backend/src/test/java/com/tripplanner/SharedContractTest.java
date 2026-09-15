package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.domain.TripRequests;
import java.nio.file.*;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class SharedContractTest {
  @Test
  void sharedWireSampleUsesCanonicalJavaRequest() throws Exception {
    var sample =
        new JsonMapper()
            .readTree(Files.readString(Path.of("../contracts/internal-v1/execution.sample.json")));
    assertEquals(1, sample.path("protocol_version").asInt(0));
    assertEquals(
        sample.path("request"), TripRequests.normalize((ObjectNode) sample.path("request")));
    assertNotNull(java.time.Instant.parse(sample.path("deadline_at").asText("")));
  }
}
