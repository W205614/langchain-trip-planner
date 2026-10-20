package com.tripplanner.api;
import com.tripplanner.domain.*;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
public class PlanningController {
  private final PlanningInputService planning;private final ExecutionGovernanceService governance;private final TaskService tasks;
  public PlanningController(PlanningInputService planning,ExecutionGovernanceService governance,TaskService tasks){this.planning=planning;this.governance=governance;this.tasks=tasks;}
  @GetMapping("/api/trips/{id}/planning-input") public Object get(HttpServletRequest req,@PathVariable long id){return planning.get(UsersController.uid(req),id);}
  @PutMapping("/api/trips/{id}/planning-input") public Object put(HttpServletRequest req,@PathVariable long id,@RequestHeader("If-Match")int version,@RequestBody ObjectNode body){return planning.put(UsersController.uid(req),id,version,body.path("planning_revision").asInt(-1),body.path("planning_input"));}
  @PostMapping("/api/trips/{id}/activities/{activityId}/state") public Object state(HttpServletRequest req,@PathVariable long id,@PathVariable String activityId,@RequestHeader("If-Match")int version,@RequestBody ObjectNode body){return planning.state(UsersController.uid(req),id,version,activityId,body);}
  @PostMapping({"/api/tasks/{id}/resume","/api/trip/tasks/{id}/resume"}) public Object resume(HttpServletRequest req,@PathVariable String id,@RequestHeader("Idempotency-Key")String key){return tasks.resume(UsersController.uid(req),id,key,req.getHeader("X-Request-ID"));}
  @GetMapping("/internal/v2/executions/{executionId}/checkpoints") public Object checkpoints(@PathVariable String executionId){return governance.get(executionId);}
  @PostMapping("/internal/v2/executions/{executionId}/checkpoints") public Object checkpoint(@PathVariable String executionId,@RequestBody ObjectNode body){return governance.save(executionId,body);}
  @PostMapping("/internal/v2/outbound/reserve") public Object reserve(@RequestBody ObjectNode body){return governance.reserve(body);}
}
