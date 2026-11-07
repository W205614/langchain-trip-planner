package com.tripplanner.domain;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import tools.jackson.databind.JsonNode;

/** Java registers the authenticated actor; Python can only reference the opaque operation. */
@Service
public class OutboundActorContext {
  private final JdbcTemplate db;
  public OutboundActorContext(JdbcTemplate db) { this.db = db; }

  public String forAgent(String path, Object body) {
    if (path.endsWith("/cancel") || path.equals("/capabilities/status") || path.equals("/capabilities/eval-policy")
        || path.equals("/capabilities/rag-status") || path.equals("/executions") || path.equals("/v2/executions")) return null;
    String scoped=org.slf4j.MDC.get("outbound_operation");
    if(scoped!=null)return UUID.fromString(scoped).toString();
    String purpose = path.contains("research") ? "model" : path.contains("document") ? "document"
        : path.contains("history") ? "history" : path.contains("rebuild") ? "rebuild" : "map";
    if (RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attrs) {
      var req = attrs.getRequest();
      Object user = req.getAttribute("user");
      Long uid = user instanceof Map<?,?> values ? ((Number)values.get("id")).longValue() : null;
      String cache = "outbound.operation." + purpose;
      if (req.getAttribute(cache) instanceof String id) return id;
      String id = register(uid == null ? "anonymous" : "user", uid, purpose);
      req.setAttribute(cache,id);
      return id;
    }
    Long uid = null;
    Object document = field(body,"document_id"), record = field(body,"record_id");
    if (document instanceof Number number) {
      var owners=db.queryForList("SELECT submitted_by FROM knowledge_documents WHERE id=?",number.longValue());
      if (!owners.isEmpty()) uid=((Number)owners.getFirst().get("submitted_by")).longValue();
    } else if (record instanceof Number number) {
      var owners=db.queryForList("SELECT user_id FROM trip_records WHERE id=?",number.longValue());
      if (!owners.isEmpty()) uid=((Number)owners.getFirst().get("user_id")).longValue();
    }
    return register(uid==null?"system":"user",uid,purpose);
  }

  private static Object field(Object body,String name) {
    if (body instanceof Map<?,?> values) return values.get(name);
    if (body instanceof JsonNode node && node.path(name).isIntegralNumber()) return node.path(name).asLong();
    return null;
  }

  public String register(String kind,Long uid,String purpose) {
    UUID id=UUID.randomUUID();
    db.update("DELETE FROM outbound_operations WHERE id IN (SELECT id FROM outbound_operations WHERE expires_at<timezone('UTC',now())-interval '1 day' LIMIT 100)");
    db.update("INSERT INTO outbound_operations(id,actor_kind,actor_id,purpose,expires_at) VALUES(?,?,?,?,?)",
        id,kind,uid,purpose,java.time.LocalDateTime.ofInstant(Instant.now().plusSeconds(900),java.time.ZoneOffset.UTC));
    return id.toString();
  }
}
