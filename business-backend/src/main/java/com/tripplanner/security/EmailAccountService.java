package com.tripplanner.security;

import com.tripplanner.api.ApiException;
import com.tripplanner.domain.BusinessAuditService;
import jakarta.annotation.PostConstruct;
import jakarta.mail.internet.InternetAddress;
import java.net.URI;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class EmailAccountService {
  private final JdbcTemplate db;
  private final TransactionTemplate tx;
  private final AccountAccess access;
  private final AccountSecurityService security;
  private final MailDeliveryService mail;
  private final BusinessAuditService audit;
  private final String publicBase, environment;

  public EmailAccountService(JdbcTemplate db, TransactionTemplate tx, AccountAccess access,
      AccountSecurityService security, MailDeliveryService mail, BusinessAuditService audit,
      @Value("${PUBLIC_BASE_URL:http://localhost:8380}") String publicBase,
      @Value("${APP_ENV:development}") String environment) {
    this.db = db; this.tx = tx; this.access = access; this.security = security;
    this.mail = mail; this.audit = audit; this.environment = environment;
    this.publicBase = publicBase.replaceAll("/+$", "");
  }

  @PostConstruct void validate() {
    URI uri = URI.create(publicBase);
    if (!Set.of("http", "https").contains(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
        || uri.getQuery() != null || uri.getFragment() != null || !uri.getPath().isEmpty())
      throw new IllegalArgumentException("PUBLIC_BASE_URL must be an HTTP(S) origin");
    if (access.verificationRequired() && (!mail.available() || environment.equals("production") && !uri.getScheme().equals("https")))
      throw new IllegalArgumentException("Public email registration requires SMTP and a production HTTPS PUBLIC_BASE_URL");
  }

  public Object options() {
    return Map.of("registration_mode", access.verificationRequired() ? "email_verified" : "legacy", "email_available", mail.available());
  }

  public Object status(long userId) {
    var user = access.requireActive(userId);
    var result = new LinkedHashMap<String, Object>();
    result.put("email", user.get("email")); result.put("verified", user.get("email_verified_at") != null);
    result.put("verification_required", access.verificationRequired()); result.put("available", mail.available());
    var pending = db.queryForList("SELECT target_email FROM account_email_tokens WHERE user_id=? AND purpose='verify' AND consumed_at IS NULL AND expires_at>timezone('UTC',now()) ORDER BY created_at DESC LIMIT 1", userId);
    result.put("pending_email", pending.isEmpty() ? null : pending.getFirst().get("target_email"));
    return result;
  }

  public void registered(long userId, String email) {
    if ((email == null || email.isBlank()) && !access.verificationRequired()) return;
    issueVerification(userId, normalize(email));
  }

  public Object bind(long userId, String email, String password, String code, String requestId) {
    String normalized = normalize(email);
    return tx.execute(s -> {
      access.requireActive(userId); security.verifyIdentity(userId, password, code);
      issueVerification(userId, normalized);
      audit.success(userId, "account.email_verification_requested", "user", userId, null, requestId);
      return Map.of("success", true, "message", "验证邮件已排队；新邮箱验证后才会替换原邮箱");
    });
  }

  public Object resend(long userId) {
    var user = access.requireActive(userId);
    var rows = db.queryForList("SELECT target_email FROM account_email_tokens WHERE user_id=? AND purpose='verify' ORDER BY created_at DESC LIMIT 1", userId);
    String email = rows.isEmpty() ? Objects.toString(user.get("email"), "") : rows.getFirst().get("target_email").toString();
    if (email.isBlank()) throw new ApiException(409, "请先绑定邮箱");
    issueVerification(userId, email);
    return Map.of("success", true, "message", "验证邮件已排队，请使用最新邮件中的链接");
  }

  private void issueVerification(long userId, String email) {
    requireMail();
    tx.executeWithoutResult(s -> {
      var user = db.queryForMap("SELECT * FROM users WHERE id=? FOR UPDATE", userId);
      if (!"active".equals(user.get("account_state"))) throw new ApiException(403, "账号当前不可用", "ACCOUNT_INACTIVE");
      if (email.equals(user.get("email")) && user.get("email_verified_at") != null)
        throw new ApiException(409, "邮箱已经验证");
      if (db.queryForObject("SELECT count(*) FROM users WHERE lower(email)=? AND id<>? AND account_state<>'deleted'", Long.class, email, userId) > 0)
        throw new ApiException(409, "该邮箱已绑定其他账号");
      rate(userId, "verify");
      issue(userId, "verify", email, 1800);
    });
  }

  private void rate(long userId, String purpose) {
    if (db.queryForObject("SELECT count(*) FROM account_email_tokens WHERE user_id=? AND purpose=? AND created_at>timezone('UTC',now())-interval '1 minute'", Long.class, userId, purpose) > 0
        || db.queryForObject("SELECT count(*) FROM account_email_tokens WHERE user_id=? AND purpose=? AND created_at>timezone('UTC',now())-interval '1 hour'", Long.class, userId, purpose) >= 5)
      throw new ApiException(429, "邮件请求过于频繁，请稍后重试", "EMAIL_RATE_LIMITED");
  }

  private void issue(long userId, String purpose, String email, int seconds) {
    byte[] bytes = new byte[32]; new SecureRandom().nextBytes(bytes);
    String token = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    String hash = AccountSecurityService.hash(token);
    Instant expires = Instant.now().plusSeconds(seconds);
    db.update("UPDATE mail_deliveries SET status='cancelled',encrypted_payload=NULL,updated_at=timezone('UTC',now()) WHERE status IN ('pending','retry') AND event_key IN (SELECT 'account:'||token_hash FROM account_email_tokens WHERE user_id=? AND purpose=? AND consumed_at IS NULL)", userId, purpose);
    db.update("UPDATE account_email_tokens SET consumed_at=timezone('UTC',now()) WHERE user_id=? AND purpose=? AND consumed_at IS NULL", userId, purpose);
    db.update("DELETE FROM account_email_tokens WHERE token_hash IN (SELECT token_hash FROM account_email_tokens WHERE expires_at<timezone('UTC',now())-interval '7 days' LIMIT 100)");
    db.update("INSERT INTO account_email_tokens(token_hash,user_id,purpose,target_email,expires_at) VALUES(?,?,?,?,?)", hash, userId, purpose, email, java.time.LocalDateTime.ofInstant(expires, java.time.ZoneOffset.UTC));
    // Fragments are never sent in HTTP requests, keeping capabilities out of proxy logs.
    String link = publicBase + "/account/email#mode=" + purpose + "&token=" + token;
    mail.enqueue(userId, "account:" + hash, purpose.equals("verify") ? "email_verification" : "password_reset", email,
        purpose.equals("verify") ? "旅行助手：验证邮箱" : "旅行助手：重设密码",
        (purpose.equals("verify") ? "请打开以下链接验证邮箱，30分钟内有效：\n" : "请打开以下链接重设密码，15分钟内有效；已开启二次验证时仍需验证码：\n")
            + link + "\n如果不是你发起的请求，请忽略此邮件。", expires);
  }

  public Object confirm(String token, String requestId) {
    try {
      return tx.execute(s -> {
        var row = token(token, "verify"); long userId = ((Number)row.get("user_id")).longValue();
        access.requireActive(userId);
        db.update("UPDATE users SET email=?,email_verified_at=timezone('UTC',now()) WHERE id=? AND account_state='active'", row.get("target_email"), userId);
        db.update("UPDATE mail_deliveries SET status='cancelled',encrypted_payload=NULL,updated_at=timezone('UTC',now()) WHERE user_id=? AND kind='notification' AND status IN ('pending','retry')", userId);
        consumed(row);
        audit.success(userId, "account.email_verified", "user", userId, null, requestId);
        return Map.of("success", true, "message", "邮箱已验证");
      });
    } catch (DuplicateKeyException ex) { throw new ApiException(409, "该邮箱已绑定其他账号"); }
  }

  public Object requestReset(String email) {
    requireMail(); String normalized = normalize(email);
    tx.executeWithoutResult(s -> {
      var rows = db.queryForList("SELECT id,email FROM users WHERE lower(email)=? AND email_verified_at IS NOT NULL AND account_state='active' FOR UPDATE", normalized);
      if (rows.isEmpty()) return;
      long userId = ((Number)rows.getFirst().get("id")).longValue();
      try { rate(userId, "reset"); }
      catch (ApiException limited) { if (limited.status == 429) return; throw limited; }
      issue(userId, "reset", rows.getFirst().get("email").toString(), 900);
    });
    return Map.of("success", true, "message", "如果该邮箱已验证且账号可用，重置邮件将发送至该邮箱");
  }

  public Object reset(String token, String password, String code, String requestId) {
    return tx.execute(s -> {
      var row = token(token, "reset"); long userId = ((Number)row.get("user_id")).longValue();
      var user = access.requireActive(userId);
      if (!row.get("target_email").equals(user.get("email")) || user.get("email_verified_at") == null)
        throw new ApiException(409, "邮箱已变化，请重新申请重置");
      security.resetWithEmail(userId, password, code, requestId);
      consumed(row);
      return Map.of("success", true, "reauthenticate", true, "message", "密码已重设，请重新登录；二次验证设置保持有效");
    });
  }

  private Map<String, Object> token(String value, String purpose) {
    if (value == null || !value.matches("[A-Za-z0-9_-]{43}")) throw invalidToken();
    var rows = db.queryForList("SELECT * FROM account_email_tokens WHERE token_hash=? AND purpose=? AND consumed_at IS NULL AND expires_at>timezone('UTC',now()) FOR UPDATE", AccountSecurityService.hash(value), purpose);
    if (rows.isEmpty()) throw invalidToken();
    return rows.getFirst();
  }

  private ApiException invalidToken() { return new ApiException(409, "链接已使用或过期，请重新申请", "EMAIL_TOKEN_INVALID"); }
  private void consumed(Map<String, Object> row) { db.update("UPDATE account_email_tokens SET consumed_at=timezone('UTC',now()) WHERE token_hash=?", row.get("token_hash")); }
  private void requireMail() { if (!mail.available()) throw new ApiException(503, "邮件服务尚未配置", "MAIL_NOT_CONFIGURED"); }

  public Object preferences(long userId) {
    access.requireActive(userId);
    boolean enabled = Boolean.TRUE.equals(db.queryForObject("SELECT COALESCE((SELECT email_enabled FROM notification_preferences WHERE user_id=?),false)", Boolean.class, userId));
    return Map.of("email_enabled", enabled, "email_available", mail.available());
  }

  public Object preferences(long userId, boolean enabled) {
    var user = access.requireActive(userId);
    if (enabled) {
      requireMail();
      if (user.get("email_verified_at") == null) throw new ApiException(409, "请先验证邮箱");
    }
    tx.executeWithoutResult(s -> {
      db.update("INSERT INTO notification_preferences(user_id,email_enabled) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET email_enabled=excluded.email_enabled,updated_at=timezone('UTC',now())", userId, enabled);
      if (!enabled) db.update("UPDATE mail_deliveries SET status='cancelled',encrypted_payload=NULL,updated_at=timezone('UTC',now()) WHERE user_id=? AND kind='notification' AND status IN ('pending','retry')", userId);
    });
    return preferences(userId);
  }

  private static String normalize(String email) {
    if (email == null || email.isBlank() || email.length() > 254) throw new ApiException(422, "请填写有效邮箱");
    String value = email.strip().toLowerCase(Locale.ROOT);
    try {
      var parsed = new InternetAddress(value, true); parsed.validate();
      if (!parsed.getAddress().equals(value) || !value.contains("@") || value.contains("\r") || value.contains("\n")) throw new IllegalArgumentException();
      return value;
    } catch (Exception ex) { throw new ApiException(422, "请填写有效邮箱"); }
  }
}
