package com.tripplanner.api;
import com.tripplanner.domain.DeploymentIdentity;
import java.time.Instant;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
public class OperationalController {
  private final DeploymentIdentity identity;private final JdbcTemplate db;
  public OperationalController(DeploymentIdentity identity,JdbcTemplate db){this.identity=identity;this.db=db;}
  @GetMapping("/internal/v2/dataset") public Object dataset(){return identity.snapshot();}
  @PostMapping("/internal/v2/operations/heartbeat/{name}") public Object heartbeat(@PathVariable String name,@RequestBody ObjectNode body){
    if(!java.util.Set.of("backup","offhost_backup","watchdog").contains(name))throw new ApiException(422,"Unknown heartbeat");
    Instant observed;try{observed=Instant.parse(body.path("observed_at").asText(""));}catch(Exception ex){throw new ApiException(422,"Invalid heartbeat time");}
    if(observed.isAfter(Instant.now().plusSeconds(30)))throw new ApiException(422,"Future heartbeat rejected");
    db.update("INSERT INTO operational_heartbeats(name,observed_at) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET observed_at=excluded.observed_at WHERE operational_heartbeats.observed_at<excluded.observed_at",name,java.sql.Timestamp.from(observed));
    return Map.of("success",true);
  }
}
