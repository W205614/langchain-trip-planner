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
    assertEquals(0,flyway.info().pending().length);
    assertTrue(new JdbcTemplate(source).queryForObject("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='knowledge_documents' AND column_name='storage_backend')",Boolean.class));
    assertEquals(id,Files.readString(uploads.resolve(".dataset-id")).strip());
  }
  @Test void changedS3OriginRejectsBeforeFlywayAndBeforeAnyRemoteProbe()throws Exception {
    var source=fresh();var db=new JdbcTemplate(source);
    var baseline=Flyway.configure().dataSource(source).locations("classpath:db/migration").load();baseline.migrate();
    db.update("INSERT INTO deployment_identity(id,dataset_id) VALUES(1,?)",id);
    long uid=db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'fixture') RETURNING id",Long.class,"origin_"+UUID.randomUUID());
    db.update("INSERT INTO knowledge_documents(submitted_by,city,title,original_filename,stored_path,sha256,media_type,storage_backend,storage_bucket,storage_key,storage_origin) VALUES(?,'北京','来源','fixture.pdf','fixture.pdf',?,'application/pdf','s3','original-bucket','uploads/fixture.pdf',?)",uid,"0".repeat(64),"a".repeat(64));
    DeploymentIdentity.verifyMarker(uploads,id,true);
    var env=new org.springframework.mock.env.MockEnvironment().withProperty("UPLOAD_STORAGE_BACKEND","s3").withProperty("S3_BUCKET","other-bucket").withProperty("S3_ENDPOINT","https://never-contact.example.invalid");
    try(var objects=new ObjectStorageService(env)){
      var flyway=spy(baseline);String before=db.queryForObject("SELECT max(installed_rank)::text FROM flyway_schema_history",String.class);
      var strategy=new DeploymentMigrationGuard().verifiedObjectStorageMigration(id,"production",false,uploads.toString(),objects);
      var failure=assertThrows(IllegalStateException.class,()->strategy.migrate(flyway));
      assertTrue(failure.getMessage().contains("S3 storage origin"));verify(flyway,never()).migrate();
      assertEquals(before,db.queryForObject("SELECT max(installed_rank)::text FROM flyway_schema_history",String.class));
    }
  }
}
