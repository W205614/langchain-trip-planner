package com.tripplanner.security;

import com.tripplanner.api.ApiException;
import com.tripplanner.domain.BusinessAuditService;
import com.tripplanner.domain.SingleInstance;
import jakarta.annotation.PostConstruct;
import jakarta.mail.MessagingException;
import jakarta.mail.internet.InternetAddress;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.*;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.eclipse.angus.mail.smtp.SMTPAddressFailedException;
import org.eclipse.angus.mail.smtp.SMTPSendFailedException;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mail.*;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.MimeMessageHelper;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/** A durable outbox acknowledges SMTP acceptance, never recipient inbox delivery. */
@Service
public class MailDeliveryService {
  private static final byte[] AAD = "trip-mail-outbox-v1".getBytes(StandardCharsets.US_ASCII);
  private final JdbcTemplate db;
  private final TransactionTemplate tx;
  private final JsonMapper json;
  private final ObjectProvider<JavaMailSender> sender;
  private final SingleInstance owner;
  private final BusinessAuditService audit;
  private final String encryptionKey, from, publicBase;

  public MailDeliveryService(JdbcTemplate db, TransactionTemplate tx, JsonMapper json,
      ObjectProvider<JavaMailSender> sender, SingleInstance owner, BusinessAuditService audit,
      @Value("${ACCOUNT_SECURITY_ENCRYPTION_KEY:}") String encryptionKey,
      @Value("${SMTP_FROM:no-reply@localhost}") String from,
      @Value("${PUBLIC_BASE_URL:http://localhost:8380}") String publicBase) {
    this.db = db; this.tx = tx; this.json = json; this.sender = sender; this.owner = owner;
    this.audit = audit; this.encryptionKey = encryptionKey; this.from = from;
    this.publicBase = publicBase.replaceAll("/+$", "");
  }

  @PostConstruct void validate() throws Exception {
    if (!available()) return;
    Totp.key(encryptionKey);
    new InternetAddress(from, true).validate();
  }

  public boolean available() { return sender.getIfAvailable() != null; }

  public void enqueue(long userId, String event, String kind, String recipient,
      String subject, String text, Instant expires) {
    if (!available()) throw new ApiException(503, "邮件服务尚未配置", "MAIL_NOT_CONFIGURED");
    String payload = encrypt(json.writeValueAsString(Map.of("recipient", recipient, "subject", subject, "text", text)));
    db.update("INSERT INTO mail_deliveries(user_id,event_key,kind,encrypted_payload,expires_at) VALUES(?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING",
        userId, event, kind, payload, java.time.LocalDateTime.ofInstant(expires, java.time.ZoneOffset.UTC));
  }

  String encrypt(String text) {
    try {
      byte[] nonce = new byte[12]; new SecureRandom().nextBytes(nonce);
      Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
      cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(Totp.key(encryptionKey), "AES"), new GCMParameterSpec(128, nonce));
      cipher.updateAAD(AAD);
      byte[] value = cipher.doFinal(text.getBytes(StandardCharsets.UTF_8));
      return Base64.getEncoder().encodeToString(ByteBuffer.allocate(Math.addExact(nonce.length, value.length)).put(nonce).put(value).array());
    } catch (java.security.GeneralSecurityException ex) { throw new IllegalStateException("Mail payload encryption failed", ex); }
  }

  String decrypt(String stored) {
    try {
      byte[] value = Base64.getDecoder().decode(stored);
      if (value.length < 29) throw new IllegalArgumentException("Invalid encrypted mail payload");
      Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
      cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(Totp.key(encryptionKey), "AES"), new GCMParameterSpec(128, Arrays.copyOf(value, 12)));
      cipher.updateAAD(AAD);
      return new String(cipher.doFinal(Arrays.copyOfRange(value, 12, value.length)), StandardCharsets.UTF_8);
    } catch (java.security.GeneralSecurityException | IllegalArgumentException ex) {
      throw new IllegalStateException("Mail payload cannot be decrypted", ex);
    }
  }

  @Scheduled(fixedDelayString = "${MAIL_DELIVERY_SCAN_MILLIS:2000}")
  public void scheduled() {
    if (!available() || !owner.owned()) return;
    bridgeNotifications();
    deliverOne();
  }

  public void bridgeNotifications() {
    if (!available()) return;
    tx.executeWithoutResult(s -> {
      for (var row : db.queryForList("SELECT n.id,n.user_id,u.email FROM trip_notifications n JOIN users u ON u.id=n.user_id "
          + "JOIN notification_preferences p ON p.user_id=u.id AND p.email_enabled "
          + "WHERE u.account_state='active' AND u.email_verified_at IS NOT NULL AND n.created_at>=p.updated_at AND n.created_at>timezone('UTC',now())-interval '7 days' "
          + "AND NOT EXISTS(SELECT 1 FROM mail_deliveries m WHERE m.event_key='notification:'||n.id::text) ORDER BY n.id LIMIT 100")) {
        enqueue(((Number)row.get("user_id")).longValue(), "notification:" + row.get("id"), "notification", row.get("email").toString(),
            "旅行助手：你有新的站内通知", "你有新的旅行通知。请登录查看：" + publicBase + "/inbox\n可在账号安全页关闭邮件提醒。", Instant.now().plusSeconds(86400));
      }
    });
  }

  public boolean deliverOne() {
    if (!available()) return false;
    var job = tx.execute(s -> {
      db.update("UPDATE mail_deliveries SET status=CASE WHEN attempts>=5 THEN 'failed' ELSE 'retry' END,lease_until=NULL,last_error='SMTP_ACK_UNKNOWN',updated_at=timezone('UTC',now()) WHERE status='sending' AND lease_until<timezone('UTC',now())");
      db.update("UPDATE mail_deliveries SET status='cancelled',encrypted_payload=NULL,last_error='EXPIRED',updated_at=timezone('UTC',now()) WHERE status IN ('pending','retry','failed') AND expires_at<=timezone('UTC',now())");
      var rows = db.queryForList("SELECT m.* FROM mail_deliveries m JOIN users u ON u.id=m.user_id AND u.account_state='active' "
          + "WHERE (m.kind<>'notification' OR EXISTS(SELECT 1 FROM notification_preferences p WHERE p.user_id=m.user_id AND p.email_enabled)) AND m.status IN ('pending','retry') AND m.attempts<5 AND m.next_attempt_at<=timezone('UTC',now()) "
          + "AND m.expires_at>timezone('UTC',now()) ORDER BY m.id LIMIT 1 FOR UPDATE OF m SKIP LOCKED");
      if (rows.isEmpty()) return null;
      var row = rows.getFirst();
      db.update("UPDATE mail_deliveries SET status='sending',attempts=attempts+1,lease_until=timezone('UTC',now())+interval '2 minutes',updated_at=timezone('UTC',now()) WHERE id=?", row.get("id"));
      return row;
    });
    if (job == null) return false;
    long id = ((Number)job.get("id")).longValue();
    int attempt = ((Number)job.get("attempts")).intValue() + 1;
    try {
      var payload = json.readTree(decrypt(job.get("encrypted_payload").toString()));
      var mailer = sender.getObject();
      var message = mailer.createMimeMessage();
      var helper = new MimeMessageHelper(message, false, "UTF-8");
      helper.setFrom(from); helper.setTo(payload.path("recipient").asText());
      helper.setSubject(payload.path("subject").asText()); helper.setText(payload.path("text").asText(), false);
      message.saveChanges();
      message.setHeader("Message-ID", "<trip-delivery-" + id + "@" + new InternetAddress(from).getAddress().split("@")[1] + ">");
      mailer.send(message);
      db.update("UPDATE mail_deliveries SET status='accepted',encrypted_payload=NULL,accepted_at=timezone('UTC',now()),lease_until=NULL,last_error='',updated_at=timezone('UTC',now()) WHERE id=? AND status='sending'", id);
    } catch (Exception ex) {
      boolean permanent = permanent(ex);
      int delay = Math.min(900, 30 * (1 << (attempt - 1)));
      db.update("UPDATE mail_deliveries SET status=?,lease_until=NULL,last_error=?,next_attempt_at=timezone('UTC',now())+(?*interval '1 second'),updated_at=timezone('UTC',now()) WHERE id=? AND status='sending'",
          permanent || attempt >= 5 ? "failed" : "retry", permanent ? "SMTP_PERMANENT_FAILURE" : "SMTP_SEND_OR_ACK_FAILED", delay, id);
    }
    return true;
  }

  static boolean permanent(Throwable error) {
    if (error instanceof MailAuthenticationException || error instanceof MailParseException || error instanceof IllegalArgumentException || error instanceof IllegalStateException) return true;
    if (error instanceof SMTPAddressFailedException smtp) return smtp.getReturnCode() >= 500;
    if (error instanceof SMTPSendFailedException smtp) return smtp.getReturnCode() >= 500;
    if (error instanceof MailSendException sent)
      for (Exception cause : sent.getMessageExceptions()) if (permanent(cause)) return true;
    if (error instanceof MessagingException message && message.getNextException() != null && message.getNextException() != error)
      return permanent(message.getNextException());
    return error.getCause() != null && error.getCause() != error && permanent(error.getCause());
  }

  public Object list(String status) {
    if (!status.isBlank() && !Set.of("pending", "sending", "retry", "accepted", "failed", "cancelled").contains(status))
      throw new ApiException(422, "投递状态无效");
    return Map.of("available", available(), "acceptance_note", "accepted仅表示SMTP接受，不证明已到达收件箱；响应丢失后重试可能重复投递",
        "data", db.queryForList("SELECT id,user_id,kind,status,attempts,next_attempt_at,expires_at,last_error,accepted_at,created_at,updated_at FROM mail_deliveries WHERE (?='' OR status=?) ORDER BY id DESC LIMIT 100", status, status));
  }

  public Object replay(long id, long actor, String requestId) {
    return tx.execute(s -> {
      if (db.update("UPDATE mail_deliveries SET status='pending',attempts=0,next_attempt_at=timezone('UTC',now()),last_error='',updated_at=timezone('UTC',now()) "
          + "WHERE id=? AND status='failed' AND encrypted_payload IS NOT NULL AND expires_at>timezone('UTC',now()) "
          + "AND EXISTS(SELECT 1 FROM users u WHERE u.id=mail_deliveries.user_id AND u.account_state='active')", id) != 1)
        throw new ApiException(409, "投递不可重放，可能已过期、已接受或账号不可用");
      audit.success(actor, "mail.replayed", "mail_delivery", id, null, requestId);
      return Map.of("success", true);
    });
  }

  public void revokeUser(long userId) {
    db.update("DELETE FROM account_email_tokens WHERE user_id=?", userId);
    db.update("UPDATE mail_deliveries SET status=CASE WHEN status='accepted' THEN status ELSE 'cancelled' END,encrypted_payload=NULL,last_error='',lease_until=NULL,updated_at=timezone('UTC',now()) WHERE user_id=?", userId);
    db.update("DELETE FROM notification_preferences WHERE user_id=?", userId);
  }
}
