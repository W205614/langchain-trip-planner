package com.tripplanner;

import static org.junit.jupiter.api.Assertions.assertThrows;

import java.sql.DriverManager;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationVersion;
import org.flywaydb.core.api.FlywayException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

@EnabledIfEnvironmentVariable(named = "TEST_DATABASE_URL", matches = ".+")
class MigrationIntegrityTest {
  @Test
  void v4RefusesOrphanRowsInsteadOfSilentlyDeletingThem() throws Exception {
    String url = System.getenv("TEST_DATABASE_URL");
    String password = System.getenv().getOrDefault("TEST_DATABASE_PASSWORD", "isolated-test-only");
    String schema = "migration_" + UUID.randomUUID().toString().replace("-", "");
    try {
      Flyway.configure()
          .dataSource(url, "trip", password)
          .schemas(schema)
          .defaultSchema(schema)
          .target(MigrationVersion.fromVersion("3"))
          .load()
          .migrate();
      try (var connection = DriverManager.getConnection(url, "trip", password);
          var statement = connection.createStatement()) {
        statement.execute("SET search_path TO " + schema);
        statement.execute("INSERT INTO user_travel_preferences(user_id) VALUES (9223372036854775807)");
      }
      var upgrade =
          Flyway.configure()
              .dataSource(url, "trip", password)
              .schemas(schema)
              .defaultSchema(schema)
              .load();
      assertThrows(FlywayException.class, () -> upgrade.migrate());
    } finally {
      try (var connection = DriverManager.getConnection(url, "trip", password);
          var statement = connection.createStatement()) {
        statement.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE");
      }
    }
  }
}
