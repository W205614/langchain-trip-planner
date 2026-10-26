package com.tripplanner.domain;

import jakarta.annotation.PostConstruct;
import java.nio.file.*;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.sql.init.dependency.DependsOnDatabaseInitialization;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/** Refuse a known database paired with unregistered replacement upload/index volumes. */
@Component("deploymentIdentity")
@DependsOnDatabaseInitialization
public class DeploymentIdentity {
  private final JdbcTemplate db;
  private final String identity,environment;
  private final boolean enrollNew;
  private final Path uploads;
  public DeploymentIdentity(JdbcTemplate db,@Value("${DATASET_ID:}")String identity,
      @Value("${APP_ENV:development}")String environment,@Value("${DATASET_ENROLL_NEW:false}")boolean enrollNew,
      @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}")String uploads){
    this.db=db;this.identity=identity;this.environment=environment;this.enrollNew=enrollNew;this.uploads=Path.of(uploads);
  }
  @PostConstruct void verify() throws Exception {
    if(identity.isBlank()) {if(environment.equals("production"))throw new IllegalStateException("Production requires DATASET_ID and enrolled persistent mounts");return;}
    if(!identity.matches("[a-zA-Z0-9_-]{16,64}"))throw new IllegalStateException("Invalid DATASET_ID");
    var rows=db.queryForList("SELECT dataset_id FROM deployment_identity WHERE id=1");
    boolean empty=emptyBusiness();
    if(rows.isEmpty()){
      if(!enrollNew||!empty)throw new IllegalStateException("Existing dataset requires backup-backed enrollment before upgrade");
      verifyMarker(uploads,identity,true);
      db.update("INSERT INTO deployment_identity(id,dataset_id) VALUES(1,?)",identity);
    }else{
      if(!identity.equals(rows.getFirst().get("dataset_id")))throw new IllegalStateException("Database DATASET_ID does not match this environment");
      verifyMarker(uploads,identity,enrollNew&&empty);
    }
  }
  public boolean emptyBusiness(){return db.queryForObject("SELECT (SELECT count(*) FROM trip_records)+(SELECT count(*) FROM knowledge_documents)",Long.class)==0;}
  static void verifyMarker(Path folder,String identity,boolean initialize) throws java.io.IOException {
    Path marker=folder.resolve(".dataset-id");
    if(Files.isSymbolicLink(folder)||Files.isSymbolicLink(marker))throw new IllegalStateException("Dataset markers cannot be symlinks");
    if(Files.exists(marker)){if(!Files.readString(marker).strip().equals(identity))throw new IllegalStateException("Persistent mount belongs to another dataset");return;}
    Files.createDirectories(folder);
    try(var files=Files.list(folder)){if(!initialize||files.findAny().isPresent())throw new IllegalStateException("Persistent mount has no verified dataset marker");}
    Files.writeString(marker,identity+"\n",StandardOpenOption.CREATE_NEW);
  }
  public Map<String,Object> snapshot(){return Map.of("dataset_id",identity,"allow_initialize",enrollNew&&emptyBusiness());}
}
