package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.KnowledgeMapper;
import java.io.*;
import java.nio.file.Path;
import java.util.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

/** Durable quota reservations bracket object I/O; uncertain writes retain their quota. */
@Service
public class UploadStorage {
  private static final long LOCK=81126301351012L;
  private final JdbcTemplate db;private final TransactionTemplate tx;private final KnowledgeMapper documents;
  private final ObjectStorageService objects;private final long userLimit,globalLimit,minimumFree;private final int pendingLimit,rateLimit;
  private final Set<UUID> active=java.util.concurrent.ConcurrentHashMap.newKeySet();
  @Autowired
  public UploadStorage(JdbcTemplate db,TransactionTemplate tx,KnowledgeMapper documents,
      @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}") String directory,
      @Value("${UPLOAD_USER_BYTES:209715200}") long userLimit,@Value("${UPLOAD_GLOBAL_BYTES:2147483648}") long globalLimit,
      @Value("${UPLOAD_MIN_FREE_BYTES:536870912}") long minimumFree,@Value("${UPLOAD_PENDING_LIMIT:10}") int pendingLimit,
      @Value("${UPLOADS_PER_MINUTE:5}") int rateLimit,ObjectStorageService objects) {
    this.db=db;this.tx=tx;this.documents=documents;this.objects=objects;
    if(userLimit<1||globalLimit<userLimit||minimumFree<0||pendingLimit<1||rateLimit<1)throw new IllegalArgumentException("Invalid upload quota");
    this.userLimit=userLimit;this.globalLimit=globalLimit;this.minimumFree=minimumFree;this.pendingLimit=pendingLimit;this.rateLimit=rateLimit;
  }
  public UploadStorage(JdbcTemplate db,TransactionTemplate tx,KnowledgeMapper documents,String directory,long userLimit,long globalLimit,long minimumFree,int pendingLimit,int rateLimit){
    this(db,tx,documents,directory,userLimit,globalLimit,minimumFree,pendingLimit,rateLimit,ObjectStorageService.local(Path.of(directory)));
  }
  private void lock(){db.execute("SELECT pg_advisory_xact_lock("+LOCK+")");}
  private long scalar(String sql,Object...params){return Objects.requireNonNull(db.queryForObject(sql,Long.class,params));}
  public Map<String,Object> store(long uid,String city,String title,String original,String media,byte[] content) {
    try{return store(uid,city,title,original,media,content.length,ObjectStorageService.digest(new ByteArrayInputStream(content),content.length),()->new ByteArrayInputStream(content));}
    catch(IOException ex){throw new IllegalStateException(ex);}
  }
  public Map<String,Object> store(long uid,String city,String title,String original,String media,MultipartFile file)throws IOException {
    return store(uid,city,title,original,media,file.getSize(),ObjectStorageService.digest(file.getInputStream(),file.getSize()),file::getInputStream);
  }
  @FunctionalInterface private interface Source {InputStream open()throws IOException;}
  private Map<String,Object> store(long uid,String city,String title,String original,String media,long size,String hash,Source source) {
    if(size<1||size>20L*1024*1024)throw new ApiException(413,"文件不能超过20 MB");
    String suffix=switch(media){case "application/pdf"->".pdf";case "image/png"->".png";case "image/jpeg"->".jpg";case "image/gif"->".gif";default->".webp";};
    UUID id=UUID.randomUUID();String name=id+suffix;var location=objects.target(name);
    long free;
    try{objects.prepareWrite();free=objects.backend().equals("local")?objects.usableSpace():Long.MAX_VALUE;}
    catch(IOException ex){throw new ApiException(503,"上传存储暂不可用","UPLOAD_STORAGE_UNAVAILABLE");}
    active.add(id);
    Map<String,Object> duplicate;
    try { duplicate=tx.execute(s->{
      lock();requireActive(uid);var found=db.queryForList("SELECT id FROM knowledge_documents WHERE submitted_by=? AND sha256=? AND status<>'deleted' ORDER BY id LIMIT 1",uid,hash);
      db.update("DELETE FROM upload_reservations WHERE id IN (SELECT id FROM upload_reservations WHERE status IN ('committed','released') AND updated_at<timezone('UTC',now())-interval '7 days' LIMIT 100)");
      if(!found.isEmpty()) {
        var existing=documents.get(((Number)found.getFirst().get("id")).longValue());
        if(!city.equals(existing.get("city"))||!title.equals(existing.get("title")))throw new ApiException(409,"相同文件已投稿；请在我的投稿查看原资料，重复上传不会改变城市或标题","UPLOAD_DUPLICATE_METADATA");
        return existing;
      }
      if(scalar("SELECT count(*) FROM upload_reservations WHERE user_id=? AND sha256=? AND status IN ('reserved','stored','uncertain')",uid,hash)>0)throw new ApiException(409,"相同资料正在保存或等待核对，请勿重复提交","UPLOAD_RECONCILIATION_REQUIRED");
      if(scalar("SELECT count(*) FROM upload_reservations WHERE user_id=? AND created_at>timezone('UTC',now())-interval '1 minute'",uid)>=rateLimit)throw new ApiException(429,"资料上传过于频繁，请稍后重试","UPLOAD_RATE_LIMITED");
      long userBytes=scalar("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE submitted_by=? AND status<>'deleted'",uid)+scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE user_id=? AND status IN ('reserved','stored','uncertain')",uid);
      long globalBytes=scalar("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE status<>'deleted'")+scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE status IN ('reserved','stored','uncertain')");
      long pending=scalar("SELECT count(*) FROM knowledge_documents WHERE submitted_by=? AND status NOT IN ('published','deleted')",uid)+scalar("SELECT count(*) FROM upload_reservations WHERE user_id=? AND status IN ('reserved','stored','uncertain')",uid);
      if(userBytes>userLimit-size||pending>=pendingLimit)throw new ApiException(429,"上传容量或待审核资料数量已达上限","UPLOAD_USER_QUOTA");
      if(globalBytes>globalLimit-size)throw new ApiException(503,"上传存储额度已满，请稍后重试","UPLOAD_GLOBAL_QUOTA");
      if(location.backend().equals("local")){
        long outstanding=scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE storage_backend='local' AND status IN ('reserved','stored','uncertain')");
        if(free<minimumFree||free-minimumFree<size||outstanding>free-minimumFree-size)throw new ApiException(503,"存储空间不足，已暂停资料上传","UPLOAD_LOW_DISK");
      }
      db.update("INSERT INTO upload_reservations(id,user_id,sha256,size_bytes,stored_path,city,title,original_filename,media_type,status,storage_backend,storage_bucket,storage_key,storage_origin) VALUES(?,?,?,?,?,?,?,?,?,'reserved',?,?,?,?)",
        id,uid,hash,size,name,city,title,original,media,location.backend(),location.bucket(),location.key(),location.origin());return null;
    }); }catch(RuntimeException ex){active.remove(id);throw ex;}
    if(duplicate!=null){active.remove(id);return duplicate;}
    try {
      db.update("UPDATE upload_reservations SET write_started_at=timezone('UTC',now()) WHERE id=?",id);
      objects.put(location,source.open(),size,hash,media);
      objects.verify(location,size,hash);
      return tx.execute(s->{lock();var reservation=db.queryForMap("SELECT * FROM upload_reservations WHERE id=? FOR UPDATE",id);
        if("committed".equals(reservation.get("status")))return documents.get(((Number)reservation.get("document_id")).longValue());
        if(!Set.of("reserved","stored","uncertain").contains(reservation.get("status")))throw new ApiException(409,"上传预留状态已变更");
        return finish(reservation);});
    } catch(Exception ex) {
      try{db.update("UPDATE upload_reservations SET status='uncertain',updated_at=timezone('UTC',now()) WHERE id=? AND status<>'committed'",id);}catch(Exception ignored){}
      throw new ApiException(503,"资料保存状态待核对，请勿重复上传；管理员可执行存储核对","UPLOAD_RECONCILIATION_REQUIRED");
    }finally{active.remove(id);}
  }
  private Map<String,Object> finish(Map<String,Object> reservation) {
    requireActive(((Number)reservation.get("user_id")).longValue());
    var row=new HashMap<String,Object>();
    for(String field:List.of("city","title","original_filename","stored_path","sha256","media_type","size_bytes","storage_backend","storage_bucket","storage_key","storage_origin"))row.put(field,reservation.get(field));
    row.put("submitted_by",reservation.get("user_id"));documents.insert(row);
    db.update("UPDATE knowledge_documents SET storage_verified=true WHERE id=?",row.get("id"));
    db.update("UPDATE upload_reservations SET status='committed',document_id=?,updated_at=timezone('UTC',now()) WHERE id=?",row.get("id"),reservation.get("id"));
    return documents.get(((Number)row.get("id")).longValue());
  }
  private void requireActive(long uid){
    String state=db.queryForObject("SELECT account_state FROM users WHERE id=? FOR SHARE",String.class,uid);
    if(!"active".equals(state))throw new ApiException(403,"账号当前不可上传资料","ACCOUNT_INACTIVE");
  }
  public synchronized void eraseReservations(long uid)throws IOException {
    var reservations=db.queryForList("SELECT * FROM upload_reservations WHERE user_id=?",uid);
    if(reservations.stream().anyMatch(row->active.contains((UUID)row.get("id"))))throw new ApiException(409,"资料仍在上传，清理稍后继续","UPLOAD_IN_PROGRESS");
    for(var row:reservations){
      var location=ObjectStorageService.location(row);
      // A missing object does not prove a timed-out, already-started PUT is finished.
      if(row.get("write_started_at")!=null&&Set.of("reserved","uncertain").contains(row.get("status"))&&objects.stat(location)==null)
        throw new ApiException(409,"资料保存结果仍待核对，清理稍后继续","UPLOAD_IN_PROGRESS");
      objects.delete(location);
      db.update("DELETE FROM upload_reservations WHERE id=? AND user_id=?",row.get("id"),uid);
    }
  }
  public synchronized Object reconcile() {
    int recovered=0,released=0,uncertain=0;
    for(var reservation:db.queryForList("SELECT * FROM upload_reservations WHERE status IN ('reserved','stored','uncertain') AND updated_at<timezone('UTC',now())-interval '10 minutes' ORDER BY created_at LIMIT 100")){
      UUID id=(UUID)reservation.get("id");if(active.contains(id))continue;
      var linked=db.queryForList("SELECT id FROM knowledge_documents WHERE stored_path=? AND sha256=?",reservation.get("stored_path"),reservation.get("sha256"));
      if(!linked.isEmpty()){db.update("UPDATE upload_reservations SET status='committed',document_id=?,updated_at=timezone('UTC',now()) WHERE id=?",linked.getFirst().get("id"),id);recovered++;continue;}
      try {
        var location=ObjectStorageService.location(reservation);var state=objects.stat(location);
        if(state==null&&reservation.get("write_started_at")==null){db.update("UPDATE upload_reservations SET status='released',updated_at=timezone('UTC',now()) WHERE id=? AND write_started_at IS NULL AND status IN ('reserved','stored','uncertain')",id);released++;continue;}
        long expected=((Number)reservation.get("size_bytes")).longValue();
        if(expected>20L*1024*1024)throw new IOException("Invalid reserved size");
        objects.verify(location,expected,reservation.get("sha256").toString());
        tx.executeWithoutResult(s->{lock();var current=db.queryForMap("SELECT * FROM upload_reservations WHERE id=? FOR UPDATE",id);if(!"committed".equals(current.get("status")))finish(current);});recovered++;
      }catch(Exception ex){db.update("UPDATE upload_reservations SET status='uncertain',updated_at=timezone('UTC',now()) WHERE id=? AND status<>'committed'",id);uncertain++;}
    }
    return Map.of("success",true,"recovered",recovered,"released_missing",released,"uncertain_retained",uncertain);
  }
  public Object status(long uid,boolean admin) {
    var result=new LinkedHashMap<String,Object>();
    result.put("used_bytes",scalar("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE submitted_by=? AND status<>'deleted'",uid));
    result.put("reserved_bytes",scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE user_id=? AND status IN ('reserved','stored','uncertain')",uid));
    result.put("user_limit_bytes",userLimit);result.put("pending_limit",pendingLimit);result.put("uploads_per_minute",rateLimit);result.put("storage_backend",objects.backend());
    if(admin){result.put("reservations",db.queryForList("SELECT id,user_id,size_bytes,status,document_id,created_at,updated_at FROM upload_reservations WHERE status IN ('reserved','stored','uncertain') ORDER BY created_at LIMIT 100"));result.put("minimum_free_bytes",minimumFree);result.put("global_limit_bytes",globalLimit);}
    return result;
  }
}
