package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.io.IOException;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

/** Copy and verify outside transactions, then compare the exact source before switching. */
@Service
public class StorageMigrationService {
  private final JdbcTemplate db;private final TransactionTemplate tx;private final ObjectStorageService objects;
  private final Set<Long> active=java.util.concurrent.ConcurrentHashMap.newKeySet();
  public StorageMigrationService(JdbcTemplate db,TransactionTemplate tx,ObjectStorageService objects){this.db=db;this.tx=tx;this.objects=objects;}
  public Object preview(int limit){
    if(limit<1||limit>100)throw new ApiException(422,"迁移批次须为1至100");
    var candidates=new ArrayList<Object>();
    var target=objects.target("preview");String targetPrefix=target.key().substring(0,target.key().length()-7);
    for(var row:db.queryForList("SELECT id,stored_path,storage_backend,storage_bucket,storage_key,storage_origin,size_bytes FROM knowledge_documents WHERE status<>'deleted' AND (storage_backend<>? OR storage_bucket<>? OR storage_key<>?||regexp_replace(replace(stored_path,chr(92),'/'),'^.*/','')) ORDER BY id LIMIT ?",target.backend(),target.bucket(),targetPrefix,limit)){
      var from=ObjectStorageService.location(row);var to=objects.target(row.get("stored_path").toString().replace('\\','/').replaceAll("^.*/",""));
      if(!from.equals(to)){candidates.add(Map.of("document_id",row.get("id"),"source_backend",from.backend(),"target_backend",to.backend(),"size_bytes",row.get("size_bytes")));if(candidates.size()>=limit)break;}
    }
    return Map.of("target",objects.configuration(),"candidates",candidates,"writes_performed",false);
  }
  public Object migrate(long id){
    if(!active.add(id))throw new ApiException(409,"该资料正在迁移","STORAGE_MIGRATION_IN_PROGRESS");
    try{return copy(id);}finally{
      active.remove(id);
      // A delete racing a copy needs a fresh durable cleanup after all object writes stop.
      db.update("INSERT INTO knowledge_ingest_jobs(document_id,document_version,phase) SELECT id,version,'delete' FROM knowledge_documents WHERE id=? AND status='deleted'",id);
    }
  }
  private Object copy(long id){
    var rows=db.queryForList("SELECT * FROM knowledge_documents WHERE id=? AND status<>'deleted'",id);
    if(rows.isEmpty())throw new ApiException(404,"资料不存在");
    var row=rows.getFirst();var from=ObjectStorageService.location(row);
    var to=objects.target(row.get("stored_path").toString().replace('\\','/').replaceAll("^.*/",""));
    if(from.equals(to))return Map.of("success",true,"document_id",id,"status","already_current");
    try{
      var state=objects.stat(from);if(state==null)throw new IOException("Original missing");
      long size=state.size();if(size<1||size>20L*1024*1024)throw new IOException("Invalid original size");
      String hash=row.get("sha256").toString();objects.verify(from,size,hash);
      db.update("INSERT INTO storage_migrations(document_id,target_backend,target_bucket,target_key,target_origin,source_backend,source_bucket,source_key,source_origin,source_version,sha256,size_bytes,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'copying') ON CONFLICT DO NOTHING",
        id,to.backend(),to.bucket(),to.key(),to.origin(),from.backend(),from.bucket(),from.key(),from.origin(),row.get("version"),hash,size);
      if(objects.stat(to)==null){objects.put(to,objects.open(from),size,hash,row.get("media_type").toString());}
      objects.verify(to,size,hash);
      boolean switched=tx.execute(s->{
        var current=db.queryForList("SELECT * FROM knowledge_documents WHERE id=? FOR UPDATE",id);
        if(current.isEmpty()||"deleted".equals(current.getFirst().get("status"))||!current.getFirst().get("version").equals(row.get("version"))||!ObjectStorageService.location(current.getFirst()).equals(from)){
          db.update("UPDATE storage_migrations SET status='conflict',updated_at=timezone('UTC',now()) WHERE document_id=? AND target_backend=? AND target_bucket=? AND target_key=?",id,to.backend(),to.bucket(),to.key());return false;
        }
        db.update("UPDATE knowledge_documents SET storage_backend=?,storage_bucket=?,storage_key=?,storage_origin=?,storage_verified=true,size_bytes=? WHERE id=?",to.backend(),to.bucket(),to.key(),to.origin(),size,id);
        db.update("UPDATE upload_reservations SET storage_backend=?,storage_bucket=?,storage_key=?,storage_origin=? WHERE document_id=? AND status='committed'",to.backend(),to.bucket(),to.key(),to.origin(),id);
        db.update("UPDATE storage_migrations SET status='switched',updated_at=timezone('UTC',now()) WHERE document_id=? AND target_backend=? AND target_bucket=? AND target_key=?",id,to.backend(),to.bucket(),to.key());return true;
      });
      if(!switched)throw new ApiException(409,"资料在复制期间已变更；源文件和副本均保留","STORAGE_MIGRATION_CONFLICT");
      return Map.of("success",true,"document_id",id,"status","switched","source_retained",true);
    }catch(ApiException ex){throw ex;}catch(Exception ex){throw new ApiException(503,"存储迁移未完成；原引用保留，可重试核对","STORAGE_MIGRATION_UNAVAILABLE");}
  }
  public void removeRetainedCopies(long id)throws IOException {
    if(active.contains(id))throw new IOException("Storage copy still active");
    for(var row:db.queryForList("SELECT * FROM storage_migrations WHERE document_id=?",id)){
      objects.delete(new ObjectStorageService.Location(row.get("source_backend").toString(),row.get("source_bucket").toString(),row.get("source_key").toString(),row.get("source_origin").toString()));
      objects.delete(new ObjectStorageService.Location(row.get("target_backend").toString(),row.get("target_bucket").toString(),row.get("target_key").toString(),row.get("target_origin").toString()));
      db.update("DELETE FROM storage_migrations WHERE document_id=? AND target_backend=? AND target_bucket=? AND target_key=?",id,row.get("target_backend"),row.get("target_bucket"),row.get("target_key"));
    }
  }
}
