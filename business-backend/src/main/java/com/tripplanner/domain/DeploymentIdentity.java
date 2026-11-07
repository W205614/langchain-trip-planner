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
  @org.springframework.beans.factory.annotation.Autowired(required=false) private ObjectStorageService objects;
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
    verifyOrigins(db,objects);
    if(rows.isEmpty()){
      if(!enrollNew||!empty)throw new IllegalStateException("Existing dataset requires backup-backed enrollment before upgrade");
      verifyStorage(true);
      db.update("INSERT INTO deployment_identity(id,dataset_id) VALUES(1,?)",identity);
    }else{
      if(!identity.equals(rows.getFirst().get("dataset_id")))throw new IllegalStateException("Database DATASET_ID does not match this environment");
      verifyStorage(enrollNew&&empty);
    }
  }
  private void verifyStorage(boolean initialize)throws java.io.IOException {
    // Local archives remain part of a dataset after writes switch to S3.
    if(objects==null||objects.backend().equals("local"))verifyMarker(uploads,identity,initialize);
    else {if(Files.exists(uploads.resolve(".dataset-id")))verifyMarker(uploads,identity,false);objects.verifyDataset(identity,initialize);}
  }
  static void verifyOrigins(JdbcTemplate db,ObjectStorageService objects){
    if(objects==null)return;
    for(String[] source:new String[][]{{"knowledge_documents","storage","(status<>'deleted' OR EXISTS(SELECT 1 FROM knowledge_ingest_jobs j WHERE j.document_id=knowledge_documents.id AND j.status<>'succeeded'))"},
        {"upload_reservations","storage","status IN ('reserved','stored','uncertain')"},{"storage_migrations","source","true"},{"storage_migrations","target","true"}}){
      String table=source[0],column=source[1];
      boolean hasBackend=Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?)",Boolean.class,table,column+"_backend"));
      if(!hasBackend)continue;
      String predicate=column+"_backend='s3' AND "+source[2];
      if(!Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM "+table+" WHERE "+predicate+")",Boolean.class)))continue;
      boolean hasOrigin=Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?)",Boolean.class,table,column+"_origin"));
      if(!hasOrigin||Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM "+table+" WHERE "+predicate+" AND "+column+"_origin<>?)",Boolean.class,objects.s3Origin())))
        throw new IllegalStateException("S3 storage origin is unregistered or does not match this deployment; explicit restore/migration is required");
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
