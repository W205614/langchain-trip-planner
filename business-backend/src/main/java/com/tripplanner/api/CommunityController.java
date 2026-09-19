package com.tripplanner.api;

import com.tripplanner.domain.BusinessAuditService;
import com.tripplanner.domain.BusinessTypes;
import com.tripplanner.domain.TripLedgerService;
import com.tripplanner.persistence.CommunityMapper;
import com.tripplanner.persistence.HistoryMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.util.*;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
public class CommunityController {
  private final CommunityMapper community;
  private final HistoryMapper history;
  private final JsonMapper json;
  private final TransactionTemplate tx;
  private final TripLedgerService ledger;
  private final BusinessAuditService audit;

  public CommunityController(
      CommunityMapper community,
      HistoryMapper history,
      JsonMapper json,
      TransactionTemplate tx,
      TripLedgerService ledger,
      BusinessAuditService audit) {
    this.community = community;
    this.history = history;
    this.json = json;
    this.tx = tx;
    this.ledger = ledger;
    this.audit = audit;
  }

  @PostMapping("/api/community/cards")
  public Object submit(HttpServletRequest req, @RequestBody ObjectNode body) {
    long uid = UsersController.uid(req);
    long recordId = body.path("record_id").asLong(0);
    var record = history.owned(uid, recordId);
    if (record == null) throw new ApiException(404, "行程不存在");
    String title = body.path("title").asText(Objects.toString(record.get("title"), "")).strip();
    if (title.isEmpty()) title = Objects.toString(record.get("city"), "旅行") + "行程";
    if (title.length() > 160) throw new ApiException(422, "标题不能超过160个字符");

    var snapshot = json.createObjectNode();
    for (String field : List.of("departure_city", "city", "start_date", "end_date", "travel_days",
        "transportation", "accommodation", "traveler_count", "room_count", "budget_total"))
      snapshot.set(field, json.valueToTree(record.get(field)));
    snapshot.set("plan", json.readTree(record.get("plan_json").toString()));
    var quality = json.readTree(record.get("quality_json").toString());
    snapshot.put("outcome", quality.path("outcome").asText("unassessed"));

    var row = new HashMap<String, Object>();
    row.put("record_id", recordId);
    row.put("owner_id", uid);
    row.put("record_version", record.get("version"));
    row.put("title", title);
    row.put("city", record.get("city"));
    row.put("snapshot_json", json.writeValueAsString(snapshot));
    try {
      tx.executeWithoutResult(status -> {
        community.insert(row);
        audit.success(uid, "community.submit", "community_trip_card", row.get("id"),
            ((Number) record.get("version")).intValue(), req.getHeader("X-Request-ID"),
            Map.of("record_id", recordId));
      });
    } catch (DuplicateKeyException ex) {
      throw new ApiException(409, "当前行程版本已投稿；修改行程后可提交新版本");
    }
    return Map.of("success", true, "id", row.get("id"), "status", "pending", "message", "已提交审核");
  }

  @GetMapping("/api/community/cards")
  public Object list(@RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "12") int pageSize) {
    int safePage = Math.max(1, page);
    int size = Math.max(1, Math.min(30, pageSize));
    var data = community.published((safePage - 1) * size, size).stream().map(this::publicRow).toList();
    return Map.of("success", true, "data", data,
        "total", community.publishedCount(), "page", safePage, "page_size", size);
  }

  @GetMapping("/api/community/cards/{id}")
  public Object detail(@PathVariable long id) {
    var row = community.publishedById(id);
    if (row == null) throw new ApiException(404, "公开行程不存在");
    return Map.of("success", true, "data", publicRow(row));
  }

  @PostMapping("/api/community/cards/{id}/copy")
  public Object copy(HttpServletRequest req, @PathVariable long id) {
    long uid = UsersController.uid(req);
    var card = community.publishedById(id);
    if (card == null) throw new ApiException(404, "公开行程不存在");
    JsonNode snapshot = json.readTree(card.get("snapshot_json").toString());
    var record = new HashMap<String, Object>();
    record.put("user_id", uid);
    record.put("title", card.get("title") + "（社区副本）");
    record.put("source", BusinessTypes.TripSource.COPIED.wire());
    for (String field : List.of("departure_city", "city", "start_date", "end_date", "transportation", "accommodation"))
      record.put(field, snapshot.path(field).asText(""));
    for (String field : List.of("travel_days", "traveler_count", "room_count"))
      record.put(field, snapshot.path(field).asInt(1));
    record.put("budget_total", snapshot.path("budget_total").isNumber() ? snapshot.path("budget_total").asInt() : null);
    record.put("preferences", "[]");
    record.put("free_text_input", "");
    record.put("plan_json", json.writeValueAsString(snapshot.path("plan")));
    var quality = json.createObjectNode().put("outcome", "draft");
    quality.putArray("data_gaps").add("copied_community_plan_requires_reverification");
    record.put("quality_json", json.writeValueAsString(quality));
    record.put("last_verified_at", null);
    tx.executeWithoutResult(status -> {
      history.insert(record);
      long copiedId = ((Number) record.get("id")).longValue();
      history.outbox(copiedId, uid, "delete");
      ledger.capture(uid, copiedId, "copied_create", req.getHeader("X-Request-ID"));
      audit.success(uid, "community.copy", "community_trip_card", id, null,
          req.getHeader("X-Request-ID"), Map.of("copied_record_id", copiedId));
    });
    return Map.of("success", true, "id", record.get("id"), "version", 1,
        "message", "已复制为待重新核验的个人行程");
  }

  @GetMapping("/api/community/admin/cards")
  public Object queue(@RequestParam(defaultValue = "pending") String status) {
    if (!Set.of("", "pending", "published", "rejected").contains(status))
      throw new ApiException(422, "审核状态无效");
    var data = community.reviewQueue(status, 100).stream().map(this::reviewRow).toList();
    return Map.of("success", true, "data", data);
  }

  @PostMapping("/api/community/admin/cards/{id}/review")
  public Object review(HttpServletRequest req, @PathVariable long id, @RequestBody ObjectNode body) {
    long uid = UsersController.uid(req);
    String decision = body.path("decision").asText("");
    String status = switch (decision) {
      case "approve" -> "published";
      case "reject" -> "rejected";
      default -> throw new ApiException(422, "审核决定必须是 approve 或 reject");
    };
    String note = body.path("note").asText("").strip();
    if (note.length() > 500) throw new ApiException(422, "审核备注不能超过500个字符");
    tx.executeWithoutResult(transaction -> {
      if (community.review(id, status, note, uid) == 0)
        throw new ApiException(409, "投稿不存在或已经审核");
      audit.success(uid, "community.review." + decision, "community_trip_card", id, null,
          req.getHeader("X-Request-ID"), Map.of("note", note));
    });
    return Map.of("success", true, "status", status);
  }

  private Map<String, Object> publicRow(Map<String, Object> row) {
    var result = new LinkedHashMap<String, Object>();
    for (String field : List.of("id", "record_version", "title", "city", "author", "published_at"))
      result.put(field, row.get(field));
    result.put("snapshot", json.readTree(row.get("snapshot_json").toString()));
    return result;
  }

  private Map<String, Object> reviewRow(Map<String, Object> row) {
    var result = new LinkedHashMap<>(row);
    result.remove("snapshot_json");
    result.put("snapshot", json.readTree(row.get("snapshot_json").toString()));
    return result;
  }
}
