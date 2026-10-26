package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.HistoryMapper;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Shared authorization and transactional row-lock policy for trip operations. */
@Service
public class TripOperationAccess {
  final JdbcTemplate db;
  final JsonMapper json;
  private final PlanningInputService planning;
  public TripOperationAccess(JdbcTemplate db,JsonMapper json,PlanningInputService planning){
    this.db=db;this.json=json;this.planning=planning;
  }
  Map<String,Object> row(String sql, Object... args) {
    var rows=db.queryForList(sql,args);
    return rows.isEmpty()?null:rows.getFirst();
  }

  Map<String,Object> trip(long id) {
    var found=row("SELECT * FROM trip_records WHERE id=?",id);
    if(found==null) throw new ApiException(404,"行程不存在");
    return planning.view(found);
  }

  Map<String,Object> regular(Map<String,Object> found) {
    if(!json.readTree(found.get("quality_json").toString()).path("assistant_proposal_status").asText("").isBlank())
      throw new ApiException(409,"助手方案不能作为执行行程，请先确认原行程");
    return found;
  }

  Map<String,Object> access(long actor,long id,boolean edit) {
    var found=trip(id);
    long owner=((Number)found.get("user_id")).longValue();
    if(owner==actor) return regular(found);
    var member=row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=?",id,actor);
    if(member==null || !"accepted".equals(member.get("status")) ||
        (edit && !"editor".equals(member.get("role"))))
      throw new ApiException(404,"行程不存在或无权访问");
    return regular(found);
  }

  Map<String,Object> owned(long actor,long id) {
    var found=trip(id);
    if(((Number)found.get("user_id")).longValue()!=actor)
      throw new ApiException(404,"行程不存在或无权访问");
    return regular(found);
  }

  /** Keeps an accepted editor row locked until the business write commits. */
  void writeAccessLocked(long actor,long id) {
    var found=trip(id);
    if(((Number)found.get("user_id")).longValue()==actor) {regular(found);return;}
    var member=row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=? FOR SHARE",id,actor);
    if(member==null || !"accepted".equals(member.get("status")) || !"editor".equals(member.get("role")))
      throw new ApiException(404,"行程不存在或无权访问");
    regular(found);
  }

  void version(Map<String,Object> trip,int expected) {
    if(expected<1 || ((Number)trip.get("version")).intValue()!=expected)
      throw new ApiException(409,"行程版本冲突，请重新加载","VERSION_CONFLICT");
  }

  int day(Map<String,Object> trip, JsonNode payload) {
    int index=payload.path("day_index").asInt(-1);
    if(index<0 || index>=((Number)trip.get("travel_days")).intValue())
      throw new ApiException(422,"目标日期无效");
    return index;
  }

  String required(JsonNode body,String field,int max) {
    String value=body.path(field).asText("").strip();
    if(value.isEmpty() || value.length()>max) throw new ApiException(422,field+"无效");
    return value;
  }

  String optional(JsonNode body,String field,int max) {
    String value=body.path(field).asText("").strip();
    if(value.length()>max) throw new ApiException(422,field+"过长");
    return value;
  }

  String choice(JsonNode body,String field,Set<String> allowed) {
    String value=body.path(field).asText("");
    if(!allowed.contains(value)) throw new ApiException(422,field+"无效");
    return value;
  }

  Long cents(JsonNode body,boolean required) {
    JsonNode node=body.path("amount");
    if(node.isMissingNode() || node.isNull()) {
      if(required) throw new ApiException(422,"金额不能为空");
      return null;
    }
    try {
      BigDecimal value=new BigDecimal(node.asText()).setScale(2,RoundingMode.UNNECESSARY);
      if(value.signum()<(required?1:0) || value.compareTo(new BigDecimal("10000000"))>0)
        throw new ApiException(422,"金额超出范围");
      return value.movePointRight(2).longValueExact();
    } catch(NumberFormatException | ArithmeticException ex) {
      throw new ApiException(422,"金额必须精确到分");
    }
  }

  String key(String key) {
    if(key==null || !key.matches("[A-Za-z0-9_-]{8,128}"))
      throw new ApiException(422,"Idempotency-Key 无效");
    return key;
  }

  String fingerprint(JsonNode body) {
    try {
      byte[] digest=MessageDigest.getInstance("SHA-256").digest(json.writeValueAsBytes(body));
      return HexFormat.of().formatHex(digest);
    } catch(Exception ex) { throw new IllegalStateException(ex); }
  }

}
