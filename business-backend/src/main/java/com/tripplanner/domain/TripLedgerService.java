package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.*;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/** Immutable trip versions plus deletion/restore lifecycle owned by Java. */
@Service
public class TripLedgerService {
  private final HistoryMapper history;
  private final TripVersionMapper versions;
  private final ShareMapper shares;
  private final AssistantMapper conversations;
  private final BusinessAuditService audit;
  private final TransactionTemplate tx;
  private final JsonMapper json;

  public TripLedgerService(
      HistoryMapper history,
      TripVersionMapper versions,
      ShareMapper shares,
      AssistantMapper conversations,
      BusinessAuditService audit,
      TransactionTemplate tx,
      JsonMapper json) {
    this.history = history;
    this.versions = versions;
    this.shares = shares;
    this.conversations = conversations;
    this.audit = audit;
    this.tx = tx;
    this.json = json;
  }

  public void capture(long userId, long recordId, String changeType, String requestId) {
    captureOnBehalf(userId, userId, recordId, changeType, requestId);
  }

  public void captureOnBehalf(long ownerId, long actorId, long recordId, String changeType, String requestId) {
    if (versions.capture(ownerId, recordId, changeType, BusinessAuditService.safeRequestId(requestId)) != 1)
      throw new IllegalStateException("Trip version capture lost its source record");
    var row = history.owned(ownerId, recordId);
    int version = ((Number) row.get("version")).intValue();
    audit.success(actorId, "trip." + changeType, "trip", recordId, version, requestId);
  }

  public Map<String, Object> list(long userId, long recordId, int page, int pageSize) {
    requireOwned(userId, recordId);
    if (page < 1 || pageSize < 1 || pageSize > 50) throw new ApiException(422, "分页参数无效");
    return Map.of(
        "success", true,
        "data", versions.list(userId, recordId, (page - 1) * pageSize, pageSize),
        "total", versions.count(userId, recordId),
        "page", page,
        "page_size", pageSize);
  }

  public Map<String, Object> get(long userId, long recordId, int version) {
    requireOwned(userId, recordId);
    var row = versions.owned(userId, recordId, version);
    if (row == null) throw new ApiException(404, "行程版本不存在");
    var result = new LinkedHashMap<>(row);
    result.put("plan", json.readTree(result.remove("plan_json").toString()));
    result.put("quality", json.readTree(result.remove("quality_json").toString()));
    result.remove("user_id");
    return Map.of("success", true, "data", result);
  }

  public Map<String, Object> restore(
      long userId, long recordId, int currentVersion, int sourceVersion, String requestId) {
    if (currentVersion < 1 || sourceVersion < 1) throw new ApiException(422, "版本无效");
    return tx.execute(
        status -> {
          var current = requireOwned(userId, recordId);
          if (((Number) current.get("version")).intValue() != currentVersion)
            throw new ApiException(409, "行程版本冲突，请重新加载", "VERSION_CONFLICT");
          var source = versions.owned(userId, recordId, sourceVersion);
          if (source == null) throw new ApiException(404, "行程版本不存在");
          if (history.update(
                  Map.of(
                      "id", recordId,
                      "user_id", userId,
                      "version", currentVersion,
                      "plan_json", source.get("plan_json"),
                      "quality_json", source.get("quality_json")))
              != 1) throw new ApiException(409, "行程版本冲突，请重新加载", "VERSION_CONFLICT");
          var quality = json.readTree(source.get("quality_json").toString());
          history.outbox(
              recordId,
              userId,
              "draft".equals(quality.path("outcome").asText("")) ? "delete" : "upsert");
          capture(userId, recordId, "restore", requestId);
          return Map.of(
              "success", true,
              "id", recordId,
              "version", currentVersion + 1,
              "restored_from", sourceVersion,
              "data", json.readTree(source.get("plan_json").toString()),
              "quality", quality,
              "rag_sync_pending", true);
        });
  }

  public void delete(long userId, long recordId, String requestId) {
    tx.executeWithoutResult(
        status -> {
          var row = requireOwned(userId, recordId);
          int version = ((Number) row.get("version")).intValue();
          shares.revokeAll(userId, recordId);
          conversations.detachTrip(userId, recordId);
          history.outbox(recordId, userId, "delete");
          audit.success(userId, "trip.delete", "trip", recordId, version, requestId);
          if (history.delete(userId, recordId) != 1) throw new ApiException(409, "行程删除冲突");
        });
  }

  private Map<String, Object> requireOwned(long userId, long recordId) {
    var row = history.owned(userId, recordId);
    if (row == null) throw new ApiException(404, "行程不存在");
    return row;
  }
}
