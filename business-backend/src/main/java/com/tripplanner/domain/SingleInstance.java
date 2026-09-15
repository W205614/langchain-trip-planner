package com.tripplanner.domain;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.sql.Connection;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** The supported deployment has exactly one scheduler owner per database. */
@Component("singleInstance")
public class SingleInstance {
  private final DataSource source;
  private final boolean enabled;
  private Connection lease;

  public SingleInstance(DataSource source, @Value("${WORKERS_ENABLED:true}") boolean enabled) {
    this.source = source;
    this.enabled = enabled;
  }

  @PostConstruct
  void acquire() throws Exception {
    if (!enabled) return;
    lease = source.getConnection();
    try (var statement = lease.createStatement();
        var result = statement.executeQuery("SELECT pg_try_advisory_lock(842491570021)")) {
      result.next();
      if (!result.getBoolean(1)) {
        lease.close();
        lease = null;
        throw new IllegalStateException("Another business worker owns this database");
      }
    }
  }

  @PreDestroy
  void release() throws Exception {
    if (lease != null) {
      try (var statement = lease.createStatement()) {
        statement.execute("SELECT pg_advisory_unlock(842491570021)");
      } finally {
        lease.close();
      }
    }
  }
}
