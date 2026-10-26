package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;

class BusinessMetricsDiskTest {
  @TempDir Path temporary;
  private BusinessMetrics metrics(Path folder, JdbcTemplate jdbc, SimpleMeterRegistry registry) {
    var amap = mock(AmapGateway.class);
    when(amap.metrics()).thenReturn(Map.of());
    return new BusinessMetrics(jdbc, registry, amap, folder.toString());
  }
  private double value(SimpleMeterRegistry registry, String name) { return registry.get(name).gauge().value(); }

  @Test void missingUploadPathIsUnknownAtStartupAndAfterSampling() {
    var registry = new SimpleMeterRegistry();
    var metrics = metrics(temporary.resolve("not-created"), mock(JdbcTemplate.class), registry);
    assertTrue(Double.isNaN(value(registry, "trip.data.disk.free.ratio")));
    assertEquals(0, value(registry, "trip.data.disk.sample.available"));
    metrics.refreshDisk();
    assertTrue(Double.isNaN(value(registry, "trip.data.disk.free.ratio")));
    assertEquals(0, value(registry, "trip.data.disk.sample.available"));
  }

  @Test void unavailablePathClearsPreviousHealthySampleEvenWhenJdbcAggregationFails() throws Exception {
    var upload = Files.createDirectory(temporary.resolve("sampled-uploads"));
    var registry = new SimpleMeterRegistry();
    var jdbc = mock(JdbcTemplate.class);
    when(jdbc.queryForList(anyString())).thenThrow(new DataAccessResourceFailureException("Synthetic aggregate failure"));
    var metrics = metrics(upload, jdbc, registry);
    metrics.refreshDisk();
    assertEquals(1, value(registry, "trip.data.disk.sample.available"));
    assertTrue(Double.isFinite(value(registry, "trip.data.disk.free.ratio")));
    assertThrows(DataAccessResourceFailureException.class, metrics::refresh);
    // Keep this test directory under a different name; no daily file removal.
    Files.move(upload, temporary.resolve("retained-uploads"));
    assertDoesNotThrow(metrics::refreshDisk);
    assertEquals(0, value(registry, "trip.data.disk.sample.available"));
    assertTrue(Double.isNaN(value(registry, "trip.data.disk.free.ratio")));
  }
}
