package com.tripplanner.domain;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.sql.Connection;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** The supported deployment has exactly one scheduler owner per database. */
@Component("singleInstance")
@org.springframework.context.annotation.DependsOn("deploymentIdentity")
public class SingleInstance {
  private final DataSource source;
  private final boolean enabled;
  private Connection lease;
  private volatile boolean lost;
  private volatile java.time.Instant lostAt;

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
      lost=false;lostAt=null;
    }
  }

  public synchronized boolean owned() {
    if(!enabled)return false;
    if(lost)return false;
    try {if(lease!=null&&!lease.isClosed()&&lease.isValid(1))return true;}catch(Exception ignored){}
    lost=true;lostAt=java.time.Instant.now();return false;
  }
  @org.springframework.beans.factory.annotation.Autowired
  void metrics(io.micrometer.core.instrument.MeterRegistry registry){
    io.micrometer.core.instrument.Gauge.builder("trip.scheduler.owned",this,o->o.owned()?1:0).register(registry);
  }
  public java.time.Instant lostAt(){return lostAt;}

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
