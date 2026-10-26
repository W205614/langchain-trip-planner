package com.tripplanner.security;

import com.tripplanner.api.ApiException;
import com.tripplanner.domain.BusinessAuditService;
import com.tripplanner.persistence.UserMapper;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class AccountSecurityService {
  private final JdbcTemplate db; private final TransactionTemplate tx; private final UserMapper users;
  private final BusinessAuditService audit; private final String encryptionKey; private final boolean required;
  private final String dummy=Passwords.hash("timing-only-"+UUID.randomUUID());
  public AccountSecurityService(JdbcTemplate db,TransactionTemplate tx,UserMapper users,BusinessAuditService audit,
      @Value("${ACCOUNT_SECURITY_ENCRYPTION_KEY:}") String encryptionKey,
      @Value("${ADMIN_MFA_REQUIRED:false}") boolean required) {
    this.db=db;this.tx=tx;this.users=users;this.audit=audit;this.encryptionKey=encryptionKey;this.required=required;
  }
  public static void validatePassword(String value,String username) {
    if(value==null || value.length()<8 || value.length()>64 || value.getBytes(StandardCharsets.UTF_8).length>72
        || value.isBlank() || value.equalsIgnoreCase(username)
        || Set.of("password","12345678","123456789","qwertyui","password123").contains(value.toLowerCase(Locale.ROOT)))
      throw new ApiException(422,"密码须8至64字符、最多72个UTF-8字节，且不能使用常见弱密码或用户名");
  }
  public static String hash(String value) {
    try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
    catch(NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
  }
  private Map<String,Object> locked(long uid) {
    db.update("INSERT INTO account_security(user_id) VALUES(?) ON CONFLICT DO NOTHING",uid);
    return db.queryForMap("SELECT * FROM account_security WHERE user_id=? FOR UPDATE",uid);
  }
  public boolean adminReady(long uid) {
    return !required || Boolean.TRUE.equals(db.queryForObject("SELECT COALESCE((SELECT totp_enabled FROM account_security WHERE user_id=?),false)",Boolean.class,uid));
  }
  private String identity(String username){return hash(username.strip().toLowerCase(Locale.ROOT));}
  private void attempt(String key) {
    db.update("DELETE FROM account_login_attempts WHERE identity_hash IN (SELECT identity_hash FROM account_login_attempts WHERE updated_at < timezone('UTC',now()) - interval '7 days' LIMIT 100)");
    if(Boolean.FALSE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM account_login_attempts WHERE identity_hash=?)",Boolean.class,key))
        && db.queryForObject("SELECT count(*) FROM account_login_attempts",Long.class)>=10000)
      throw new ApiException(429,"认证请求容量已满，请稍后重试","ACCOUNT_RATE_LIMITED");
    db.update("INSERT INTO account_login_attempts(identity_hash) VALUES(?) ON CONFLICT DO NOTHING",key);
    var row=db.queryForMap("SELECT * FROM account_login_attempts WHERE identity_hash=? FOR UPDATE",key);
    // Schema timestamps are UTC without an offset; never interpret them in the host timezone.
    if(row.get("blocked_until") instanceof Timestamp blocked && blocked.toLocalDateTime().toInstant(ZoneOffset.UTC).isAfter(Instant.now()))
      throw new ApiException(429,"尝试过于频繁，请稍后重试","ACCOUNT_RATE_LIMITED");
  }
  private void failed(String key) {
    db.update("UPDATE account_login_attempts SET failures=CASE WHEN window_started_at < timezone('UTC',now()) - interval '15 minutes' THEN 1 ELSE LEAST(failures+1,20) END, "
        +"window_started_at=CASE WHEN window_started_at < timezone('UTC',now()) - interval '15 minutes' THEN timezone('UTC',now()) ELSE window_started_at END,updated_at=timezone('UTC',now()) WHERE identity_hash=?",key);
    db.update("UPDATE account_login_attempts SET blocked_until=timezone('UTC',now()) + (LEAST(300,30*GREATEST(1,failures-4))*interval '1 second') WHERE identity_hash=? AND failures>=5",key);
  }
  public Map<String,Object> authenticate(String username,String password,String code) {
    String key=identity(username);
    var user=tx.execute(s->{
      attempt(key);var found=users.byName(username);
      boolean passwordOk=Passwords.verify(password,found==null?dummy:found.get("hashed_password").toString());
      if(found==null || !passwordOk){failed(key);return null;}
      long uid=((Number)found.get("id")).longValue();var security=locked(uid);
      if(Boolean.TRUE.equals(security.get("totp_enabled")) && !consumeTotp(uid,security,code)){failed(key);return null;}
      db.update("DELETE FROM account_login_attempts WHERE identity_hash=?",key);return found;
    });
    if(user==null)throw new ApiException(401,"账号、密码或二次验证码错误","INVALID_CREDENTIALS");return user;
  }
  private boolean consumeTotp(long uid,Map<String,Object> security,String code) {
    long step=Totp.verify(Totp.decrypt(security.get("totp_secret").toString(),encryptionKey),code,Instant.now().getEpochSecond(),((Number)security.get("last_totp_step")).longValue());
    if(step<0)return false;
    db.update("UPDATE account_security SET last_totp_step=?,updated_at=timezone('UTC',now()) WHERE user_id=?",step,uid);return true;
  }
  private void verifyCurrent(long uid,Map<String,Object> security,String password,String code) {
    var user=users.byId(uid);
    if(!Passwords.verify(password,user.get("hashed_password").toString())
        || Boolean.TRUE.equals(security.get("totp_enabled"))&&!consumeTotp(uid,security,code))
      throw new ApiException(401,"当前密码或二次验证码错误");
  }
  public Object status(long uid) {
    var rows=db.queryForList("SELECT totp_enabled,recovery_created_at FROM account_security WHERE user_id=?",uid);
    boolean configured;
    try{Totp.key(encryptionKey);configured=true;}catch(ApiException ex){configured=false;}
    var result=new LinkedHashMap<String,Object>();result.put("mfa_enabled",!rows.isEmpty()&&Boolean.TRUE.equals(rows.getFirst().get("totp_enabled")));
    result.put("recovery_configured",!rows.isEmpty()&&rows.getFirst().get("recovery_created_at")!=null);
    result.put("mfa_available",configured);result.put("admin_mfa_required",required);
    result.put("external_recovery_status","blocked_external");return result;
  }
  public Object changePassword(long uid,String current,String next,String code,String requestId) {
    validatePassword(next,users.byId(uid).get("username").toString());
    return tx.execute(s->{var security=locked(uid);verifyCurrent(uid,security,current,code);
      db.update("UPDATE users SET hashed_password=?,token_version=token_version+1 WHERE id=?",Passwords.hash(next),uid);
      audit.success(uid,"account.password_changed","user",uid,null,requestId);
      return Map.of("success",true,"reauthenticate",true);});
  }
  public Object recoveryCode(long uid,String password,String code,String requestId) {
    return tx.execute(s->{var security=locked(uid);verifyCurrent(uid,security,password,code);
      byte[] value=new byte[32];new SecureRandom().nextBytes(value);String recovery=Base64.getUrlEncoder().withoutPadding().encodeToString(value);
      db.update("UPDATE account_security SET recovery_hash=?,recovery_created_at=timezone('UTC',now()),updated_at=timezone('UTC',now()) WHERE user_id=?",hash(recovery),uid);
      audit.success(uid,"account.recovery_rotated","user",uid,null,requestId);
      return Map.of("success",true,"recovery_code",recovery,"message","仅显示一次；请离线保存，重新生成后旧码失效");});
  }
  public Object recover(String username,String recovery,String next,String requestId) {
    validatePassword(next,username);String key=identity(username);
    Boolean ok=tx.execute(s->{attempt(key);var user=users.byName(username);
      if(user==null){failed(key);return false;}long uid=((Number)user.get("id")).longValue();var security=locked(uid);
      if(recovery==null || recovery.length()>128 || security.get("recovery_hash")==null || !MessageDigest.isEqual(hash(recovery).getBytes(StandardCharsets.US_ASCII),security.get("recovery_hash").toString().getBytes(StandardCharsets.US_ASCII))){failed(key);return false;}
      db.update("UPDATE users SET hashed_password=?,token_version=token_version+1 WHERE id=?",Passwords.hash(next),uid);
      db.update("UPDATE account_security SET recovery_hash=NULL,recovery_created_at=NULL,totp_secret=NULL,pending_totp_secret=NULL,totp_enabled=false,last_totp_step=-1,updated_at=timezone('UTC',now()) WHERE user_id=?",uid);
      db.update("DELETE FROM account_login_attempts WHERE identity_hash=?",key);
      audit.success(uid,"account.recovered","user",uid,null,requestId);return true;});
    if(!Boolean.TRUE.equals(ok))throw new ApiException(401,"账号或恢复码错误");
    return Map.of("success",true,"message","密码已重设、旧会话与恢复码已失效，请重新登录并设置二次验证");
  }
  public Object enroll(long uid,String password,String code,String requestId) {
    return tx.execute(s->{var security=locked(uid);verifyCurrent(uid,security,password,code);
      String secret=Totp.newSecret();String encrypted=Totp.encrypt(secret,encryptionKey);
      db.update("UPDATE account_security SET pending_totp_secret=?,pending_expires_at=timezone('UTC',now())+interval '10 minutes' WHERE user_id=?",encrypted,uid);
      audit.success(uid,"account.mfa_enrollment_started","user",uid,null,requestId);
      String name=java.net.URLEncoder.encode(users.byId(uid).get("username").toString(),StandardCharsets.UTF_8).replace("+","%20");
      return Map.of("secret",secret,"otpauth_uri","otpauth://totp/TripPlanner:"+name+"?secret="+secret+"&issuer=TripPlanner&algorithm=SHA1&digits=6&period=30");});
  }
  public Object confirm(long uid,String code,String requestId) {
    return tx.execute(s->{var security=locked(uid);
      if(!(security.get("pending_expires_at") instanceof Timestamp expires)||expires.toLocalDateTime().toInstant(ZoneOffset.UTC).isBefore(Instant.now())||security.get("pending_totp_secret")==null)
        throw new ApiException(409,"二次验证设置已过期，请重新开始");
      String secret=Totp.decrypt(security.get("pending_totp_secret").toString(),encryptionKey);
      long step=Totp.verify(secret,code,Instant.now().getEpochSecond(),-1);
      if(step<0)throw new ApiException(422,"二次验证码错误");
      db.update("UPDATE account_security SET totp_secret=pending_totp_secret,pending_totp_secret=NULL,pending_expires_at=NULL,totp_enabled=true,last_totp_step=?,updated_at=timezone('UTC',now()) WHERE user_id=?",step,uid);
      users.revoke(uid);audit.success(uid,"account.mfa_enabled","user",uid,null,requestId);
      return Map.of("success",true,"reauthenticate",true);});
  }
  public Object disable(long uid,String password,String code,String requestId) {
    return tx.execute(s->{var security=locked(uid);verifyCurrent(uid,security,password,code);
      if(required&&Boolean.TRUE.equals(users.byId(uid).get("is_admin")))throw new ApiException(409,"当前部署要求管理员启用二次验证");
      db.update("UPDATE account_security SET totp_secret=NULL,totp_enabled=false,pending_totp_secret=NULL,pending_expires_at=NULL,last_totp_step=-1 WHERE user_id=?",uid);
      users.revoke(uid);audit.success(uid,"account.mfa_disabled","user",uid,null,requestId);return Map.of("success",true,"reauthenticate",true);});
  }
}
