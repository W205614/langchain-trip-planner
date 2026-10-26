package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.KnowledgeMapper;
import java.nio.file.*;
import java.security.MessageDigest;
import java.io.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

/** Cross-process PostgreSQL reservations account for bytes before the filesystem write. */
@Service
public class UploadStorage {
  private static final long LOCK=81126301351012L;
  private final JdbcTemplate db;private final TransactionTemplate tx;private final KnowledgeMapper documents;
  private final Path uploads;private final long userLimit,globalLimit,minimumFree;private final int pendingLimit,rateLimit;
  private final Set<UUID> active=java.util.concurrent.ConcurrentHashMap.newKeySet();
  public UploadStorage(JdbcTemplate db,TransactionTemplate tx,KnowledgeMapper documents,
      @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}") String directory,
      @Value("${UPLOAD_USER_BYTES:209715200}") long userLimit,@Value("${UPLOAD_GLOBAL_BYTES:2147483648}") long globalLimit,
      @Value("${UPLOAD_MIN_FREE_BYTES:536870912}") long minimumFree,@Value("${UPLOAD_PENDING_LIMIT:10}") int pendingLimit,
      @Value("${UPLOADS_PER_MINUTE:5}") int rateLimit) {
    this.db=db;this.tx=tx;this.documents=documents;this.uploads=Path.of(directory).toAbsolutePath().normalize();
    if(userLimit<1||globalLimit<userLimit||minimumFree<0||pendingLimit<1||rateLimit<1)throw new IllegalArgumentException("Invalid upload quota");
    this.userLimit=userLimit;this.globalLimit=globalLimit;this.minimumFree=minimumFree;this.pendingLimit=pendingLimit;this.rateLimit=rateLimit;
  }
  private void lock(){db.execute("SELECT pg_advisory_xact_lock("+LOCK+")");}
  private long scalar(String sql,Object...params){return Objects.requireNonNull(db.queryForObject(sql,Long.class,params));}
  private String sha(byte[] value) {
    try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));}
    catch(java.security.NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
  }
  private String sha(InputStream source,long expected)throws IOException {
    try(source){
      MessageDigest digest;
      try{digest=MessageDigest.getInstance("SHA-256");}catch(java.security.NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
      byte[] buffer=new byte[8192];int read;long total=0;
      while((read=source.read(buffer))!=-1){total+=read;if(total>expected||total>20L*1024*1024)throw new IOException("Upload size changed");digest.update(buffer,0,read);}
      if(total!=expected)throw new IOException("Upload size changed");return HexFormat.of().formatHex(digest.digest());
    }
  }
  private Path path(String name) {
    Path result=uploads.resolve(name).normalize();
    if(!name.matches("[0-9a-f-]{36}\\.(pdf|png|jpg|gif|webp)")||!result.startsWith(uploads)||Files.isSymbolicLink(result))throw new ApiException(409,"上传文件状态异常");
    return result;
  }
  public Map<String,Object> store(long uid,String city,String title,String original,String media,byte[] content) {
    return store(uid,city,title,original,media,content.length,sha(content),target->Files.write(target,content,StandardOpenOption.CREATE_NEW));
  }
  public Map<String,Object> store(long uid,String city,String title,String original,String media,MultipartFile file)throws IOException {
    long size=file.getSize();String hash=sha(file.getInputStream(),size);
    return store(uid,city,title,original,media,size,hash,target->{
      try(var source=file.getInputStream();var output=Files.newOutputStream(target,StandardOpenOption.CREATE_NEW)) {
        byte[] buffer=new byte[8192];int read;long total=0;
        MessageDigest digest;
        try{digest=MessageDigest.getInstance("SHA-256");}catch(java.security.NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
        while((read=source.read(buffer))!=-1){total+=read;if(total>size||total>20L*1024*1024)throw new IOException("Upload size changed");output.write(buffer,0,read);digest.update(buffer,0,read);}
        if(total!=size||!hash.equals(HexFormat.of().formatHex(digest.digest())))throw new IOException("Upload content changed");
      }
    });
  }
  @FunctionalInterface private interface FileWriter {void write(Path target)throws IOException;}
  private Map<String,Object> store(long uid,String city,String title,String original,String media,long size,String hash,FileWriter writer) {
    if(size<1||size>20L*1024*1024)throw new ApiException(413,"文件不能超过20 MB");
    String suffix=switch(media){case "application/pdf"->".pdf";case "image/png"->".png";case "image/jpeg"->".jpg";case "image/gif"->".gif";default->".webp";};
    UUID id=UUID.randomUUID();String name=id+suffix;
    try{Files.createDirectories(uploads);}catch(Exception ex){throw new ApiException(503,"上传存储暂不可用","UPLOAD_STORAGE_UNAVAILABLE");}
    active.add(id);
    Map<String,Object> duplicate;
    try { duplicate=tx.execute(s->{
      lock();var found=db.queryForList("SELECT id FROM knowledge_documents WHERE submitted_by=? AND sha256=? AND status<>'deleted' ORDER BY id LIMIT 1",uid,hash);
      db.update("DELETE FROM upload_reservations WHERE id IN (SELECT id FROM upload_reservations WHERE status IN ('committed','released') AND updated_at<timezone('UTC',now())-interval '7 days' LIMIT 100)");
      if(!found.isEmpty()) {
        var existing=documents.get(((Number)found.getFirst().get("id")).longValue());
        if(!city.equals(existing.get("city"))||!title.equals(existing.get("title")))
          throw new ApiException(409,"相同文件已投稿；请在我的投稿查看原资料，重复上传不会改变城市或标题","UPLOAD_DUPLICATE_METADATA");
        return existing;
      }
      if(scalar("SELECT count(*) FROM upload_reservations WHERE user_id=? AND sha256=? AND status IN ('reserved','stored','uncertain')",uid,hash)>0)
        throw new ApiException(409,"相同资料正在保存或等待核对，请勿重复提交","UPLOAD_RECONCILIATION_REQUIRED");
      if(scalar("SELECT count(*) FROM upload_reservations WHERE user_id=? AND created_at>timezone('UTC',now())-interval '1 minute'",uid)>=rateLimit)
        throw new ApiException(429,"资料上传过于频繁，请稍后重试","UPLOAD_RATE_LIMITED");
      long userBytes=scalar("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE submitted_by=? AND status<>'deleted'",uid)
          +scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE user_id=? AND status IN ('reserved','stored','uncertain')",uid);
      long globalBytes=scalar("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE status<>'deleted'")
          +scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE status IN ('reserved','stored','uncertain')");
      long pending=scalar("SELECT count(*) FROM knowledge_documents WHERE submitted_by=? AND status NOT IN ('published','deleted')",uid)
          +scalar("SELECT count(*) FROM upload_reservations WHERE user_id=? AND status IN ('reserved','stored','uncertain')",uid);
      if(userBytes>userLimit-size||pending>=pendingLimit)throw new ApiException(429,"上传容量或待审核资料数量已达上限","UPLOAD_USER_QUOTA");
      if(globalBytes>globalLimit-size)throw new ApiException(503,"上传存储额度已满，请稍后重试","UPLOAD_GLOBAL_QUOTA");
      try {
        long outstanding=scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE status IN ('reserved','stored','uncertain')");
        long free=Files.getFileStore(uploads).getUsableSpace();
        if(free<minimumFree||free-minimumFree<size||outstanding>free-minimumFree-size)
          throw new ApiException(503,"存储空间不足，已暂停资料上传","UPLOAD_LOW_DISK");
      }catch(java.io.IOException ex){throw new ApiException(503,"无法确认可用存储空间","UPLOAD_STORAGE_UNAVAILABLE");}
      db.update("INSERT INTO upload_reservations(id,user_id,sha256,size_bytes,stored_path,city,title,original_filename,media_type,status) VALUES(?,?,?,?,?,?,?,?,?,'reserved')",
          id,uid,hash,size,name,city,title,original,media);return null;
    }); }catch(RuntimeException ex){active.remove(id);throw ex;}
    if(duplicate!=null){active.remove(id);return duplicate;}
    try {
      return tx.execute(s->{lock();var reservation=db.queryForMap("SELECT * FROM upload_reservations WHERE id=? FOR UPDATE",id);
        if("committed".equals(reservation.get("status")))return documents.get(((Number)reservation.get("document_id")).longValue());
        if(!"reserved".equals(reservation.get("status")))throw new ApiException(409,"上传预留状态已变更");
        try{writer.write(path(name));}catch(java.io.IOException ex){throw new IllegalStateException("Upload filesystem outcome requires reconciliation",ex);}
        return finish(reservation);});
    } catch(Exception ex) {
      // A failed response may follow a successful commit. Never infer rollback or delete the file.
      try{db.update("UPDATE upload_reservations SET status='uncertain',updated_at=timezone('UTC',now()) WHERE id=? AND status<>'committed'",id);}catch(Exception ignored){}
      throw new ApiException(503,"资料保存状态待核对，请勿重复上传；管理员可执行存储核对","UPLOAD_RECONCILIATION_REQUIRED");
    }finally{active.remove(id);}
  }
  private Map<String,Object> finish(Map<String,Object> reservation) {
    var row=new HashMap<String,Object>();
    for(String field:List.of("city","title","original_filename","stored_path","sha256","media_type","size_bytes"))row.put(field,reservation.get(field));
    row.put("submitted_by",reservation.get("user_id"));documents.insert(row);
    db.update("UPDATE upload_reservations SET status='committed',document_id=?,updated_at=timezone('UTC',now()) WHERE id=?",row.get("id"),reservation.get("id"));
    return documents.get(((Number)row.get("id")).longValue());
  }
  public Object reconcile() {
    return tx.execute(s->{lock();int recovered=0,released=0,uncertain=0;
      for(var reservation:db.queryForList("SELECT * FROM upload_reservations WHERE status IN ('reserved','stored','uncertain') AND updated_at<timezone('UTC',now())-interval '10 minutes' ORDER BY created_at LIMIT 100 FOR UPDATE")) {
        if(active.contains((UUID)reservation.get("id")))continue;
        var linked=db.queryForList("SELECT id FROM knowledge_documents WHERE stored_path=? AND sha256=?",reservation.get("stored_path"),reservation.get("sha256"));
        if(!linked.isEmpty()){db.update("UPDATE upload_reservations SET status='committed',document_id=?,updated_at=timezone('UTC',now()) WHERE id=?",linked.getFirst().get("id"),reservation.get("id"));recovered++;continue;}
        Path target=path(reservation.get("stored_path").toString());
        if(!Files.exists(target,LinkOption.NOFOLLOW_LINKS)){db.update("UPDATE upload_reservations SET status='released',updated_at=timezone('UTC',now()) WHERE id=?",reservation.get("id"));released++;continue;}
        try {
          long expected=((Number)reservation.get("size_bytes")).longValue();
          if(!Files.isRegularFile(target,LinkOption.NOFOLLOW_LINKS)||expected>20L*1024*1024||Files.size(target)!=expected||!sha(Files.newInputStream(target),expected).equals(reservation.get("sha256")))throw new java.io.IOException();
          finish(reservation);recovered++;
        }catch(java.io.IOException ex){db.update("UPDATE upload_reservations SET status='uncertain',updated_at=timezone('UTC',now()) WHERE id=?",reservation.get("id"));uncertain++;}
      }
      return Map.of("success",true,"recovered",recovered,"released_missing",released,"uncertain_retained",uncertain);
    });
  }
  public Object status(long uid,boolean admin) {
    var result=new LinkedHashMap<String,Object>();
    result.put("used_bytes",scalar("SELECT COALESCE(sum(size_bytes),0) FROM knowledge_documents WHERE submitted_by=? AND status<>'deleted'",uid));
    result.put("reserved_bytes",scalar("SELECT COALESCE(sum(size_bytes),0) FROM upload_reservations WHERE user_id=? AND status IN ('reserved','stored','uncertain')",uid));
    result.put("user_limit_bytes",userLimit);result.put("pending_limit",pendingLimit);result.put("uploads_per_minute",rateLimit);
    if(admin){result.put("reservations",db.queryForList("SELECT id,user_id,size_bytes,status,document_id,created_at,updated_at FROM upload_reservations WHERE status IN ('reserved','stored','uncertain') ORDER BY created_at LIMIT 100"));result.put("minimum_free_bytes",minimumFree);result.put("global_limit_bytes",globalLimit);}
    return result;
  }
}
