package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.security.*;
import jakarta.annotation.PostConstruct;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import java.util.zip.*;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.Resource;
import org.springframework.core.io.FileSystemResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@Service
@org.springframework.context.annotation.DependsOn("singleInstance")
public class AccountDataService {
  @org.springframework.beans.factory.annotation.Autowired private UserQuotaService userQuotas;
  private final JdbcTemplate db;private final TransactionTemplate tx;private final JsonMapper json;
  private final AccountAccess access;private final AccountSecurityService security;private final MailDeliveryService mail;
  private final PublicOperationsService operations;private final TripLedgerService ledger;private final KnowledgeService knowledge;
  private final UploadStorage storage;private final BusinessAuditService audit;private final SingleInstance owner;
  private final Path exports;private final boolean workers;private final long maximum;
  public AccountDataService(JdbcTemplate db,TransactionTemplate tx,JsonMapper json,AccountAccess access,
      AccountSecurityService security,MailDeliveryService mail,PublicOperationsService operations,TripLedgerService ledger,
      KnowledgeService knowledge,UploadStorage storage,BusinessAuditService audit,SingleInstance owner,
      @Value("${ACCOUNT_EXPORT_DIR:${UPLOAD_DIR:../backend/data/knowledge_uploads}/account-exports}")String exports,
      @Value("${WORKERS_ENABLED:true}")boolean workers,@Value("${ACCOUNT_EXPORT_MAX_BYTES:536870912}")long maximum) {
    this.db=db;this.tx=tx;this.json=json;this.access=access;this.security=security;this.mail=mail;this.operations=operations;
    this.ledger=ledger;this.knowledge=knowledge;this.storage=storage;this.audit=audit;this.owner=owner;
    this.exports=Path.of(exports).toAbsolutePath().normalize();this.workers=workers;this.maximum=maximum;
    if(maximum<1024)throw new IllegalArgumentException("Export byte budget is too small");
  }
  @PostConstruct void recover(){if(workers)db.update("UPDATE account_data_jobs SET status='queued',stage='recovered',updated_at=timezone('UTC',now()) WHERE status='running'");}
  public Object jobs(long uid){return Map.of("data",db.queryForList("SELECT id,kind,status,stage,error_code,created_at,updated_at,expires_at FROM account_data_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 50",uid));}
  public Object adminJobs(){return Map.of("data",db.queryForList("SELECT id,user_id,kind,status,stage,error_code,created_at,updated_at FROM account_data_jobs ORDER BY updated_at DESC LIMIT 100"));}
  public Object requestExport(long uid,String requestId) {
    access.requireActive(uid);
    return tx.execute(s->{
      db.queryForMap("SELECT id FROM users WHERE id=? FOR UPDATE",uid);
      access.requireActive(uid);
      var existing=db.queryForList("SELECT id FROM account_data_jobs WHERE user_id=? AND kind='export' AND status IN ('queued','running')",uid);
      if(!existing.isEmpty())return Map.of("success",true,"id",existing.getFirst().get("id"),"cached",true);
      if(db.queryForObject("SELECT count(*) FROM account_data_jobs WHERE user_id=? AND kind='export' AND created_at>timezone('UTC',now())-interval '1 hour'",Long.class,uid)>=3)
        throw new ApiException(429,"数据导出过于频繁，请稍后重试","EXPORT_RATE_LIMITED");
      UUID id=UUID.randomUUID();db.update("INSERT INTO account_data_jobs(id,user_id,kind) VALUES(?,?,'export')",id,uid);
      audit.success(uid,"account.export_requested","data_job",id,null,requestId);
      return Map.of("success",true,"id",id,"cached",false);
    });
  }
  public Object preview(long uid) {
    access.requireActive(uid);
    return Map.of("owned_trips",count("SELECT count(*) FROM trip_records WHERE user_id=?",uid),
        "submissions",count("SELECT count(*) FROM knowledge_documents WHERE submitted_by=?",uid),
        "public_cards",count("SELECT count(*) FROM community_trip_cards WHERE owner_id=?",uid),
        "shared_memberships",count("SELECT count(*) FROM trip_members WHERE user_id=?",uid),
        "policy","本人行程及共享访问、公开投稿将撤回；私有资料和索引异步清理。在他人行程中的必要协作记录匿名保留。备份中的历史数据按其保留策略到期，恢复时必须重放注销记录。");
  }
  private long count(String sql,Object...args){return Objects.requireNonNull(db.queryForObject(sql,Long.class,args));}
  public Object erase(long uid,String password,String totp,String requestId) {
    byte[] bytes=new byte[32];new SecureRandom().nextBytes(bytes);String receipt=Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    UUID id=UUID.randomUUID();
    tx.executeWithoutResult(s->{
      access.requireActive(uid);security.verifyIdentity(uid,password,totp);operations.protectLastAdmin(uid);
      access.requireActive(uid);
      db.update("UPDATE users SET account_state='deleting',token_version=token_version+1 WHERE id=?",uid);
      db.update("INSERT INTO account_data_jobs(id,user_id,kind,receipt_hash,expires_at) VALUES(?,?,'erasure',?,timezone('UTC',now())+interval '30 days')",id,uid,AccountSecurityService.hash(receipt));
      db.update("INSERT INTO account_erasure_targets(job_id,kind,target_id) SELECT ?,'history',id FROM trip_records WHERE user_id=?",id,uid);
      db.update("INSERT INTO account_erasure_targets(job_id,kind,target_id) SELECT DISTINCT ?,'history',record_id FROM rag_sync_jobs WHERE user_id=? ON CONFLICT DO NOTHING",id,uid);
      db.update("INSERT INTO account_erasure_targets(job_id,kind,target_id,version) SELECT ?,'document',id,version+CASE WHEN status='deleted' THEN 0 ELSE 1 END FROM knowledge_documents WHERE submitted_by=?",id,uid);
      db.update("UPDATE trip_shares SET revoked_at=COALESCE(revoked_at,timezone('UTC',now())) WHERE owner_id=?",uid);
      db.update("UPDATE community_trip_cards SET status='rejected',snapshot_json='{}',title='已撤回',review_note='',published_at=NULL WHERE owner_id=?",uid);
      db.update("DELETE FROM trip_members WHERE user_id=? OR trip_id IN(SELECT id FROM trip_records WHERE user_id=?)",uid,uid);
      for(var document:db.queryForList("SELECT id,status FROM knowledge_documents WHERE submitted_by=?",uid))
        if(!"deleted".equals(document.get("status")))knowledge.review(((Number)document.get("id")).longValue(),uid,"delete",json.createObjectNode(),requestId);
      mail.revokeUser(uid);
      audit.success(uid,"account.erasure_requested","data_job",id,null,requestId);
    });
    db.update("UPDATE trip_tasks SET status='cancelled',stage='cancelled',error_code='ACCOUNT_NOT_ACTIVE',message='账号正在注销，任务停止' WHERE user_id=? AND status IN ('queued','running')",uid);
    return Map.of("success",true,"id",id,"receipt",receipt,"status","queued","message","账号访问已撤销。请保存注销凭据；清理完成前状态为处理中。");
  }
  public Object erasureStatus(UUID id,String receipt) {
    if(receipt==null||!receipt.matches("[A-Za-z0-9_-]{43}"))throw new ApiException(404,"注销记录不存在");
    var rows=db.queryForList("SELECT status,stage,error_code,receipt_hash FROM account_data_jobs WHERE id=? AND kind='erasure' AND expires_at>timezone('UTC',now())",id);
    if(rows.isEmpty()||!MessageDigest.isEqual(AccountSecurityService.hash(receipt).getBytes(StandardCharsets.US_ASCII),Objects.toString(rows.getFirst().get("receipt_hash"),"").getBytes(StandardCharsets.US_ASCII)))throw new ApiException(404,"注销记录不存在");
    var row=rows.getFirst();return Map.of("id",id,"status",row.get("status"),"stage",row.get("stage"),"error_code",row.get("error_code"));
  }
  private Path path(UUID id){return exports.resolve(id+".zip");}
  public Resource download(long uid,UUID id) {
    access.requireActive(uid);
    var jobs=db.queryForList("SELECT expires_at FROM account_data_jobs WHERE id=? AND user_id=? AND kind='export' AND status='succeeded'",id,uid);
    if(jobs.isEmpty())throw new ApiException(404,"导出文件不存在或尚未完成");
    if(((Timestamp)jobs.getFirst().get("expires_at")).toLocalDateTime().toInstant(ZoneOffset.UTC).isBefore(Instant.now()))throw new ApiException(410,"导出文件已过期，请重新申请");
    if(!Files.isRegularFile(path(id),LinkOption.NOFOLLOW_LINKS)||Files.isSymbolicLink(exports))throw new ApiException(404,"导出文件不可用，请重新申请");
    return new FileSystemResource(path(id));
  }
  @Scheduled(fixedDelayString="${ACCOUNT_DATA_SCAN_MILLIS:2000}",scheduler="accountDataScheduler")
  public void tick(){if(workers&&owner.owned())runOnce();}
  public synchronized boolean runOnce() {
    if(workers&&!owner.owned())return false;
    expireExports();
    var rows=db.queryForList("SELECT * FROM account_data_jobs WHERE status IN ('queued','waiting_cleanup') ORDER BY updated_at,id LIMIT 1");
    if(rows.isEmpty())return false;
    var job=rows.getFirst();UUID id=(UUID)job.get("id");long uid=((Number)job.get("user_id")).longValue();
    try {
      if("export".equals(job.get("kind"))){
        db.update("UPDATE account_data_jobs SET status='running',stage='building_archive',updated_at=timezone('UTC',now()) WHERE id=?",id);
        export(uid,id);
      } else eraseStep(uid,id,"waiting_cleanup".equals(job.get("status")));
    }catch(Exception ex){
      if(workers&&!owner.owned())return true;
      String code=ex instanceof ApiException api&&!api.code.isBlank()?api.code:"ACCOUNT_DATA_PROCESSING_FAILED";
      if("UPLOAD_IN_PROGRESS".equals(code)&&"erasure".equals(job.get("kind"))){db.update("UPDATE account_data_jobs SET status='waiting_cleanup',stage='waiting_for_upload',updated_at=timezone('UTC',now()) WHERE id=?",id);return true;}
      db.update("UPDATE account_data_jobs SET status='failed',error_code=?,expires_at=COALESCE(expires_at,timezone('UTC',now())+interval '24 hours'),updated_at=timezone('UTC',now()) WHERE id=? AND status NOT IN ('succeeded','expired')",code,id);
      LoggerFactory.getLogger(getClass()).warn("account_data_job_failed job_id={} kind={} error_type={}",id,job.get("kind"),ex.getClass().getSimpleName());
    }
    return true;
  }
  private void export(long uid,UUID id)throws IOException {
    access.requireActive(uid);Files.createDirectories(exports);
    if(Files.isSymbolicLink(exports))throw new IOException("Export directory cannot be a symlink");
    var snapshot=new TransactionTemplate(Objects.requireNonNull(tx.getTransactionManager()));
    snapshot.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);snapshot.setReadOnly(true);
    var data=snapshot.execute(s->{
      Map<String,Object> result=new LinkedHashMap<>();
      result.put("export",Map.of("schema","trip-account-export-v1","job_id",id,"generated_at",Instant.now().toString()));
      result.put("account",db.queryForMap("SELECT id,username,email,email_verified_at,created_at FROM users WHERE id=?",uid));
      result.put("preferences",db.queryForList("SELECT preferences,transportation,accommodation,created_at,updated_at FROM user_travel_preferences WHERE user_id=?",uid));
      result.put("trips",db.queryForList("SELECT * FROM trip_records WHERE user_id=? ORDER BY id",uid));
      result.put("trip_versions",db.queryForList("SELECT * FROM trip_record_versions WHERE user_id=? ORDER BY record_id,record_version",uid));
      result.put("favorites",db.queryForList("SELECT * FROM favorite_pois WHERE user_id=? ORDER BY id",uid));
      result.put("conversations",db.queryForList("SELECT * FROM assistant_conversations WHERE user_id=? ORDER BY id",uid));
      result.put("messages",db.queryForList("SELECT * FROM assistant_messages WHERE user_id=? ORDER BY id",uid));
      result.put("tasks",db.queryForList("SELECT id,request_json,usage_json,status,stage,error_code,created_at,updated_at FROM trip_tasks WHERE user_id=? ORDER BY created_at",uid));
      result.put("feedback",db.queryForList("SELECT id,category,subject,message,status,reply,created_at,updated_at FROM user_feedback WHERE user_id=? ORDER BY id",uid));
      result.put("commitments",db.queryForList("SELECT c.* FROM trip_commitments c JOIN trip_records r ON r.id=c.trip_id WHERE r.user_id=? ORDER BY c.id",uid));
      result.put("expenses",db.queryForList("SELECT e.* FROM trip_expenses e JOIN trip_records r ON r.id=e.trip_id WHERE r.user_id=? ORDER BY e.id",uid));
      result.put("submissions",db.queryForList("SELECT id,city,title,original_filename,status,media_type,size_bytes,source_text,extracted_pages_json,version,created_at,updated_at FROM knowledge_documents WHERE submitted_by=? ORDER BY id",uid));
      result.put("_originals",db.queryForList("SELECT * FROM knowledge_documents WHERE submitted_by=? AND status<>'deleted' ORDER BY id",uid));
      return result;
    });
    @SuppressWarnings("unchecked") var originals=(List<Map<String,Object>>)Objects.requireNonNull(data).remove("_originals");
    byte[] metadata=json.writeValueAsBytes(snapshotData(data));
    long total=metadata.length;if(total>maximum)throw new ApiException(413,"导出内容超过部署限制，请联系管理员","EXPORT_SIZE_LIMIT");
    Path temporary=exports.resolve(id+"-"+UUID.randomUUID()+".part");
    try {
    try(var zip=new ZipOutputStream(Files.newOutputStream(temporary,StandardOpenOption.CREATE_NEW),StandardCharsets.UTF_8)) {
      zip.putNextEntry(new ZipEntry("account.json"));zip.write(metadata);zip.closeEntry();
      for(var row:originals) {
        total+=((Number)row.get("size_bytes")).longValue();if(total>maximum)throw new ApiException(413,"导出文件超过部署限制，请联系管理员","EXPORT_SIZE_LIMIT");
        String filename=row.get("original_filename").toString().replaceAll("[^\\p{L}\\p{N}._-]","_");
        zip.putNextEntry(new ZipEntry("originals/"+row.get("id")+"-"+filename));
        MessageDigest digest;try{digest=MessageDigest.getInstance("SHA-256");}catch(NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
        long expected=((Number)row.get("size_bytes")).longValue(),copied=0;
        try(InputStream input=knowledge.openOriginal(row)){byte[] buffer=new byte[8192];int read;while((read=input.read(buffer))!=-1){copied+=read;if(copied>expected)throw new IOException("Original exceeds declared size");digest.update(buffer,0,read);zip.write(buffer,0,read);}}
        if(copied!=expected||!HexFormat.of().formatHex(digest.digest()).equals(row.get("sha256")))throw new IOException("Original content changed");zip.closeEntry();
      }
    }
    tx.executeWithoutResult(s->{
      userQuotas.lockActive(uid);requireOwnership();
      try{Files.move(temporary,path(id),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);}
      catch(IOException ex){throw new UncheckedIOException(ex);}
      db.update("UPDATE account_data_jobs SET status='succeeded',stage='available',file_name=?,expires_at=timezone('UTC',now())+interval '24 hours',updated_at=timezone('UTC',now()) WHERE id=?",id+".zip",id);
    });
    } finally {Files.deleteIfExists(temporary);}
  }
  // JDBC JSONB objects must be represented as parsed JSON rather than driver implementation fields.
  private Object snapshotData(Object value) {
    if(value!=null&&value.getClass().getName().equals("org.postgresql.util.PGobject")){try{return json.readTree(value.toString());}catch(Exception ex){throw new IllegalStateException("Invalid stored JSON",ex);}}
    if(value instanceof Map<?,?> source){var result=new LinkedHashMap<String,Object>();source.forEach((key,item)->result.put(key.toString(),snapshotData(item)));return result;}
    if(value instanceof List<?> list)return list.stream().map(this::snapshotData).toList();
    return value;
  }
  private void eraseStep(long uid,UUID id,boolean waiting)throws IOException {
    requireOwnership();
    if(!waiting) {
      db.update("UPDATE account_data_jobs SET status='running',stage='withdrawing_content',updated_at=timezone('UTC',now()) WHERE id=?",id);
      tx.executeWithoutResult(s->{
        for(long record:db.queryForList("SELECT id FROM trip_records WHERE user_id=?",Long.class,uid))ledger.delete(uid,record,"account-erasure");
        for(var target:db.queryForList("SELECT target_id FROM account_erasure_targets WHERE job_id=? AND kind='history'",id)) {
          long record=((Number)target.get("target_id")).longValue();
          db.update("INSERT INTO rag_sync_jobs(record_id,user_id,operation) VALUES(?,?,'delete')",record,uid);
        }
        for(var document:db.queryForList("SELECT id,version FROM knowledge_documents WHERE submitted_by=? AND status='deleted'",uid))
          db.update("INSERT INTO knowledge_ingest_jobs(document_id,document_version,phase) VALUES(?,?,'delete')",document.get("id"),document.get("version"));
        db.update("DELETE FROM community_trip_cards WHERE owner_id=?",uid);
        db.update("DELETE FROM assistant_conversations WHERE user_id=?",uid);
        db.update("DELETE FROM favorite_pois WHERE user_id=?",uid);db.update("DELETE FROM user_travel_preferences WHERE user_id=?",uid);
        db.update("DELETE FROM trip_notifications WHERE user_id=?",uid);db.update("DELETE FROM trip_usage_policies WHERE user_id=?",uid);
        db.update("DELETE FROM execution_checkpoints WHERE task_id IN(SELECT id FROM trip_tasks WHERE user_id=?)",uid);
        db.update("UPDATE trip_tasks SET request_json='{}',idempotency_key='erased:'||id,fingerprint='',request_id='',message='账号已注销' WHERE user_id=?",uid);
        db.update("DELETE FROM user_feedback WHERE user_id=?",uid);
        db.update("UPDATE account_data_jobs SET status='waiting_cleanup',stage='waiting_for_index_and_files',updated_at=timezone('UTC',now()) WHERE id=?",id);
      });
      return;
    }
    long pending=count("SELECT count(*) FROM account_erasure_targets t WHERE t.job_id=? AND ((t.kind='history' AND NOT EXISTS(SELECT 1 FROM rag_sync_jobs j WHERE j.record_id=t.target_id AND j.user_id=? AND j.operation='delete' AND j.status='succeeded')) OR (t.kind='document' AND NOT EXISTS(SELECT 1 FROM knowledge_ingest_jobs j WHERE j.document_id=t.target_id AND j.document_version=t.version AND j.status='succeeded')))",id,uid);
    if(pending>0){db.update("UPDATE account_data_jobs SET updated_at=timezone('UTC',now()) WHERE id=?",id);return;}
    storage.eraseReservations(uid);
    for(var export:db.queryForList("SELECT id FROM account_data_jobs WHERE user_id=? AND kind='export'",uid)) {
      UUID exportId=(UUID)export.get("id");Files.deleteIfExists(path(exportId));
      if(Files.isDirectory(exports,LinkOption.NOFOLLOW_LINKS))try(var parts=Files.newDirectoryStream(exports,exportId+"-*.part")){for(Path part:parts)Files.delete(part);}
    }
    tx.executeWithoutResult(s->{
      requireOwnership();
      db.update("DELETE FROM knowledge_documents WHERE submitted_by=?",uid);
      db.update("DELETE FROM account_security WHERE user_id=?",uid);mail.revokeUser(uid);
      db.update("UPDATE mail_deliveries SET user_id=NULL WHERE user_id=?",uid);
      String username=db.queryForObject("SELECT username FROM users WHERE id=?",String.class,uid);
      db.update("DELETE FROM account_login_attempts WHERE identity_hash=?",AccountSecurityService.hash(Objects.requireNonNull(username).strip().toLowerCase(Locale.ROOT)));
      db.update("UPDATE trip_expenses SET note='' WHERE created_by=?",uid);
      db.update("UPDATE trip_expenses SET void_reason='' WHERE voided_by=?",uid);
      db.update("UPDATE trip_commitments SET note='' WHERE created_by=?",uid);
      db.update("DELETE FROM user_operation_quotas WHERE user_id=?",uid);
      db.update("UPDATE account_data_jobs SET status='expired',stage='erased',file_name=NULL WHERE user_id=? AND kind='export'",uid);
      db.update("UPDATE business_audit_events SET actor_id=NULL,actor_type='system',metadata_json='{}',request_id='' WHERE actor_id=?",uid);
      db.update("UPDATE users SET username=?,email=NULL,email_verified_at=NULL,hashed_password=?,is_admin=false,account_state='deleted',token_version=token_version+1 WHERE id=?","deleted_"+UUID.randomUUID().toString().replace("-",""),Passwords.hash(UUID.randomUUID().toString()),uid);
      db.update("UPDATE account_data_jobs SET status='succeeded',stage='erased',error_code='',updated_at=timezone('UTC',now()) WHERE id=?",id);
      audit.success(null,"account.erasure_completed","data_job",id,null,"");
    });
  }
  private void expireExports() {
    for(var row:db.queryForList("SELECT id FROM account_data_jobs WHERE kind='export' AND status IN ('succeeded','failed') AND expires_at<timezone('UTC',now()) LIMIT 20")) {
      UUID id=(UUID)row.get("id");try{Files.deleteIfExists(path(id));
        if(Files.isDirectory(exports,LinkOption.NOFOLLOW_LINKS))try(var parts=Files.newDirectoryStream(exports,id+"-*.part")){for(Path part:parts)Files.delete(part);}
        db.update("UPDATE account_data_jobs SET status='expired',stage='expired',file_name=NULL WHERE id=?",id);}
      catch(IOException ex){LoggerFactory.getLogger(getClass()).warn("account_export_expiry_failed job_id={} error_type={}",id,ex.getClass().getSimpleName());}
    }
  }
  private void requireOwnership(){if(workers&&!owner.owned())throw new ApiException(503,"数据作业调度所有权已丢失","SCHEDULER_OWNERSHIP_LOST");}
  public Object replay(long idOwner,UUID id,String requestId) {
    return tx.execute(s->{
      var rows=db.queryForList("SELECT kind,user_id,stage FROM account_data_jobs WHERE id=? AND status='failed' FOR UPDATE",id);
      if(rows.isEmpty())throw new ApiException(409,"当前作业不可重放");
      var job=rows.getFirst();
      db.queryForMap("SELECT id FROM users WHERE id=? FOR UPDATE",job.get("user_id"));
      if(count("SELECT count(*) FROM account_data_jobs WHERE user_id=? AND kind=? AND id<>? AND status IN ('queued','running','waiting_cleanup')",job.get("user_id"),job.get("kind"),id)>0)throw new ApiException(409,"该用户已有同类活动作业");
      String next="erasure".equals(job.get("kind"))&&Set.of("waiting_for_index_and_files","waiting_for_upload").contains(job.get("stage"))?"waiting_cleanup":"queued";
      db.update("UPDATE account_data_jobs SET status=?,error_code='',stage=?,updated_at=timezone('UTC',now()) WHERE id=?",next,next.equals("waiting_cleanup")?"waiting_for_index_and_files":"queued",id);
      audit.success(idOwner,"account.data_job_replayed","data_job",id,null,requestId);return Map.of("success",true);
    });
  }
}
