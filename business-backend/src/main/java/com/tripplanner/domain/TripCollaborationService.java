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

/** Accepted-member collaboration and versioned shared itinerary edits. */
@Service
public class TripCollaborationService {
  private final TripOperationAccess ctx;
  private final JdbcTemplate db;
  private final JsonMapper json;
  private final TransactionTemplate tx;
  private final BusinessAuditService audit;
  private final HistoryMapper history;
  private final PlanRules rules;
  private final TripLedgerService ledger;
  private final PlanningInputService planning;
  private final TripNotificationUsageService notifications;
  public TripCollaborationService(TripOperationAccess ctx,TransactionTemplate tx,BusinessAuditService audit,
      HistoryMapper history,PlanRules rules,TripLedgerService ledger,PlanningInputService planning,TripNotificationUsageService notifications){
    this.ctx=ctx;this.db=ctx.db;this.json=ctx.json;this.tx=tx;this.audit=audit;
    this.history=history;this.rules=rules;this.ledger=ledger;this.planning=planning;this.notifications=notifications;
  }
  public Object workspace(long actor,long id) {
    var trip=ctx.access(actor,id,false);
    var result=new LinkedHashMap<String,Object>();
    for(String f:List.of("id","city","start_date","end_date","travel_days","version","title","budget_total"))
      result.put(f,trip.get(f));
    result.put("plan",PlanningInputService.stableIds((ObjectNode)json.readTree(trip.get("plan_json").toString())));
    result.put("planning_revision",trip.get("planning_revision"));
    result.put("quality",json.readTree(trip.get("quality_json").toString()));
    result.put("viewer_id",actor);
    if(((Number)trip.get("user_id")).longValue()==actor) result.put("role","owner");
    else {
      var membership=ctx.row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=?",id,actor);
      if(membership==null || !"accepted".equals(membership.get("status")))
        throw new ApiException(404,"行程不存在或无权访问");
      result.put("role",membership.get("role"));
    }
    return Map.of("success",true,"data",result);
  }

  public Object reorder(long actor,long id,int expected,ObjectNode input,String requestId) {
    var found=ctx.access(actor,id,true);
    ctx.version(found,expected);
    int dayIndex=ctx.day(found,input);
    JsonNode ids=input.path("poi_ids");
    if(!ids.isArray() || ids.size()>20) throw new ApiException(422,"景点顺序无效");
    ObjectNode plan=(ObjectNode)json.readTree(found.get("plan_json").toString());
    JsonNode target=plan.path("days").get(dayIndex);
    if(target==null || !target.isObject()) throw new ApiException(422,"行程日期无效");
    var original=new LinkedHashMap<String,JsonNode>();
    for(JsonNode a:target.path("attractions")) original.put(a.path("poi_id").asText(""),a);
    if(ids.size()!=original.size()) throw new ApiException(422,"只能重排当天既有景点");
    var seen=new HashSet<String>();
    var reordered=json.createArrayNode();
    for(JsonNode node:ids) {
      String poi=node.asText("");
      if(!seen.add(poi) || !original.containsKey(poi)) throw new ApiException(422,"只能重排当天既有景点");
      reordered.add(original.get(poi).deepCopy());
    }
    ((ObjectNode)target).set("attractions",reordered);
    if(input.has("description")) ((ObjectNode)target).put("description",ctx.optional(input,"description",500));
    var request=json.createObjectNode();
    for(String f:List.of("departure_city","city","start_date","end_date","travel_days","transportation",
        "accommodation","traveler_count","room_count","budget_total","free_text_input"))
      request.set(f,json.valueToTree(found.get(f)));
    request.set("preferences",json.readTree(found.get("preferences").toString()));
    request.set("constraints",plan.path("constraints").isObject()?plan.path("constraints").deepCopy():json.createObjectNode());
    ObjectNode quality=rules.finish(plan,TripRequests.normalize(request),null,false);
    var before=json.readTree(found.get("plan_json").toString());
    var updatedDays=(ArrayNode)plan.path("days");
    for(int i=0;i<updatedDays.size();i++)if(i!=dayIndex)updatedDays.set(i,before.path("days").get(i).deepCopy());
    planning.invalidateManualSchedule(before,plan,quality,Set.of(dayIndex));
    quality.withArray("data_gaps").add("shared_reorder_route_unverified");
    long owner=((Number)found.get("user_id")).longValue();
    return tx.execute(s -> {
      ctx.writeAccessLocked(actor,id);
      planning.protect(owner,id,plan,json.createObjectNode(),false);
      if(history.update(Map.of("id",id,"user_id",owner,"version",expected,
          "plan_json",json.writeValueAsString(plan),"quality_json",json.writeValueAsString(quality)))!=1)
        throw new ApiException(409,"行程版本冲突，请重新加载","VERSION_CONFLICT");
      history.outbox(id,owner,"draft".equals(quality.path("outcome").asText(""))?"delete":"upsert");
      ledger.captureOnBehalf(owner,actor,id,"user_edit",requestId);
      notifications.notifyUser(owner,id,"reorder:"+id+":"+(expected+1),"trip_change","同行人调整了行程","第"+(dayIndex+1)+"天顺序已变更，请核对路线。");
      return Map.of("success",true,"id",id,"version",expected+1,"quality",quality,"data",plan);
    });
  }

  public Object invite(long actor,long id,ObjectNode body,String requestId) {
    ctx.owned(actor,id);
    String username=ctx.required(body,"username",64);
    String role=ctx.choice(body,"role",Set.of("viewer","editor"));
    var invited=ctx.row("SELECT id FROM users WHERE username=?",username);
    if(invited==null) throw new ApiException(404,"用户不存在");
    long member=((Number)invited.get("id")).longValue();
    if(member==actor) throw new ApiException(422,"不能邀请自己");
    return tx.execute(s -> {
      var existing=ctx.row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=? FOR UPDATE",id,member);
      if(existing!=null && role.equals(existing.get("role")))
        return Map.of("success",true,"user_id",member,"role",role);
      db.update("INSERT INTO trip_members(trip_id,user_id,role,status,invited_by) VALUES(?,?,?,'pending',?) " +
          "ON CONFLICT(trip_id,user_id) DO UPDATE SET role=EXCLUDED.role,status='pending'," +
          "invited_by=EXCLUDED.invited_by,created_at=timezone('UTC',now()),accepted_at=NULL",
          id,member,role,actor);
      notifications.notifyUser(member,id,"invite:"+id+":"+member+":"+UUID.randomUUID(),"invitation",
          "收到同行邀请","请在我的行程中接受邀请。");
      audit.success(actor,"trip.member_invite","trip",id,null,requestId,Map.of("member_id",member,"role",role));
      return Map.of("success",true,"user_id",member,"role",role);
    });
  }

  public Object invitations(long actor) {
    return Map.of("success",true,"data",db.queryForList(
        "SELECT m.trip_id,m.role,m.status,r.city,r.title,r.start_date,r.end_date " +
        "FROM trip_members m JOIN trip_records r ON r.id=m.trip_id WHERE m.user_id=? ORDER BY m.created_at DESC",actor));
  }

  public Object members(long actor,long id) {
    ctx.owned(actor,id);
    return Map.of("success",true,"data",db.queryForList(
        "SELECT m.user_id,u.username,m.role,m.status,m.created_at,m.accepted_at " +
        "FROM trip_members m JOIN users u ON u.id=m.user_id WHERE m.trip_id=? ORDER BY m.created_at",id));
  }

  public Object accept(long actor,long id,String requestId) {
    return tx.execute(s -> {
      int changed=db.update("UPDATE trip_members SET status='accepted',accepted_at=timezone('UTC',now()) " +
          "WHERE trip_id=? AND user_id=? AND status='pending'",id,actor);
      if(changed!=1) throw new ApiException(404,"待接受邀请不存在");
      long owner=((Number)ctx.trip(id).get("user_id")).longValue();
      notifications.notifyUser(owner,id,"accept:"+id+":"+actor+":"+UUID.randomUUID(),"invitation",
          "同行邀请已接受","成员已加入行程。");
      audit.success(actor,"trip.member_accept","trip",id,null,requestId);
      return Map.of("success",true);
    });
  }

  public Object removeMember(long actor,long id,long member,String requestId) {
    return tx.execute(s -> {
      if(actor!=member) ctx.owned(actor,id);
      int changed=db.update("DELETE FROM trip_members WHERE trip_id=? AND user_id=?",id,member);
      if(changed!=1) throw new ApiException(404,"成员不存在");
      audit.success(actor,"trip.member_remove","trip",id,null,requestId,Map.of("member_id",member));
      return Map.of("success",true);
    });
  }

}
