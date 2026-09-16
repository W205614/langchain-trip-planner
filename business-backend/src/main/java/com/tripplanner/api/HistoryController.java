package com.tripplanner.api;

import com.tripplanner.domain.*;
import com.tripplanner.persistence.HistoryMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.util.*;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/history")
public class HistoryController {
  private final HistoryMapper history;
  private final TaskService tasks;
  private final PlanRules rules;
  private final TransactionTemplate tx;
  private final JsonMapper json;

  public HistoryController(
      HistoryMapper history,
      TaskService tasks,
      PlanRules rules,
      TransactionTemplate tx,
      JsonMapper json) {
    this.history = history;
    this.tasks = tasks;
    this.rules = rules;
    this.tx = tx;
    this.json = json;
  }

  private Map<String, Object> owned(long uid, long id) {
    var row = history.owned(uid, id);
    if (row == null) throw new ApiException(404, "历史记录不存在");
    return row;
  }

  private ObjectNode request(Map<String, Object> row) {
    var body = json.createObjectNode();
    for (String f :
        List.of(
            "city",
            "start_date",
            "end_date",
            "travel_days",
            "transportation",
            "accommodation",
            "free_text_input")) body.set(f, json.valueToTree(row.get(f)));
    body.set("preferences", json.readTree(row.get("preferences").toString()));
    body.set("constraints", json.readTree(row.get("plan_json").toString()).path("constraints"));
    if (body.path("constraints").isMissingNode()) body.putObject("constraints");
    return TripRequests.normalize(body);
  }

  @GetMapping
  public Object list(
      HttpServletRequest req,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "10") int page_size,
      @RequestParam(defaultValue = "") String city) {
    if (page < 1 || page_size < 1 || page_size > 50) throw new ApiException(422, "分页参数无效");
    long uid = UsersController.uid(req);
    var rows =
        history.list(uid, city, (page - 1) * page_size, page_size).stream()
            .map(
                row -> {
                  var result = new LinkedHashMap<String, Object>();
                  for (String f :
                      List.of(
                          "id",
                          "version",
                          "city",
                          "start_date",
                          "end_date",
                          "travel_days",
                          "transportation",
                          "accommodation",
                          "title",
                          "source",
                          "updated_at",
                          "last_verified_at",
                          "created_at")) result.put(f, row.get(f));
                  result.put("preferences", json.readTree(row.get("preferences").toString()));
                  var plan = json.readTree(row.get("plan_json").toString());
                  result.put(
                      "outcome",
                      json.readTree(row.get("quality_json").toString())
                          .path("outcome")
                          .asText("unassessed"));
                  result.put("budget_total", plan.path("budget").path("total").asInt(0));
                  result.put(
                      "attraction_count",
                      plan.path("days")
                          .valueStream()
                          .mapToInt(d -> d.path("attractions").size())
                          .sum());
                  return result;
                })
            .toList();
    return Map.of(
        "success",
        true,
        "data",
        rows,
        "total",
        history.count(uid, city),
        "page",
        page,
        "page_size",
        page_size);
  }

  @GetMapping("/{id}")
  public Object get(HttpServletRequest req, @PathVariable long id) {
    var row = new LinkedHashMap<>(owned(UsersController.uid(req), id));
    row.put("plan", json.readTree(row.remove("plan_json").toString()));
    row.put("quality", json.readTree(row.remove("quality_json").toString()));
    row.put("preferences", json.readTree(row.get("preferences").toString()));
    row.remove("user_id");
    return Map.of("success", true, "data", row);
  }

  @PutMapping("/{id}")
  public Object update(
      HttpServletRequest req,
      @PathVariable long id,
      @RequestHeader("If-Match") int version,
      @RequestBody ObjectNode plan) {
    long uid = UsersController.uid(req);
    var row = owned(uid, id);
    if (version < 1) throw new ApiException(422, "版本无效");
    if (!plan.path("days").isArray()) throw new ApiException(422, "行程结构无效");
    var original = json.readTree(row.get("plan_json").toString());
    var pois = new HashMap<String, tools.jackson.databind.JsonNode>();
    original
        .path("days")
        .forEach(d -> d.path("attractions").forEach(a -> pois.put(a.path("poi_id").asText(""), a)));
    for (var d : plan.path("days"))
      for (var a : d.path("attractions")) {
        var saved = pois.get(a.path("poi_id").asText(""));
        ((ObjectNode) a)
            .set(
                "requested_names",
                saved != null && saved.path("name").equals(a.path("name"))
                    ? saved.path("requested_names")
                    : json.createArrayNode());
      }
    var quality = rules.finish(plan, request(row), null, false);
    ((tools.jackson.databind.node.ArrayNode) quality.path("data_gaps"))
        .add("user_edited_plan_not_externally_verified");
    tx.executeWithoutResult(s -> save(uid, id, version, plan, quality));
    return Map.of(
        "success",
        true,
        "message",
        "更新成功",
        "id",
        id,
        "version",
        version + 1,
        "data",
        plan,
        "quality",
        quality,
        "saved",
        true,
        "rag_sync_pending",
        true);
  }

  private void save(long uid, long id, int version, ObjectNode plan, ObjectNode quality) {
    if (history.update(
            Map.of(
                "id",
                id,
                "user_id",
                uid,
                "version",
                version,
                "plan_json",
                json.writeValueAsString(plan),
                "quality_json",
                json.writeValueAsString(quality)))
        != 1) throw new ApiException(409, "行程版本冲突，请重新加载", "VERSION_CONFLICT");
    history.outbox(
        id, uid, quality.path("outcome").asText("").equals("draft") ? "delete" : "upsert");
  }

  @DeleteMapping("/{id}")
  public Object delete(HttpServletRequest req, @PathVariable long id) {
    long uid = UsersController.uid(req);
    tx.executeWithoutResult(
        s -> {
          owned(uid, id);
          history.outbox(id, uid, "delete");
          history.delete(uid, id);
        });
    return Map.of("success", true, "message", "删除成功", "rag_sync_pending", true);
  }

  @PostMapping("/{id}/revise-task")
  @ResponseStatus(org.springframework.http.HttpStatus.ACCEPTED)
  public ObjectNode revise(
      HttpServletRequest req,
      @PathVariable long id,
      @RequestHeader("If-Match") int version,
      @RequestHeader("Idempotency-Key") String key,
      @RequestBody ObjectNode revision) {
    long uid = UsersController.uid(req);
    var row = owned(uid, id);
    var body = request(row);
    TripRequests.integer(revision, "day_index", 0, body.path("travel_days").asInt(0) - 1);
    TripRequests.text(revision, "instruction", 2, 500);
    if (version < 1) throw new ApiException(422, "版本无效");
    revision = revision.deepCopy().put("record_id", id).put("version", version);
    return tasks.submit(uid, body, key, req.getHeader("X-Request-ID"), revision);
  }

  @PostMapping("/{id}/revise-day")
  public Object reviseDay(
      HttpServletRequest req,
      @PathVariable long id,
      @RequestHeader("If-Match") int version,
      @RequestHeader(value = "Idempotency-Key", required = false) String key,
      @RequestBody ObjectNode revision)
      throws InterruptedException {
    long uid = UsersController.uid(req);
    if (((Number) owned(uid, id).get("version")).intValue() != version)
      throw new ApiException(409, "行程版本冲突，请重新加载", "VERSION_CONFLICT");
    var created =
        revise(req, id, version, key == null ? UUID.randomUUID().toString() : key, revision);
    String taskId = created.path("data").path("id").asText("");
    while (true) {
      var state = tasks.snapshot(uid, taskId);
      String status = state.path("status").asText("");
      if (Set.of("succeeded", "needs_attention").contains(status))
        return ((ObjectNode) state.path("result")).put("rag_sync_pending", true);
      if (Set.of("failed", "cancelled").contains(status))
        throw new ApiException(
            state.path("error_code").asText("").equals("VERSION_CONFLICT") ? 409 : 500,
            state.path("message").asText(""),
            state.path("error_code").asText(""));
      Thread.sleep(100);
    }
  }

  @PostMapping("/{id}/apply-draft")
  public Object apply(
      HttpServletRequest req, @PathVariable long id, @RequestHeader("If-Match") int version) {
    long uid = UsersController.uid(req);
    return tx.execute(
        s -> {
          var row = owned(uid, id);
          var quality = (ObjectNode) json.readTree(row.get("quality_json").toString());
          var parent = quality.remove("revision_parent");
          if (parent == null || ((Number) row.get("version")).intValue() != version)
            throw new ApiException(409, "草稿已变更或不是改排草稿", "VERSION_CONFLICT");
          var plan = (ObjectNode) json.readTree(row.get("plan_json").toString());
          long target = parent.path("record_id").asLong(0);
          save(uid, target, parent.path("version").asInt(0), plan, quality);
          quality.put("applied_to", target);
          save(uid, id, version, plan, quality);
          return Map.of("success", true, "id", target, "message", "已应用；未满足的要求仍保留为草稿提示");
        });
  }
}
