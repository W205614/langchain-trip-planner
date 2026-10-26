package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import java.nio.file.*;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class DeploymentMigrationGuardTest {
  @TempDir Path uploads;
  final String id="0123456789abcdef0123456789abcdef";
  DriverManagerDataSource fresh(){
    String url=System.getenv("TEST_DATABASE_URL"),password=System.getenv().getOrDefault("TEST_DATABASE_PASSWORD","isolated-test-only");
    var administrative=new DriverManagerDataSource(url,"trip",password);
    String database="tripopt_guard_"+UUID.randomUUID().toString().replace("-","");
    new JdbcTemplate(administrative).execute("CREATE DATABASE "+database);
    return new DriverManagerDataSource(url.substring(0,url.lastIndexOf('/')+1)+database,"trip",password);
  }
  @Test void wrongIdentityRefusesEvenFlywayDdlBeforeItRuns() throws Exception{
    var source=fresh();var db=new JdbcTemplate(source);
    db.execute("CREATE TABLE deployment_identity(id int primary key,dataset_id varchar(64))");
    db.update("INSERT INTO deployment_identity VALUES(1,?)",id);
    DeploymentIdentity.verifyMarker(uploads,id,true);
    var flyway=spy(Flyway.configure().dataSource(source).locations("classpath:db/migration").load());
    var strategy=new DeploymentMigrationGuard().verifiedDatasetMigration("another-dataset-identity","production",false,uploads.toString());
    assertThrows(IllegalStateException.class,()->strategy.migrate(flyway));
    verify(flyway,never()).migrate();
    assertFalse(db.queryForObject("SELECT to_regclass('public.flyway_schema_history') IS NOT NULL",Boolean.class));
    assertEquals(id,Files.readString(uploads.resolve(".dataset-id")).strip());
  }
  @Test void unregisteredExistingBusinessAndWrongUploadBothFailWithoutFlyway() throws Exception{
    var source=fresh();var db=new JdbcTemplate(source);
    db.execute("CREATE TABLE trip_records(id int)");db.update("INSERT INTO trip_records VALUES(7)");
    assertThrows(IllegalStateException.class,()->DeploymentMigrationGuard.verify(source,id,"production",true,uploads));
    assertEquals(7,db.queryForObject("SELECT id FROM trip_records",Integer.class));
    db.execute("CREATE TABLE deployment_identity(id int primary key,dataset_id varchar(64))");db.update("INSERT INTO deployment_identity VALUES(1,?)",id);
    DeploymentIdentity.verifyMarker(uploads,"another-dataset-identity",true);
    assertThrows(IllegalStateException.class,()->DeploymentMigrationGuard.verify(source,id,"production",false,uploads));
    assertEquals("another-dataset-identity",Files.readString(uploads.resolve(".dataset-id")).strip());
    assertFalse(db.queryForObject("SELECT to_regclass('public.flyway_schema_history') IS NOT NULL",Boolean.class));
  }
  @Test void explicitNewDatasetCanMigrateAndVerifyItsOriginalMarker() throws Exception{
    var source=fresh();
    var flyway=Flyway.configure().dataSource(source).locations("classpath:db/migration").load();
    new DeploymentMigrationGuard().verifiedDatasetMigration(id,"production",true,uploads.toString()).migrate(flyway);
    assertEquals("14",new JdbcTemplate(source).queryForObject("SELECT version FROM flyway_schema_history ORDER BY installed_rank DESC LIMIT 1",String.class));
    assertEquals(id,Files.readString(uploads.resolve(".dataset-id")).strip());
  }
}
