package com.tripplanner.domain;

import java.nio.file.Path;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.flyway.autoconfigure.FlywayMigrationStrategy;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jdbc.core.JdbcTemplate;

/** Verify known dataset ownership before Flyway can issue DDL against it. */
@Configuration(proxyBeanMethods=false)
public class DeploymentMigrationGuard {
  @Bean FlywayMigrationStrategy verifiedDatasetMigration(
      @Value("${DATASET_ID:}") String identity,
      @Value("${APP_ENV:development}") String environment,
      @Value("${DATASET_ENROLL_NEW:false}") boolean enrollNew,
      @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}") String uploads) {
    return flyway -> {
      verify(flyway.getConfiguration().getDataSource(),identity,environment,enrollNew,Path.of(uploads));
      flyway.migrate();
    };
  }

  static void verify(DataSource source,String identity,String environment,boolean enrollNew,Path uploads) {
    if(identity.isBlank()) {
      if(environment.equals("production"))throw new IllegalStateException("Production requires DATASET_ID and enrolled persistent mounts");
      return;
    }
    if(!identity.matches("[a-zA-Z0-9_-]{16,64}"))throw new IllegalStateException("Invalid DATASET_ID");
    var db=new JdbcTemplate(source);
    boolean hasIdentity=Boolean.TRUE.equals(db.queryForObject("SELECT to_regclass('public.deployment_identity') IS NOT NULL",Boolean.class));
    var rows=hasIdentity?db.queryForList("SELECT dataset_id FROM deployment_identity WHERE id=1"):java.util.List.<java.util.Map<String,Object>>of();
    boolean empty=true;
    for(String table:java.util.List.of("trip_records","knowledge_documents"))
      if(Boolean.TRUE.equals(db.queryForObject("SELECT to_regclass('public."+table+"') IS NOT NULL",Boolean.class))
          &&db.queryForObject("SELECT count(*) FROM "+table,Long.class)>0)empty=false;
    if(rows.isEmpty()&&(!enrollNew||!empty))throw new IllegalStateException("Existing dataset requires backup-backed enrollment before upgrade");
    if(!rows.isEmpty()&&!identity.equals(rows.getFirst().get("dataset_id")))throw new IllegalStateException("Database DATASET_ID does not match this environment");
    try{DeploymentIdentity.verifyMarker(uploads,identity,enrollNew&&empty);}
    catch(java.io.IOException ex){throw new IllegalStateException("Persistent mount cannot be verified before migration",ex);}
  }
}
