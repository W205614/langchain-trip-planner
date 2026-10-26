package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DelegatingDataSource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class DatabaseCapacityMetricsTest {
  private DriverManagerDataSource source() {
    var source = new DriverManagerDataSource();
    source.setUrl(System.getenv("TEST_DATABASE_URL"));
    source.setUsername(System.getenv().getOrDefault("TEST_DATABASE_USER", "trip"));
    source.setPassword(password());
    return source;
  }
  private String password() { return System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", "isolated-test-only"); }
  private double value(SimpleMeterRegistry registry, String name) { return registry.get(name).gauge().value(); }

  @Test void realPostgresSizeIsCachedAndWarningIsConfigurationNotQuota() {
    var source = new CountingSource(source());
    var registry = new SimpleMeterRegistry();
    var metrics = new DatabaseCapacityMetrics(source, registry, 1, 2);
    assertTrue(Double.isNaN(value(registry, "trip.database.size.bytes")));
    assertEquals(0, value(registry, "trip.database.capacity.sample.available"));
    assertEquals(Double.POSITIVE_INFINITY, value(registry, "trip.database.capacity.last.success.age.seconds"));
    metrics.refresh();
    assertEquals(1, value(registry, "trip.database.capacity.sample.available"));
    long actual = new JdbcTemplate(source).queryForObject("SELECT pg_database_size(current_database())", Long.class);
    assertEquals(actual, value(registry, "trip.database.size.bytes"));
    assertEquals(1, value(registry, "trip.database.size.warning.bytes"));
    assertTrue(value(registry, "trip.database.size.bytes") > value(registry, "trip.database.size.warning.bytes"));
    int connections = source.connections.get();
    for (int i = 0; i < 10; i++) {
      value(registry, "trip.database.size.bytes");
      value(registry, "trip.database.capacity.last.success.age.seconds");
    }
    assertEquals(connections, source.connections.get(), "Metric reads must not run database queries");
  }

  @Test void actualPostgresAuthenticationFailureClearsHealthyValueAndRecoveryResamples() throws Exception {
    var source = source();
    var registry = new SimpleMeterRegistry();
    var metrics = new DatabaseCapacityMetrics(source, registry, Long.MAX_VALUE, 2);
    metrics.refresh();
    assertEquals(1, value(registry, "trip.database.capacity.sample.available"));
    assertTrue(Double.isFinite(value(registry, "trip.database.size.bytes")));
    Thread.sleep(15);
    double previousAge = value(registry, "trip.database.capacity.last.success.age.seconds");
    // Change only this test datasource. The next real PG connection rejects
    // authentication; no role, database, server or production setting changes.
    source.setPassword("wrong-test-only-" + UUID.randomUUID());
    assertDoesNotThrow(metrics::refresh);
    assertEquals(0, value(registry, "trip.database.capacity.sample.available"));
    assertTrue(Double.isNaN(value(registry, "trip.database.size.bytes")), "Do not retain old healthy bytes");
    assertTrue(value(registry, "trip.database.capacity.last.success.age.seconds") >= previousAge);
    source.setPassword(password());
    metrics.refresh();
    assertEquals(1, value(registry, "trip.database.capacity.sample.available"));
    assertTrue(Double.isFinite(value(registry, "trip.database.size.bytes")));
  }

  @Test void invalidWarningCannotSilentlyDisableTheAlert() {
    assertThrows(IllegalArgumentException.class,
        () -> new DatabaseCapacityMetrics(source(), new SimpleMeterRegistry(), 0, 2));
  }

  private static class CountingSource extends DelegatingDataSource {
    final AtomicInteger connections = new AtomicInteger();
    CountingSource(DataSource source) { super(source); }
    @Override public Connection getConnection() throws SQLException {
      connections.incrementAndGet();
      return super.getConnection();
    }
  }
}
