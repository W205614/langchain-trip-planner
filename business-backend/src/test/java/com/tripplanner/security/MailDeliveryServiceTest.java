package com.tripplanner.security;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.tripplanner.domain.BusinessAuditService;
import com.tripplanner.domain.SingleInstance;
import jakarta.mail.internet.InternetAddress;
import java.util.Base64;
import org.eclipse.angus.mail.smtp.SMTPAddressFailedException;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mail.MailAuthenticationException;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

class MailDeliveryServiceTest {
  @Test void encryptedOutboxRoundTripsUtf8AndRejectsTampering() {
    @SuppressWarnings("unchecked") ObjectProvider<JavaMailSender> provider = mock(ObjectProvider.class);
    var service = new MailDeliveryService(mock(JdbcTemplate.class), mock(TransactionTemplate.class), JsonMapper.builder().build(), provider,
        mock(SingleInstance.class), mock(BusinessAuditService.class), "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "trip@example.test", "http://localhost:8380");
    String text = "验证邮箱：旅行助手 https://example.test/#token=private";
    String encrypted = service.encrypt(text);
    assertFalse(encrypted.contains("private")); assertEquals(text, service.decrypt(encrypted));
    byte[] bytes = Base64.getDecoder().decode(encrypted); bytes[bytes.length - 1] ^= 1;
    assertThrows(IllegalStateException.class, () -> service.decrypt(Base64.getEncoder().encodeToString(bytes)));
  }
  @Test void smtpPermanentAndTransientRepliesHaveDifferentRetryBehavior() throws Exception {
    assertTrue(MailDeliveryService.permanent(new MailAuthenticationException("denied")));
    assertTrue(MailDeliveryService.permanent(new SMTPAddressFailedException(new InternetAddress("a@example.test"), "RCPT", 550, "rejected")));
    assertFalse(MailDeliveryService.permanent(new SMTPAddressFailedException(new InternetAddress("a@example.test"), "RCPT", 451, "temporary")));
    assertFalse(MailDeliveryService.permanent(new java.net.SocketTimeoutException("ack unknown")));
  }
}
