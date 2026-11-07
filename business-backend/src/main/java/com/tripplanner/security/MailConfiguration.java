package com.tripplanner.security;

import java.util.Properties;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.JavaMailSenderImpl;

@Configuration(proxyBeanMethods = false)
public class MailConfiguration {
  @Bean
  @ConditionalOnProperty(name = "SMTP_ENABLED", havingValue = "true")
  JavaMailSender mailSender(@Value("${SMTP_HOST:}") String host,
      @Value("${SMTP_PORT:587}") int port,
      @Value("${SMTP_USERNAME:}") String username,
      @Value("${SMTP_PASSWORD:}") String password,
      @Value("${SMTP_SECURITY:starttls}") String security,
      @Value("${APP_ENV:development}") String environment) {
    if (host.isBlank() || port < 1 || port > 65535 || !Set.of("starttls", "tls", "none").contains(security))
      throw new IllegalArgumentException("Invalid SMTP configuration");
    if (environment.equals("production") && security.equals("none"))
      throw new IllegalArgumentException("Production SMTP requires TLS");
    var sender = new JavaMailSenderImpl();
    sender.setHost(host); sender.setPort(port); sender.setDefaultEncoding("UTF-8");
    if (!username.isBlank()) { sender.setUsername(username); sender.setPassword(password); }
    var properties = new Properties();
    properties.setProperty("mail.smtp.auth", Boolean.toString(!username.isBlank()));
    properties.setProperty("mail.smtp.starttls.enable", Boolean.toString(security.equals("starttls")));
    properties.setProperty("mail.smtp.starttls.required", Boolean.toString(security.equals("starttls")));
    properties.setProperty("mail.smtp.ssl.enable", Boolean.toString(security.equals("tls")));
    properties.setProperty("mail.smtp.ssl.checkserveridentity", "true");
    properties.setProperty("mail.smtp.connectiontimeout", "5000");
    properties.setProperty("mail.smtp.timeout", "5000");
    properties.setProperty("mail.smtp.writetimeout", "5000");
    sender.setJavaMailProperties(properties);
    return sender;
  }
}
