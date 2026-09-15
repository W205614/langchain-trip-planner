package com.tripplanner.api;

import com.tripplanner.agent.AgentClient;
import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.*;

@RestController
@ConditionalOnProperty(name = "APP_ENV", havingValue = "validation")
public class ValidationController {
  private final AgentClient agent;

  public ValidationController(AgentClient agent) {
    this.agent = agent;
  }

  @GetMapping("/api/validation/fixture")
  public Object marker() {
    return agent.post("/capabilities/validation", Map.of());
  }
}
