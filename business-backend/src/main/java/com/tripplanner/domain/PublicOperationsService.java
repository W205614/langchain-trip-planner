package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.security.AccountAccess;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class PublicOperationsService {
  static final long ADMIN_LOCK=842491570099L;
  private final JdbcTemplate db;
  private final TransactionTemplate tx;
  private final BusinessAuditService audit;
  private final UserQuotaService quotas;
  private final AccountAccess access;
  private final boolean adminMfa;
  public PublicOperationsService(JdbcTemplate db,TransactionTemplate tx,BusinessAuditService audit,
      UserQuotaService quotas,AccountAccess access,@Value("${ADMIN_MFA_REQUIRED:false}") boolean adminMfa) {
    this.db=db;this.tx=tx;this.audit=audit;this.quotas=quotas;this.access=access;this.adminMfa=adminMfa;
  }
  public Object users(int page,String query) {
    if(page<1||page>100000||query.length()>64)throw new ApiException(422,"分页或搜索参数无效");
    return Map.of("data",db.queryForList("SELECT id,username,email,email_verified_at,account_state,is_admin,created_at FROM users WHERE position(lower(?) in lower(username))>0 ORDER BY id LIMIT 50 OFFSET ?",query,(page-1)*50),
        "page",page,"total",db.queryForObject("SELECT count(*) FROM users WHERE position(lower(?) in lower(username))>0",Long.class,query));
  }
  void protectLastAdmin(long uid) {
    db.execute("SELECT pg_advisory_xact_lock("+ADMIN_LOCK+")");
    var rows=db.queryForList("SELECT is_admin,account_state FROM users WHERE id=? FOR UPDATE",uid);
    if(rows.isEmpty())throw new ApiException(404,"用户不存在");
    if(Boolean.TRUE.equals(rows.getFirst().get("is_admin"))&&"active".equals(rows.getFirst().get("account_state"))) {
      long active=db.queryForObject("SELECT count(*) FROM users WHERE is_admin AND account_state='active'",Long.class);
      if(active<=1)throw new ApiException(409,"不能停用或注销最后一个管理员","LAST_ADMIN_REQUIRED");
      long usable=db.queryForObject("SELECT count(*) FROM users u WHERE u.is_admin AND u.account_state='active' AND (NOT ? OR EXISTS(SELECT 1 FROM account_security s WHERE s.user_id=u.id AND s.totp_enabled))",Long.class,adminMfa);
      boolean targetUsable=!adminMfa||Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM account_security WHERE user_id=? AND totp_enabled)",Boolean.class,uid));
      if(targetUsable&&usable<=1)throw new ApiException(409,"不能停用或注销最后一个可用管理员","LAST_ADMIN_REQUIRED");
    }
  }
  public Object state(long actor,long uid,String state,String reason,String requestId) {
    if(!Set.of("active","disabled").contains(state)||reason.isBlank()||reason.length()>300)throw new ApiException(422,"请填写账号状态及处理原因");
    tx.executeWithoutResult(s->{
      db.execute("SELECT pg_advisory_xact_lock("+ADMIN_LOCK+")");
      var rows=db.queryForList("SELECT account_state FROM users WHERE id=? FOR UPDATE",uid);
      if(rows.isEmpty())throw new ApiException(404,"用户不存在");
      var current=rows.getFirst().get("account_state");
      if(!Set.of("active","disabled").contains(current))throw new ApiException(409,"正在注销或已注销的账号不能恢复");
      if(state.equals(current))return;
      if("disabled".equals(state))protectLastAdmin(uid);
      db.update("UPDATE users SET account_state=?,token_version=token_version+1 WHERE id=?",state,uid);
      audit.success(actor,"admin.account_"+state,"user",uid,null,requestId,Map.of("reason",reason));
    });
    if("disabled".equals(state))db.update("UPDATE trip_tasks SET status='cancelled',stage='cancelled',error_code='ACCOUNT_NOT_ACTIVE',message='账号已停用，任务停止' WHERE user_id=? AND status IN ('queued','running')",uid);
    return Map.of("success",true,"account_state",state);
  }
  public Object updateQuotas(long actor,long uid,Map<String,Integer> values,String requestId) {
    return tx.execute(s->{
      if(!Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM users WHERE id=? AND account_state IN ('active','disabled'))",Boolean.class,uid)))throw new ApiException(404,"用户不存在或已注销");
      Object result=quotas.update(uid,values);
      audit.success(actor,"admin.quota_updated","user",uid,null,requestId);return result;
    });
  }
  public Object createFeedback(long uid,String category,String subject,String message,Long trip,String task,String request) {
    access.requireActive(uid);
    if(!Set.of("planning","account","knowledge","other").contains(category)||subject.isBlank()||subject.length()>160||message.isBlank()||message.length()>3000)
      throw new ApiException(422,"反馈类别、主题或内容无效");
    if(trip!=null&&!Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM trip_records WHERE id=? AND user_id=?)",Boolean.class,trip,uid)))throw new ApiException(404,"本人行程不存在");
    if(task!=null&&!Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM trip_tasks WHERE id=? AND user_id=?)",Boolean.class,task,uid)))throw new ApiException(404,"本人任务不存在");
    long count=db.queryForObject("SELECT count(*) FROM user_feedback WHERE user_id=? AND created_at>timezone('UTC',now())-interval '1 hour'",Long.class,uid);
    if(count>=10)throw new ApiException(429,"反馈提交过于频繁，请稍后重试","FEEDBACK_RATE_LIMITED");
    long id=db.queryForObject("INSERT INTO user_feedback(user_id,category,subject,message,trip_id,task_id,request_id) VALUES(?,?,?,?,?,?,?) RETURNING id",Long.class,
        uid,category,subject.strip(),message.strip(),trip,task,BusinessAuditService.safeRequestId(request));
    return Map.of("success",true,"id",id);
  }
  public Object feedback(long uid,int page,boolean admin,String status) {
    if(page<1||page>100000||!Set.of("","open","processing","closed").contains(status))throw new ApiException(422,"反馈筛选无效");
    int offset=Math.multiplyExact(Math.subtractExact(page,1),50);
    String condition=admin?"(?='' OR status=?)":"user_id=?";
    Object[] args=admin?new Object[]{status,status,offset}:new Object[]{uid,offset};
    return Map.of("data",db.queryForList("SELECT id,user_id,category,subject,message,trip_id,task_id,request_id,status,reply,version,created_at,updated_at FROM user_feedback WHERE "+condition+" ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET ?",args),"page",page);
  }
  public Object respond(long actor,long id,int version,String status,String reply,String requestId) {
    if(version<1||!Set.of("open","processing","closed").contains(status)||reply.length()>3000||status.equals("closed")&&reply.isBlank())throw new ApiException(422,"反馈状态、版本或回复无效");
    int nextVersion=TripRequests.nextVersion(version);
    return tx.execute(s->{
      if(db.update("UPDATE user_feedback SET status=?,reply=?,replied_by=?,version=?,updated_at=timezone('UTC',now()) WHERE id=? AND version=?",status,reply.strip(),actor,nextVersion,id,version)!=1)
        throw new ApiException(409,"反馈已被其他管理员更新，请刷新","VERSION_CONFLICT");
      audit.success(actor,"feedback.responded","feedback",id,nextVersion,requestId);
      return Map.of("success",true,"version",nextVersion);
    });
  }
}
