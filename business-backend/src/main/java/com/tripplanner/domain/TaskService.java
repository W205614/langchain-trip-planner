package com.tripplanner.domain;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.*;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
@org.springframework.context.annotation.DependsOn("singleInstance")
public class TaskService {
  private final TaskMapper tasks;
  private final HistoryMapper history;
  private final TransactionTemplate tx;
  private final AgentClient agent;
  private final PlanRules rules;
  private final AmapGateway amap;
  private final JsonMapper json;
  private final ExecutorService pool;
  private final Semaphore slots;
  private final ConcurrentHashMap<String, FutureTask<Void>> active = new ConcurrentHashMap<>();
  private final int timeout, queueLimit, userLimit, userDaily, globalDaily;
  private volatile boolean closing;

  @Value("${WORKERS_ENABLED:true}")
  private boolean workersEnabled;

  public TaskService(
      TaskMapper tasks,
      HistoryMapper history,
      TransactionTemplate tx,
      AgentClient agent,
      PlanRules rules,
      AmapGateway amap,
      JsonMapper json,
      @Value("${LLM_REQUEST_MAX_CONCURRENCY:4}") int concurrency,
      @Value("${TRIP_TASK_TIMEOUT_SECONDS:300}") int timeout,
      @Value("${TRIP_TASK_QUEUE_LIMIT:32}") int queueLimit,
      @Value("${TRIP_USER_ACTIVE_LIMIT:4}") int userLimit,
      @Value("${TRIP_USER_DAILY_LIMIT:50}") int userDaily,
      @Value("${TRIP_GLOBAL_DAILY_LIMIT:500}") int globalDaily) {
    this.tasks = tasks;
    this.history = history;
    this.tx = tx;
    this.agent = agent;
    this.rules = rules;
    this.amap = amap;
    this.json = json;
    this.timeout = timeout;
    this.queueLimit = queueLimit;
    this.userLimit = userLimit;
    this.userDaily = userDaily;
    this.globalDaily = globalDaily;
    slots = new Semaphore(concurrency);
    pool = Executors.newFixedThreadPool(concurrency);
  }

  @PostConstruct
  void recover() {
    if (workersEnabled) tasks.recover();
  }

  @PreDestroy
  synchronized void close() {
    closing = true;
    try {
      // Persist the shutdown reason before interrupting HTTP workers. Otherwise
      // their catch block can win the race and misclassify restart as a timeout.
      if (workersEnabled) tasks.recover();
    } finally {
      active.values().forEach(f -> f.cancel(true));
      pool.shutdownNow();
    }
  }

  public synchronized ObjectNode submit(
      long uid, ObjectNode input, String key, String requestId, ObjectNode revision) {
    if (closing) throw new ApiException(503, "服务正在停止，请稍后重试", "PROCESS_INTERRUPTED");
    ObjectNode body = TripRequests.normalize(input);
    if (revision != null) body.set("_revision", revision);
    String idempotency = key == null ? UUID.randomUUID().toString() : key;
    if (idempotency.isEmpty() || idempotency.length() > 128)
      throw new ApiException(422, "Idempotency-Key 长度必须为 1–128");
    String payload = json.writeValueAsString(canonical(body));
    String fingerprint;
    try {
      fingerprint =
          HexFormat.of()
              .formatHex(
                  MessageDigest.getInstance("SHA-256")
                      .digest(payload.getBytes(StandardCharsets.UTF_8)));
    } catch (Exception ex) {
      throw new IllegalStateException(ex);
    }
    var existing = tasks.byKey(uid, idempotency);
    if (existing != null) {
      if (!fingerprint.equals(existing.get("fingerprint"))
          && !canonical(json.readTree(existing.get("request_json").toString()))
              .equals(canonical(body))) throw new ApiException(409, "同一幂等键不能用于不同请求");
      return response(uid, existing.get("id").toString(), true);
    }
    if (!agent.available())
      throw new ApiException(503, "智能规划暂不可用，传统行程功能仍可使用", "AGENT_UNAVAILABLE");
    if (tasks.activeUser(uid) >= userLimit)
      throw new ApiException(429, "您的待处理任务过多", "USER_QUEUE_FULL");
    if (tasks.daily() >= globalDaily || tasks.dailyUser(uid) >= userDaily)
      throw new ApiException(429, "今日规划任务额度已用完（UTC日界）", "DAILY_TASK_LIMIT");
    if (tasks.active() >= queueLimit) throw new ApiException(429, "规划队列已满", "TASK_QUEUE_FULL");
    String id = UUID.randomUUID().toString();
    tasks.insert(
        Map.of(
            "id",
            id,
            "user_id",
            uid,
            "idempotency_key",
            idempotency,
            "fingerprint",
            fingerprint,
            "request_json",
            payload,
            "request_id",
            requestId == null ? "" : requestId.substring(0, Math.min(64, requestId.length())),
            "deadline_at",
            Timestamp.valueOf(LocalDateTime.now(ZoneOffset.UTC).plusSeconds(timeout))));
    return response(uid, id, false);
  }

  private JsonNode canonical(JsonNode value) {
    if (value.isObject()) {
      var result = json.createObjectNode();
      var names = new TreeSet<String>();
      value.propertyNames().forEach(names::add);
      names.forEach(n -> result.set(n, canonical(value.get(n))));
      return result;
    }
    if (value.isArray()) {
      var result = json.createArrayNode();
      value.forEach(v -> result.add(canonical(v)));
      return result;
    }
    return value;
  }

  private ObjectNode response(long uid, String id, boolean cached) {
    var r = json.createObjectNode().put("success", true).put("cached", cached);
    r.set("data", snapshot(uid, id));
    return r;
  }

  public Map<String, Object> owned(long uid, String id) {
    var t = tasks.owned(uid, id);
    if (t == null) throw new ApiException(404, "任务不存在");
    return t;
  }

  public ObjectNode snapshot(long uid, String id) {
    var row = owned(uid, id);
    var state = json.createObjectNode();
    for (String field : List.of("id", "status", "stage", "percent", "message", "error_code"))
      state.set(field, json.valueToTree(row.get(field)));
    state.set("usage", json.readTree(row.get("usage_json").toString()));
    state.put(
        "deadline_at",
        ((Timestamp) row.get("deadline_at"))
            .toLocalDateTime()
            .toInstant(ZoneOffset.UTC)
            .toString());
    state.put(
        "retryable",
        !Set.of("", "VERSION_CONFLICT", "RESULT_DELETED", "UPSTREAM_CONFIG_ERROR")
            .contains(row.get("error_code").toString()));
    if (Set.of("succeeded", "needs_attention").contains(row.get("status"))) {
      var record =
          row.get("record_id") == null
              ? null
              : history.owned(uid, ((Number) row.get("record_id")).longValue());
      if (record == null)
        state.put("status", "failed").put("error_code", "RESULT_DELETED").put("message", "行程已删除");
      else {
        var result =
            state
                .putObject("result")
                .put("success", true)
                .put("saved", true)
                .put("message", row.get("message").toString())
                .put("task_id", id);
        result.set("id", json.valueToTree(record.get("id")));
        result.set("version", json.valueToTree(record.get("version")));
        result.set("data", json.readTree(record.get("plan_json").toString()));
        result.set("quality", json.readTree(record.get("quality_json").toString()));
      }
    }
    return state;
  }

  public ObjectNode cancel(long uid, String id) {
    var task = owned(uid, id);
    tasks.cancel(uid, id);
    var running = active.get(id);
    if (running != null) running.cancel(true);
    if (task.get("execution_id") != null)
      try {
        agent.post("/executions/" + task.get("execution_id") + "/cancel", Map.of());
      } catch (Exception ignored) {
      }
    var response = json.createObjectNode().put("success", true);
    response.set("data", snapshot(uid, id));
    return response;
  }

  public ObjectNode retry(long uid, String id, String key, String requestId) {
    var row = owned(uid, id);
    if (!Set.of("failed", "cancelled", "needs_attention").contains(row.get("status")))
      throw new ApiException(409, "只有失败、取消或草稿任务可以重试");
    var body = (ObjectNode) json.readTree(row.get("request_json").toString());
    var revision = (ObjectNode) body.remove("_revision");
    return submit(uid, body, key, requestId, revision);
  }

  @Scheduled(fixedDelay = 250)
  public synchronized void tick() {
    if (!workersEnabled || closing) return;
    tasks.expire();
    active.forEach(
        (id, future) -> {
          var row = tasks.get(id);
          if (row == null || !"running".equals(row.get("status"))) future.cancel(true);
        });
    if (!slots.tryAcquire()) return;
    String execution = UUID.randomUUID().toString();
    Map<String, Object> row;
    try {
      row = tx.execute(s -> tasks.claim(execution));
    } catch (Exception ex) {
      slots.release();
      throw ex;
    }
    if (row == null) {
      slots.release();
      return;
    }
    String id = row.get("id").toString();
    var future =
        new FutureTask<Void>(
            () -> {
              execute(row);
              return null;
            });
    active.put(id, future);
    try {
      pool.execute(
          () -> {
            try {
              future.run();
            } finally {
              active.remove(id);
              slots.release();
            }
          });
    } catch (Exception ex) {
      active.remove(id);
      slots.release();
      tasks.fail(id, execution, "WORKER_START_FAILED", "执行线程启动失败");
    }
  }

  private void execute(Map<String, Object> task) {
    String id = task.get("id").toString(), execution = task.get("execution_id").toString();
    long uid = ((Number) task.get("user_id")).longValue();
    var body = (ObjectNode) json.readTree(task.get("request_json").toString());
    var revision = (ObjectNode) body.remove("_revision");
    try {
      Instant deadline =
          ((Timestamp) task.get("deadline_at")).toLocalDateTime().toInstant(ZoneOffset.UTC);
      var request =
          json.createObjectNode()
              .put("protocol_version", 1)
              .put("task_id", id)
              .put("execution_id", execution)
              .put("user_id", uid)
              .put("request_id", task.get("request_id").toString())
              .put("deadline_at", deadline.toString());
      request.set("request", body);
      if (revision != null && revision.path("record_id").asLong(0) > 0) {
        var parent = history.owned(uid, revision.path("record_id").asLong(0));
        if (parent == null
            || ((Number) parent.get("version")).intValue() != revision.path("version").asInt(0))
          throw new ApiException(409, "行程已变更", "VERSION_CONFLICT");
        request.set("original_plan", json.readTree(parent.get("plan_json").toString()));
        request.set("original_version", revision.path("version"));
        request.set("day_index", revision.path("day_index"));
        request.set("instruction", revision.path("instruction"));
      }
      agent.generate(
          request,
          Duration.between(Instant.now(), deadline),
          (event, data) -> {
            if (!"running".equals(tasks.get(id).get("status")) || Instant.now().isAfter(deadline))
              throw new ApiException(409, "任务已终结", "TASK_TIMEOUT");
            if (event.equals("error"))
              throw new ApiException(503, "规划未完成", data.path("code").asText("GENERATION_FAILED"));
            if (event.equals("progress")) {
              task.put("stage", data.path("stage").asText(""));
              task.put("percent", data.path("percent").asInt(0));
              task.put("message", data.path("message").asText(""));
              tasks.progress(task);
            }
            if (event.equals("usage")) {
              task.put("usage_json", json.writeValueAsString(data));
              tasks.progress(task);
            }
            if (event.equals("result")) {
              if (data.path("protocol_version").asInt(0) != 1)
                throw new ApiException(503, "结果协议版本不匹配", "AGENT_PROTOCOL_ERROR");
              if (!execution.equals(data.path("execution_id").asText("")))
                throw new ApiException(503, "执行结果编号不匹配", "AGENT_PROTOCOL_ERROR");
              var plan = (ObjectNode) data.path("plan");
              TrustedCandidates.verify(
                  plan, data.path("trusted_candidates"), request.get("original_plan"));
              var unverified = canonicalizePois(plan, body.path("city").asText(""));
              var quality =
                  rules.finish(
                      plan,
                      body,
                      (left, right, type, city) -> {
                        var current = tasks.get(id);
                        if (Thread.currentThread().isInterrupted()
                            || !Instant.now().isBefore(deadline)
                            || current == null
                            || !"running".equals(current.get("status"))
                            || !execution.equals(current.get("execution_id")))
                          throw new ApiException(409, "任务已终结，不再查询路线", "TASK_TIMEOUT");
                        return amap.routeBetween(left, right, type, city);
                      },
                      revision == null);
              if (!unverified.isEmpty()) markUnverified(quality, unverified);
              quality.set("usage", data.path("usage"));
              complete(task, body, plan, quality, revision);
            }
          });
    } catch (Exception ex) {
      String code =
          closing
              ? "PROCESS_INTERRUPTED"
              : ex instanceof ApiException ae
                  ? ae.code
                  : ex instanceof InterruptedException ? "TASK_TIMEOUT" : "AGENT_CONNECTION_LOST";
      tasks.fail(id, execution, code, "规划未完成，请查看任务状态后重试");
      if (ex instanceof InterruptedException) Thread.currentThread().interrupt();
    }
  }

  private List<String> canonicalizePois(ObjectNode plan, String requestedCity) {
    var invalid = new ArrayList<String>();
    var cache = new HashMap<String, ObjectNode>();
    for (var day : plan.path("days")) {
      if (!day.path("attractions").isArray()) continue;
      var attractions = (tools.jackson.databind.node.ArrayNode) day.path("attractions");
      for (int index = attractions.size() - 1; index >= 0; index--) {
        JsonNode current = attractions.get(index);
        String id = current.path("poi_id").asText("");
        try {
          if (id.isBlank()) throw new ApiException(422, "POI ID 缺失");
          ObjectNode canonical = cache.computeIfAbsent(id, amap::detail);
          if (!cityMatches(requestedCity, canonical.path("city").asText(""))
              || canonical.path("location").path("longitude").asDouble(0) == 0
              || canonical.path("location").path("latitude").asDouble(0) == 0)
            throw new ApiException(422, "POI 城市或坐标无效");
          var normalized = (ObjectNode) current.deepCopy();
          normalized.put("poi_id", id);
          normalized.put("name", canonical.path("name").asText(""));
          normalized.put("address", canonical.path("address").asText(""));
          normalized.set("location", canonical.path("location").deepCopy());
          normalized.put("opening_hours", canonical.path("opening_hours").asText(""));
          normalized.put("fact_source", "amap_rest");
          normalized.remove("rating");
          normalized.remove("ticket_price");
          normalized.put("price_source", "unknown");
          if (canonical.path("photos").isArray() && !canonical.path("photos").isEmpty())
            normalized.put("image_url", canonical.path("photos").get(0).asText(""));
          else normalized.remove("image_url");
          attractions.set(index, normalized);
        } catch (Exception ex) {
          invalid.add(id.isBlank() ? "missing-poi-id" : id);
          attractions.remove(index);
        }
      }
    }
    return invalid;
  }

  private static String cityKey(String city) {
    return city == null ? "" : city.strip().replaceAll("(特别行政区|自治区|自治州|地区|盟|省|市)$", "");
  }

  private static boolean cityMatches(String requested, String actual) {
    String left = cityKey(requested), right = cityKey(actual);
    return !left.isBlank() && !right.isBlank() && (left.equals(right) || left.contains(right) || right.contains(left));
  }

  private void markUnverified(ObjectNode quality, List<String> invalid) {
    quality.put("outcome", "draft");
    var gaps = quality.withArray("data_gaps");
    gaps.add("agent_pois_rejected_by_java_rest:" + String.join(",", invalid));
    quality.withArray("issues").addObject()
        .put("code", "INVALID_POI").put("scope", "plan")
        .put("reason", "部分 Agent 候选无法由 Java 高德 REST 重新确认")
        .put("action", "重新选择可信景点后再确认行程")
        .put("blocking", true).put("retryable", true);
  }

  private void complete(
      Map<String, Object> task,
      ObjectNode body,
      ObjectNode plan,
      ObjectNode quality,
      ObjectNode revision) {
    boolean assistantPreview = revision != null && revision.path("assistant_preview").asBoolean(false);
    if (assistantPreview) {
      String validated = quality.path("outcome").asText("draft");
      quality.put("validated_outcome", validated);
      quality.put("outcome", "draft");
      quality.put("assistant_confirmation_required", true);
      quality.put("assistant_conversation_id", revision.path("conversation_id").asText(""));
      quality.withArray("issues").addObject()
          .put("code", "ASSISTANT_CONFIRMATION_REQUIRED").put("scope", "plan")
          .put("reason", "旅行助手方案等待用户确认")
          .put("action", "查看 Java 校验结果与差异后确认保存")
          .put("blocking", true).put("retryable", false);
    }
    boolean draft = quality.path("outcome").asText("").equals("draft");
    if (plan.path("days").valueStream().allMatch(d -> d.path("attractions").isEmpty()))
      throw new ApiException(503, "暂无可信景点", "TRUSTED_POI_UNAVAILABLE");
    task.put("status", draft ? "needs_attention" : "succeeded");
    task.put("message", assistantPreview ? "助手方案已通过 Java 校验，等待用户确认"
        : draft ? "已保存未完成草稿，请查看缺口并调整" : "旅行计划已生成并保存");
    if (draft && revision != null) quality.set("revision_parent", revision);
    tx.executeWithoutResult(
        status -> {
          if (tasks.finish(task) != 1) {
            status.setRollbackOnly();
            return;
          }
          long uid = ((Number) task.get("user_id")).longValue(), recordId;
          if (revision != null && !draft) {
            recordId = revision.path("record_id").asLong(0);
            if (history.update(
                    Map.of(
                        "id",
                        recordId,
                        "user_id",
                        uid,
                        "version",
                        revision.path("version").asInt(0),
                        "plan_json",
                        json.writeValueAsString(plan),
                        "quality_json",
                        json.writeValueAsString(quality)))
                != 1) throw new ApiException(409, "行程版本冲突", "VERSION_CONFLICT");
          } else {
            var record = new HashMap<String, Object>();
            for (String f :
                List.of(
                    "city",
                    "start_date",
                    "end_date",
                    "transportation",
                    "accommodation",
                    "free_text_input")) record.put(f, body.path(f).asText(""));
            record.put("travel_days", body.path("travel_days").asInt(0));
            record.put("preferences", json.writeValueAsString(body.path("preferences")));
            record.put("user_id", uid);
            record.put("plan_json", json.writeValueAsString(plan));
            record.put("quality_json", json.writeValueAsString(quality));
            record.put("title", body.path("city").asText("") + "旅行计划");
            record.put("source", assistantPreview ? "assistant_revision" : "agent");
            record.put("last_verified_at", java.sql.Timestamp.from(java.time.Instant.now()));
            history.insert(record);
            recordId = ((Number) record.get("id")).longValue();
          }
          if (!draft) history.outbox(recordId, uid, "upsert");
          tasks.attach(task.get("id").toString(), recordId);
        });
  }
}
