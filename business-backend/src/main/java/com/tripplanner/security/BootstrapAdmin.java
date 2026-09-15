package com.tripplanner.security;

import com.tripplanner.persistence.UserMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

@Component
public class BootstrapAdmin implements ApplicationRunner {
  private final UserMapper users;
  private final String username;
  private final boolean enabled;

  public BootstrapAdmin(
      UserMapper users,
      @Value("${BOOTSTRAP_ADMIN_USERNAME:}") String username,
      @Value("${WORKERS_ENABLED:true}") boolean enabled) {
    this.users = users;
    this.username = username.strip();
    this.enabled = enabled;
  }

  @Override
  public void run(ApplicationArguments args) {
    if (enabled && !username.isEmpty()) users.bootstrapAdmin(username);
  }
}
