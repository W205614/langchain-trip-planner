package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.ShareMapper;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

@Service
public class TripSuggestionService {
  private final TripOperationAccess access;
  private final ShareMapper shares;
  private final TransactionTemplate tx;
  private final UserQuotaService users;
  private final PlanningInputService planning;
  private final TripLedgerService ledger;
  private final BusinessAuditService audit;

  public TripSuggestionService(TripOperationAccess access, ShareMapper shares, TransactionTemplate tx,
      UserQuotaService users, PlanningInputService planning, TripLedgerService ledger, BusinessAuditService audit) {
    this.access=access;
    this.shares=shares;
    this.tx=tx;
    this.users=users;
    this.planning=planning;
    this.ledger=ledger;
    this.audit=audit;
  }

  public ArrayNode snapshotTargets(JsonNode plan, JsonNode publicPlan) {
    var targets=access.json.createArrayNode();
    for (int dayIndex=0;dayIndex<plan.path("days").size();dayIndex++) {
      var day=plan.path("days").get(dayIndex);
      var publicDay=publicPlan.path("days").get(dayIndex);
      var timeline=day.path("timeline");
      for (int index=0;index<timeline.size();index++) {
        var item=timeline.get(index);
        String activity=item.path("activity_id").asText("");
        var start=timestamp(item,"start_at");
        var end=timestamp(item,"end_at");
        if (!Set.of("attraction","meal").contains(item.path("kind").asText(""))
            || activity.isBlank() || start==null || end==null || !end.isAfter(start)) continue;
        targets.addObject().put("day_index",dayIndex).put("item_index",index)
            .put("activity_id",activity).put("old_start",start.toString()).put("old_end",end.toString())
            .put("title",publicDay.path("timeline").get(index).path("title").asText());
      }
    }
    return targets;
  }

  public Object submit(long author, String tokenHash, ObjectNode body, String requestId) {
    String kind=access.choice(body,"kind",Set.of("shift_time","alternative"));
    int index=index(body,"day_index");
    String reason=text(body,"reason",500,true);
    return tx.execute(status -> {
      users.lockActive(author);
      var share=shares.forSuggestion(tokenHash);
      if (share==null) throw new ApiException(404,"建议链接不存在、未开放或已失效");
      if (((Number)share.get("owner_id")).longValue()==author)
        throw new ApiException(422,"请在自己的行程中直接编辑");
      var snapshot=access.json.readTree(share.get("snapshot_json").toString());
      var day=snapshot.path("plan").path("days").get(index);
      if (day==null) throw new ApiException(422,"建议日期无效");
      var date=LocalDate.parse(day.path("date").asText());
      var detail=kind.equals("shift_time")?shiftDetails(snapshot,body,index,date):
          access.json.createObjectNode().put("location",text(body,"location",120,false));
      long id=access.db.queryForObject("""
          INSERT INTO trip_suggestions(trip_id,share_id,author_id,kind,day_index,trip_date,base_version,details,reason)
          VALUES(?,?,?,?,?,?,?,?::jsonb,?) RETURNING id
          """,Long.class,share.get("record_id"),share.get("id"),author,kind,index,date,
          share.get("record_version"),access.json.writeValueAsString(detail),reason);
      audit.success(author,"trip.suggestion_submit","trip_suggestion",id,null,requestId);
      return Map.of("success",true,"id",id);
    });
  }

  public Object list(long owner, long tripId, int page) {
    access.owned(owner,tripId);
    if (page<1 || page>10000) throw new ApiException(422,"分页参数无效");
    var rows=access.db.queryForList("""
        SELECT s.id,s.kind,s.day_index,s.trip_date,s.base_version,s.details::text AS details,
          s.reason,s.status,s.applied_version,s.created_at,s.decided_at,u.username AS author
        FROM trip_suggestions s JOIN users u ON u.id=s.author_id WHERE s.trip_id=?
        ORDER BY (s.status='pending') DESC, s.id DESC LIMIT 21 OFFSET ?
        """,tripId,(page-1)*20);
    var visible=rows.stream().limit(20).toList();
    for (var row:visible) {
      var details=(ObjectNode)access.json.readTree(row.get("details").toString());
      details.remove("activity_id");
      row.put("details",details);
    }
    return Map.of("success",true,"data",visible,"has_more",rows.size()>20);
  }

  public Object decide(long owner, long tripId, long suggestionId, ObjectNode body, String requestId) {
    String decision=access.choice(body,"decision",Set.of("applied","rejected"));
    return tx.execute(status -> {
      var trip=access.row("SELECT * FROM trip_records WHERE id=? AND user_id=? FOR NO KEY UPDATE",tripId,owner);
      if (trip==null) throw new ApiException(404,"行程不存在或无权访问");
      access.regular(trip);
      var suggestion=access.row("SELECT * FROM trip_suggestions WHERE id=? AND trip_id=? FOR UPDATE",suggestionId,tripId);
      if (suggestion==null) throw new ApiException(404,"同行建议不存在");
      if (decision.equals(suggestion.get("status"))) return decision(suggestion);
      if (!"pending".equals(suggestion.get("status"))) throw new ApiException(409,"该建议已处理");
      Integer appliedVersion=null;
      if (decision.equals("applied")) {
        var plan=PlanningInputService.stableIds((ObjectNode)access.json.readTree(trip.get("plan_json").toString()));
        int dayIndex=((Number)suggestion.get("day_index")).intValue();
        var date=LocalDate.parse(suggestion.get("trip_date").toString());
        if (!(plan.path("days").get(dayIndex) instanceof ObjectNode day)
            || !date.toString().equals(day.path("date").asText())) throw stale();
        if ("shift_time".equals(suggestion.get("kind"))) {
          int version=((Number)trip.get("version")).intValue();
          appliedVersion=TripRequests.nextVersion(version);
          var original=plan.deepCopy();
          var details=access.json.readTree(suggestion.get("details").toString());
          adjust(day,details);
          var quality=(ObjectNode)access.json.readTree(trip.get("quality_json").toString());
          planning.invalidateManualSchedule(original,plan,quality,Set.of(dayIndex));
          ledger.save(owner,tripId,version,plan,quality,"user_edit",requestId);
        }
      }
      access.db.update("""
          UPDATE trip_suggestions SET status=?,applied_version=?,decided_at=timezone('UTC',now())
          WHERE id=?
          """,decision,appliedVersion,suggestionId);
      audit.success(owner,"trip.suggestion_"+decision,"trip_suggestion",suggestionId,appliedVersion,requestId);
      suggestion.put("status",decision);
      suggestion.put("applied_version",appliedVersion);
      return decision(suggestion);
    });
  }

  private ObjectNode shiftDetails(JsonNode snapshot,ObjectNode body,int dayIndex,LocalDate date) {
    int itemIndex=index(body,"item_index");
    String time=text(body,"start_time",5,false);
    if (!time.matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]")) throw new ApiException(422,"开始时间格式无效");
    for (var target:snapshot.path("_suggestion_targets")) {
      if (target.path("day_index").asInt()!=dayIndex || target.path("item_index").asInt()!=itemIndex) continue;
      var start=timestamp(target,"old_start");
      var end=timestamp(target,"old_end");
      var next=OffsetDateTime.of(date,LocalTime.parse(time),start.getOffset());
      var nextEnd=next.plus(Duration.between(start,end));
      if (next.isEqual(start) || !nextEnd.toLocalDate().equals(date))
        throw new ApiException(422,"新时段必须有变化并保持在同一天");
      return access.json.createObjectNode().put("activity_id",target.path("activity_id").asText())
          .put("title",target.path("title").asText()).put("old_start",start.toString()).put("old_end",end.toString())
          .put("new_start",next.toString()).put("new_end",nextEnd.toString());
    }
    throw new ApiException(422,"这项活动没有可调整的时间");
  }

  private static void adjust(ObjectNode day,JsonNode detail) {
    String activity=detail.path("activity_id").asText();
    var timeline=(ArrayNode)day.path("timeline");
    var targets=timeline.valueStream().filter(item -> activity.equals(item.path("activity_id").asText())).toList();
    if (targets.size()!=1 || !(targets.getFirst() instanceof ObjectNode target)
        || !timestamp(detail,"old_start").equals(timestamp(target,"start_at"))
        || !timestamp(detail,"old_end").equals(timestamp(target,"end_at"))) throw stale();
    var start=timestamp(detail,"new_start");
    var end=timestamp(detail,"new_end");
    var adjusted=new ArrayList<JsonNode>();
    for (var item:timeline) {
      if (item!=target && overlaps(item,start,end)) {
        if ("waiting".equals(item.path("kind").asText())) continue;
        throw new ApiException(409,"建议时段与当前行程中的其他安排重叠","SUGGESTION_TIME_CONFLICT");
      }
      adjusted.add(item);
    }
    target.put("start_at",start.toString()).put("end_at",end.toString());
    adjusted.sort(Comparator.comparing(item -> timestamp(item,"start_at"),
        Comparator.nullsLast(Comparator.naturalOrder())));
    timeline.removeAll();
    adjusted.forEach(timeline::add);
  }

  private static boolean overlaps(JsonNode item, OffsetDateTime start, OffsetDateTime end) {
    var otherStart=timestamp(item,"start_at");
    var otherEnd=timestamp(item,"end_at");
    return otherStart!=null && otherEnd!=null && start.isBefore(otherEnd) && end.isAfter(otherStart);
  }

  private static OffsetDateTime timestamp(JsonNode node,String field) {
    try { return OffsetDateTime.parse(node.path(field).asText("")); }
    catch (DateTimeParseException invalid) { return null; }
  }

  private static int index(JsonNode body,String field) {
    var value=body.path(field);
    if (!value.isIntegralNumber() || !value.canConvertToInt() || value.asInt()<0)
      throw new ApiException(422,"建议位置无效");
    return value.asInt();
  }

  private static String text(JsonNode body,String field,int limit,boolean optional) {
    var node=body.path(field);
    if (optional && node.isMissingNode()) return "";
    String value=node.asText("").strip();
    if (!node.isString() || value.length()>limit || (!optional && value.isBlank()))
      throw new ApiException(422,"建议内容格式或长度不符合要求");
    return value;
  }

  private static ApiException stale() {
    return new ApiException(409,"目标行程项已修改或删除，请核对后重新发起建议","SUGGESTION_STALE");
  }

  private static Object decision(Map<String,Object> suggestion) {
    var version=suggestion.get("applied_version");
    return version==null? Map.of("success",true,"status",suggestion.get("status")):
        Map.of("success",true,"status",suggestion.get("status"),"version",version);
  }
}
