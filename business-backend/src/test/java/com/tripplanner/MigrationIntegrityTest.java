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
  void v9UpgradePreservesTripsAndCommitmentsAndInvalidatesPlanningInput() throws Exception {
    String url=System.getenv("TEST_DATABASE_URL"),password=System.getenv().getOrDefault("TEST_DATABASE_PASSWORD","isolated-test-only");
    String schema="upgrade_"+UUID.randomUUID().toString().replace("-","");
    try {
      Flyway.configure().dataSource(url,"trip",password).schemas(schema).defaultSchema(schema).target(MigrationVersion.fromVersion("9")).load().migrate();
      try(var c=DriverManager.getConnection(url,"trip",password);var st=c.createStatement()){
        st.execute("SET search_path TO "+schema);
        st.execute("INSERT INTO users(id,username,hashed_password) VALUES(1,'upgrade','test')");
        st.execute("INSERT INTO trip_records(id,user_id,city,start_date,end_date,plan_json) VALUES(1,1,'北京','2026-11-01','2026-11-01','{\"legacy\":true}')");
        st.execute("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,created_by) VALUES(1,0,'ticket','预约','confirmed',1)");
      }
      Flyway.configure().dataSource(url,"trip",password).schemas(schema).defaultSchema(schema).load().migrate();
      try(var c=DriverManager.getConnection(url,"trip",password);var st=c.createStatement()){
        st.execute("SET search_path TO "+schema);
        try(var rs=st.executeQuery("SELECT plan_json,planning_revision FROM trip_records WHERE id=1")){rs.next();org.junit.jupiter.api.Assertions.assertEquals("{\"legacy\":true}",rs.getString(1));org.junit.jupiter.api.Assertions.assertEquals(1,rs.getInt(2));}
        st.execute("UPDATE trip_commitments SET note='changed' WHERE trip_id=1");
        try(var rs=st.executeQuery("SELECT planning_revision FROM trip_records WHERE id=1")){rs.next();org.junit.jupiter.api.Assertions.assertEquals(2,rs.getInt(1));}
      }
    } finally {try(var c=DriverManager.getConnection(url,"trip",password);var st=c.createStatement()){st.execute("DROP SCHEMA IF EXISTS "+schema+" CASCADE");}}
  }
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
