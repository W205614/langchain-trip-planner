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
  synchronized void acquire() throws Exception {
    if(lease!=null){try{lease.close();}finally{lease=null;}}
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

  public synchronized boolean owned() {
    if(!enabled)return false;
    try {return lease!=null&&!lease.isClosed()&&lease.isValid(1);}catch(Exception ex){return false;}
  }

  @PreDestroy
  synchronized void release() throws Exception {
    if (lease != null) {
      try {
        if(!lease.isClosed()&&lease.isValid(1))try(var statement=lease.createStatement()){statement.execute("SELECT pg_advisory_unlock(842491570021)");}
      } finally {
        lease.close();
      }
    }
  }
}
