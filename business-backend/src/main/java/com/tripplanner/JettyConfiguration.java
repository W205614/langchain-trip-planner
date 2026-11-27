package com.tripplanner;

import org.eclipse.jetty.server.ServerConnector;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.jetty.ConfigurableJettyWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.Arrays;

@Configuration(proxyBeanMethods = false)
class JettyConfiguration {
  @Bean
  WebServerFactoryCustomizer<ConfigurableJettyWebServerFactory> jettyAcceptQueue(
      @Value("${SERVER_ACCEPT_COUNT:32}") int acceptCount) {
    return factory -> factory.addServerCustomizers(server ->
        Arrays.stream(server.getConnectors())
            .filter(ServerConnector.class::isInstance)
            .map(ServerConnector.class::cast)
            .forEach(connector -> connector.setAcceptQueueSize(acceptCount)));
  }
}
