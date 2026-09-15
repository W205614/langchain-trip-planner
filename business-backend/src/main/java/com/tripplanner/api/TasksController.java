package com.tripplanner.api;

import com.tripplanner.domain.TaskService;
import com.tripplanner.persistence.TaskMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.util.*;
import java.util.concurrent.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/trip")
public class TasksController {
  private final TaskService service;
  private final TaskMapper mapper;
  private final JsonMapper json;

  public TasksController(TaskService service, TaskMapper mapper, JsonMapper json) {
    this.service = service;
    this.mapper = mapper;
    this.json = json;
  }

  @PostMapping("/tasks")
  @ResponseStatus(org.springframework.http.HttpStatus.ACCEPTED)
  public Object submit(
      HttpServletRequest req,
      @RequestBody ObjectNode body,
      @RequestHeader(value = "Idempotency-Key", required = false) String key) {
    return service.submit(UsersController.uid(req), body, key, req.getHeader("X-Request-ID"), null);
  }

  @GetMapping("/tasks/{id}")
  public Object get(HttpServletRequest req, @PathVariable String id) {
    return Map.of("success", true, "data", service.snapshot(UsersController.uid(req), id));
  }

  @PostMapping("/tasks/{id}/cancel")
  public Object cancel(HttpServletRequest req, @PathVariable String id) {
    return service.cancel(UsersController.uid(req), id);
  }

  @PostMapping("/tasks/{id}/retry")
  public Object retry(
      HttpServletRequest req,
      @PathVariable String id,
      @RequestHeader("Idempotency-Key") String key) {
    return service.retry(UsersController.uid(req), id, key, req.getHeader("X-Request-ID"));
  }

  @GetMapping("/tasks")
  public Object list(
      HttpServletRequest req,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int page_size) {
    if (page < 1 || page_size < 1 || page_size > 50) throw new ApiException(422, "分页参数无效");
    long uid = UsersController.uid(req);
    var rows =
        mapper.list(uid, (page - 1) * page_size, page_size).stream()
            .map(
                row -> {
                  var r = new LinkedHashMap<String, Object>();
                  for (String f :
                      List.of("id", "status", "message", "created_at", "record_id", "error_code"))
                    r.put(f, row.get(f));
                  r.put(
                      "city",
                      json.readTree(row.get("request_json").toString()).path("city").asText(""));
                  return r;
                })
            .toList();
    return Map.of("total", mapper.count(uid), "data", rows);
  }

  @GetMapping("/tasks/{id}/events")
  public SseEmitter events(HttpServletRequest req, @PathVariable String id) {
    return stream(UsersController.uid(req), id, false);
  }

  private SseEmitter stream(long uid, String id, boolean cached) {
    service.owned(uid, id);
    var emitter = new SseEmitter(310000L);
    var closed = new java.util.concurrent.atomic.AtomicBoolean();
    emitter.onCompletion(() -> closed.set(true));
    emitter.onTimeout(() -> closed.set(true));
    emitter.onError(e -> closed.set(true));
    Thread.startVirtualThread(
        () -> {
          try {
            String previous = "";
            while (!closed.get()) {
              var state = service.snapshot(uid, id);
              String status = state.path("status").asText("");
              if (Set.of("succeeded", "needs_attention").contains(status)) {
                var result = (ObjectNode) state.path("result");
                result.put("cached", cached);
                emitter.send(SseEmitter.event().name("complete").data(result));
                break;
              }
              if (Set.of("failed", "cancelled").contains(status)) {
                emitter.send(SseEmitter.event().name("error").data(state));
                break;
              }
              String current = state.toString();
              if (!current.equals(previous)) {
                emitter.send(SseEmitter.event().name("progress").data(state));
                previous = current;
              } else emitter.send(SseEmitter.event().comment("keep-alive"));
              Thread.sleep(1000);
            }
            emitter.complete();
          } catch (Exception ex) {
            emitter.completeWithError(ex);
          }
        });
    return emitter;
  }

  @PostMapping("/plan/stream")
  public SseEmitter planStream(
      HttpServletRequest req,
      @RequestBody ObjectNode body,
      @RequestHeader(value = "Idempotency-Key", required = false) String key) {
    long uid = UsersController.uid(req);
    var created = service.submit(uid, body, key, req.getHeader("X-Request-ID"), null);
    return stream(
        uid, created.path("data").path("id").asText(""), created.path("cached").asBoolean(false));
  }

  @PostMapping("/plan")
  public Object plan(
      HttpServletRequest req,
      @RequestBody ObjectNode body,
      @RequestHeader(value = "Idempotency-Key", required = false) String key)
      throws InterruptedException {
    long uid = UsersController.uid(req);
    var created = service.submit(uid, body, key, req.getHeader("X-Request-ID"), null);
    String id = created.path("data").path("id").asText("");
    while (true) {
      var state = service.snapshot(uid, id);
      String status = state.path("status").asText("");
      if (Set.of("succeeded", "needs_attention").contains(status)) {
        var result = (ObjectNode) state.path("result");
        result.set("cached", created.path("cached"));
        return result;
      }
      if (Set.of("failed", "cancelled").contains(status))
        throw new ApiException(
            state.path("error_code").asText("").equals("TASK_TIMEOUT") ? 504 : 500,
            state.path("message").asText(""),
            state.path("error_code").asText(""));
      Thread.sleep(100);
    }
  }
}
