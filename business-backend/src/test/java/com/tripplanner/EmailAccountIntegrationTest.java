package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.api.ApiException;
import com.tripplanner.security.*;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.regex.Pattern;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.*;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Uses real PostgreSQL, JavaMail SMTP and the isolated Mailpit API. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT, properties={
    "trip.jwt-secret=email-test-jwt-012345678901234567890123456789",
    "trip.internal-key=email-test-internal-012345678901234567890123456789",
    "spring.datasource.url=${TEST_DATABASE_URL}", "spring.datasource.username=trip",
    "spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}",
    "WORKERS_ENABLED=false", "APP_ENV=validation", "VALIDATION_ALLOW_FIXTURES=yes",
    "PUBLIC_REGISTRATION_MODE=email_verified", "PUBLIC_BASE_URL=http://localhost:8380",
    "SMTP_ENABLED=true", "SMTP_HOST=127.0.0.1", "SMTP_PORT=${TEST_SMTP_PORT:11025}", "SMTP_SECURITY=none",
    "SMTP_FROM=trip@example.test", "ACCOUNT_SECURITY_ENCRYPTION_KEY=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL", matches=".+")
@EnabledIfEnvironmentVariable(named="TEST_MAILPIT_URL", matches=".+")
class EmailAccountIntegrationTest {
  @Value("${local.server.port}") int port;
  @Autowired JdbcTemplate db;
  @Autowired JsonMapper json;
  @Autowired MailDeliveryService mail;
  @Autowired AccountAccess access;
  @Autowired EmailAccountService emails;
  @Autowired org.springframework.transaction.support.TransactionTemplate tx;
  @Autowired com.tripplanner.domain.SingleInstance owner;
  @Autowired com.tripplanner.domain.BusinessAuditService audit;
  private final HttpClient http = HttpClient.newHttpClient();
  private final String password = "email-test-password-123";

  HttpResponse<String> request(String method, String path, Object value, String bearer) throws Exception {
    var builder = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + path)).header("Content-Type", "application/json");
    if (bearer != null) builder.header("Authorization", "Bearer " + bearer);
    builder.method(method, value == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(json.writeValueAsString(value)));
    return http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
  }
  JsonNode body(HttpResponse<String> response) { return json.readTree(response.body()); }
  String username() { return "email_" + UUID.randomUUID().toString().replace("-", "").substring(0, 20); }
  long id(String name) { return db.queryForObject("SELECT id FROM users WHERE username=?", Long.class, name); }
  String register(String name, String email) throws Exception {
    var response = request("POST", "/api/auth/register", Map.of("username", name, "password", password, "email", email), null);
    assertEquals(200, response.statusCode(), response.body());
    return body(response).path("access_token").asText();
  }
  String tokenInMail(String email, String mode) throws Exception {
    String base = System.getenv("TEST_MAILPIT_URL").replaceAll("/+$", "");
    for (int attempt = 0; attempt < 20; attempt++) {
      mail.deliverOne();
      var result = http.send(HttpRequest.newBuilder(URI.create(base + "/api/v1/search?query=" + URLEncoder.encode("to:" + email, StandardCharsets.UTF_8))).GET().build(), HttpResponse.BodyHandlers.ofString());
      assertEquals(200, result.statusCode(), result.body());
      for (var item : json.readTree(result.body()).path("messages")) {
        var message = http.send(HttpRequest.newBuilder(URI.create(base + "/api/v1/message/" + item.path("ID").asText())).GET().build(), HttpResponse.BodyHandlers.ofString());
        String text = json.readTree(message.body()).path("Text").asText();
        if (!text.contains("mode=" + mode)) continue;
        var matcher = Pattern.compile("token=([A-Za-z0-9_-]{43})").matcher(text);
        if (matcher.find()) return matcher.group(1);
      }
      Thread.sleep(30);
    }
    fail("No real SMTP message for the isolated recipient"); return "";
  }

  @Test void verificationIsSingleUseAndSensitivePayloadIsEncrypted() throws Exception {
    String name = username(), email = name + "@example.test", credential = register(name, email);
    long uid = id(name);
    assertEquals("EMAIL_VERIFICATION_REQUIRED", assertThrows(ApiException.class, () -> access.requireVerified(uid)).code);
    var queued = db.queryForMap("SELECT * FROM mail_deliveries WHERE user_id=? ORDER BY id DESC LIMIT 1", uid);
    assertFalse(queued.get("encrypted_payload").toString().contains(email));
    double remaining = db.queryForObject("SELECT extract(epoch FROM expires_at-timezone('UTC',now())) FROM account_email_tokens WHERE user_id=?", Double.class, uid);
    assertTrue(remaining > 1700 && remaining <= 1800, "Expiry uses UTC without inheriting the host timezone");
    String token = tokenInMail(email, "verify");
    assertEquals(AccountSecurityService.hash(token), db.queryForObject("SELECT token_hash FROM account_email_tokens WHERE user_id=?", String.class, uid));
    assertEquals(200, request("POST", "/api/auth/email/confirm", Map.of("token", token), null).statusCode());
    assertDoesNotThrow(() -> access.requireVerified(uid));
    assertEquals(409, request("POST", "/api/auth/email/confirm", Map.of("token", token), null).statusCode());
    var me = request("GET", "/api/auth/me", null, credential);
    assertEquals(200, me.statusCode()); assertTrue(body(me).path("email_verified").asBoolean());
    assertFalse(body(me).has("hashed_password")); assertFalse(body(me).has("token_version"));
    assertEquals("accepted", db.queryForObject("SELECT status FROM mail_deliveries WHERE id=?", String.class, queued.get("id")));
    assertNull(db.queryForObject("SELECT encrypted_payload FROM mail_deliveries WHERE id=?", String.class, queued.get("id")));
    assertEquals(403, request("GET", "/api/admin/mail", null, credential).statusCode());
  }

  @Test void emailPasswordResetPreservesMfaAndRevokesPriorSession() throws Exception {
    String name = username(), email = name + "@example.test", credential = register(name, email);
    long uid = id(name);
    assertEquals(200, request("POST", "/api/auth/email/confirm", Map.of("token", tokenInMail(email, "verify")), null).statusCode());
    String secret = Totp.newSecret();
    db.update("INSERT INTO account_security(user_id,totp_enabled,totp_secret) VALUES(?,true,?)", uid,
        Totp.encrypt(secret, "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="));
    assertEquals(200, request("POST", "/api/auth/email/request-reset", Map.of("email", email), null).statusCode());
    String token = tokenInMail(email, "reset");
    String next = "email-recovered-password-456";
    assertEquals(401, request("POST", "/api/auth/email/reset", Map.of("token", token, "new_password", next), null).statusCode());
    String code = Totp.code(secret, Instant.now().getEpochSecond() / 30);
    var reset = request("POST", "/api/auth/email/reset", Map.of("token", token, "new_password", next, "totp_code", code), null);
    assertEquals(200, reset.statusCode(), reset.body());
    assertTrue(db.queryForObject("SELECT totp_enabled FROM account_security WHERE user_id=?", Boolean.class, uid));
    assertEquals(401, request("GET", "/api/auth/me", null, credential).statusCode());
    assertEquals(409, request("POST", "/api/auth/email/reset", Map.of("token", token, "new_password", next, "totp_code", code), null).statusCode());
    assertEquals(401, request("POST", "/api/auth/login", Map.of("username", name, "password", next), null).statusCode());
  }

  @Test void expiryResendAndDisabledAccountAreEnforced() throws Exception {
    String name = username(), email = name + "@example.test", credential = register(name, email);
    long uid = id(name); String token = tokenInMail(email, "verify");
    assertEquals(429, request("POST", "/api/account/email/resend", Map.of(), credential).statusCode());
    db.update("UPDATE account_email_tokens SET expires_at=timezone('UTC',now())-interval '1 second' WHERE user_id=?", uid);
    assertEquals(409, request("POST", "/api/auth/email/confirm", Map.of("token", token), null).statusCode());
    db.update("UPDATE users SET account_state='disabled' WHERE id=?", uid);
    assertEquals(401, request("GET", "/api/auth/me", null, credential).statusCode());
    assertEquals(401, request("POST", "/api/auth/login", Map.of("username", name, "password", password), null).statusCode());
    assertEquals(200, request("POST", "/api/auth/email/request-reset", Map.of("email", email), null).statusCode());
  }

  @Test void unavailableSmtpExhaustsFiveAttemptsWithoutFalseAcceptance() {
    String name = username();
    long uid = db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,?) RETURNING id", Long.class, name, Passwords.hash(password));
    var unavailable = new org.springframework.mail.javamail.JavaMailSenderImpl();
    unavailable.setHost("127.0.0.1"); unavailable.setPort(9);
    unavailable.getJavaMailProperties().setProperty("mail.smtp.connectiontimeout", "200");
    var factory = new org.springframework.beans.factory.support.StaticListableBeanFactory();
    factory.addBean("smtp", unavailable);
    var queue = new MailDeliveryService(db, tx, json, factory.getBeanProvider(org.springframework.mail.javamail.JavaMailSender.class), owner, audit,
        "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "trip@example.test", "http://localhost:8380");
    queue.enqueue(uid, "failure:" + name, "test", name + "@example.test", "isolated failure", "no external delivery", Instant.now().plusSeconds(600));
    long job = db.queryForObject("SELECT id FROM mail_deliveries WHERE event_key=?", Long.class, "failure:" + name);
    for (int attempt = 0; attempt < 5; attempt++) {
      db.update("UPDATE mail_deliveries SET next_attempt_at=timezone('UTC',now()) WHERE id=?", job);
      assertTrue(queue.deliverOne());
    }
    var result = db.queryForMap("SELECT status,attempts,accepted_at,encrypted_payload FROM mail_deliveries WHERE id=?", job);
    assertEquals("failed", result.get("status")); assertEquals(5, ((Number)result.get("attempts")).intValue());
    assertNull(result.get("accepted_at")); assertNotNull(result.get("encrypted_payload"));
    queue.revokeUser(uid);
    assertNull(db.queryForObject("SELECT encrypted_payload FROM mail_deliveries WHERE id=?", String.class, job));
  }

  @Test void registrationRollsBackWithoutEmailAndUnsubscribeCancelsPendingNotifications() throws Exception {
    String missing = username();
    var invalid = request("POST", "/api/auth/register", Map.of("username", missing, "password", password), null);
    assertEquals(422, invalid.statusCode());
    assertEquals(0, db.queryForObject("SELECT count(*) FROM users WHERE username=?", Integer.class, missing));
    String name = username(), email = name + "@example.test";
    register(name, email); long uid = id(name);
    assertEquals(200, request("POST", "/api/auth/email/confirm", Map.of("token", tokenInMail(email, "verify")), null).statusCode());
    emails.preferences(uid, true);
    long notification = db.queryForObject("INSERT INTO trip_notifications(user_id,event_key,kind,title,message) VALUES(?,?,'departure','isolated','isolated') RETURNING id", Long.class, uid, "test:" + name);
    mail.bridgeNotifications();
    assertEquals("pending", db.queryForObject("SELECT status FROM mail_deliveries WHERE event_key=?", String.class, "notification:" + notification));
    emails.preferences(uid, false);
    var cancelled = db.queryForMap("SELECT status,encrypted_payload FROM mail_deliveries WHERE event_key=?", "notification:" + notification);
    assertEquals("cancelled", cancelled.get("status")); assertNull(cancelled.get("encrypted_payload"));
  }
}
