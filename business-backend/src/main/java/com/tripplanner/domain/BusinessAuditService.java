package com.tripplanner.domain;

import com.tripplanner.persistence.AuditMapper;
import java.util.*;
import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;

@Service
public class BusinessAuditService {
  private final AuditMapper audits;
  private final JsonMapper json;

  public BusinessAuditService(AuditMapper audits, JsonMapper json) {
    this.audits = audits;
    this.json = json;
  }

  public void success(
      Long actorId,
      String action,
      String resourceType,
      Object resourceId,
      Integer version,
      String requestId) {
    success(actorId, action, resourceType, resourceId, version, requestId, Map.of());
  }

  public void success(
      Long actorId,
      String action,
      String resourceType,
      Object resourceId,
      Integer version,
      String requestId,
      Map<String, ?> metadata) {
    var event = new HashMap<String, Object>();
    event.put("request_id", safeRequestId(requestId));
    event.put("actor_type", actorId == null ? "system" : "user");
    event.put("actor_id", actorId);
    event.put("action", action);
    event.put("resource_type", resourceType);
    event.put("resource_id", Objects.toString(resourceId, ""));
    event.put("resource_version", version);
    event.put("outcome", "success");
    event.put("metadata_json", json.writeValueAsString(metadata));
    audits.insert(event);
  }

  public static String safeRequestId(String requestId) {
    return requestId != null && requestId.matches("[A-Za-z0-9_-]{1,64}") ? requestId : "";
  }
}
