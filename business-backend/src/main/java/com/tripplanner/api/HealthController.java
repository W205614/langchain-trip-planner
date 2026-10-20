package com.tripplanner.api;

import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

@RestController
public class HealthController {
  @org.springframework.beans.factory.annotation.Autowired(required=false) private com.tripplanner.domain.SingleInstance singleInstance;
  @org.springframework.beans.factory.annotation.Value("${WORKERS_ENABLED:true}") private boolean workersEnabled;
  private final JdbcTemplate jdbc;

  public HealthController(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  @GetMapping({"/health", "/healthz"})
  public Object health() {
    return Map.of("status", "healthy", "service", "trip-business");
  }

  @GetMapping("/readyz")
  public Object ready() {
    if(workersEnabled&&singleInstance!=null&&!singleInstance.owned())throw new ApiException(503,"调度器所有权已丢失，请恢复服务","SCHEDULER_OWNERSHIP_LOST");
    try {
      jdbc.queryForObject("SELECT revision FROM evidence_revision WHERE id=1", Long.class);
      return health();
    } catch (Exception ex) {
      throw new ApiException(503, "数据库尚未就绪");
    }
  }
}
