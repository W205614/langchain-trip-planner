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

/** Reservation ledger and exact-cent expense writes share one access policy. */
@Service
public class TripBookingExpenseService {
  private final TripOperationAccess ctx;
  private final JdbcTemplate db;
  private final JsonMapper json;
  private final TransactionTemplate tx;
  private final BusinessAuditService audit;
  public TripBookingExpenseService(TripOperationAccess ctx,TransactionTemplate tx,BusinessAuditService audit){
    this.ctx=ctx;this.db=ctx.db;this.json=ctx.json;this.tx=tx;this.audit=audit;
  }
  public Object commitments(long actor,long id) {
    ctx.access(actor,id,false);
    var entries=db.queryForList(
        "SELECT id,day_index,category,title,status,amount_cents,note,version,created_by,created_at,updated_at,activity_id,poi_id,location_json AS location,start_at,end_at,details_json " +
        "FROM trip_commitments WHERE trip_id=? ORDER BY day_index,id",id);
    for(var entry:entries){for(String f:List.of("location","details_json"))if(entry.get(f)!=null)entry.put(f,json.readTree(entry.get(f).toString()));for(String f:List.of("start_at","end_at"))if(entry.get(f) instanceof Timestamp t)entry.put(f,t.toInstant().toString());}
    return Map.of("success",true,"data",entries);
  }

  public Object addCommitment(long actor,long id,ObjectNode body,String requestKey,String requestId) {
    var found=ctx.access(actor,id,true);
    int day=ctx.day(found,body);
    String category=ctx.choice(body,"category",Set.of("ticket","lodging","transport","meal","other"));
    String title=ctx.required(body,"title",160);
    String status=ctx.choice(body,"status",Set.of("planned","confirmed","cancelled"));
    String note=ctx.optional(body,"note",500);
    Long amount=ctx.cents(body,false);
    String key=ctx.key(requestKey), fp=ctx.fingerprint(body);
    return tx.execute(s -> {
      ctx.writeAccessLocked(actor,id);
      int inserted=db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,amount_cents,note,created_by,request_key,fingerprint) " +
          "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trip_id,request_key) WHERE request_key IS NOT NULL DO NOTHING",
          id,day,category,title,status,amount,note,actor,key,fp);
      var saved=ctx.row("SELECT id,fingerprint FROM trip_commitments WHERE trip_id=? AND request_key=?",id,key);
      if(!fp.equals(saved.get("fingerprint"))) throw new ApiException(409,"幂等键已用于不同内容");
      if(inserted==1) {requirePlanningDetailOwner(actor,id,body);saveCommitmentDetails(id,((Number)saved.get("id")).longValue(),body);audit.success(actor,"trip.commitment_add","trip_commitment",saved.get("id"),null,requestId);}
      return Map.of("success",true,"id",saved.get("id"));
    });
  }

  public Object updateCommitment(long actor,long id,long commitment,int expected,ObjectNode body,String requestId) {
    ctx.access(actor,id,true);
    String status=ctx.choice(body,"status",Set.of("planned","confirmed","cancelled"));
    String note=ctx.optional(body,"note",500);
    Long amount=ctx.cents(body,false);
    return tx.execute(s -> {
      ctx.writeAccessLocked(actor,id);
      int changed=db.update("UPDATE trip_commitments SET status=?,amount_cents=?,note=?,version=version+1," +
          "updated_at=timezone('UTC',now()) WHERE id=? AND trip_id=? AND version=?",
          status,amount,note,commitment,id,expected);
      if(changed!=1) throw new ApiException(409,"预订事项版本冲突","VERSION_CONFLICT");
      requirePlanningDetailOwner(actor,id,body);saveCommitmentDetails(id,commitment,body);
      audit.success(actor,"trip.commitment_update","trip_commitment",commitment,expected+1,requestId);
      return Map.of("success",true,"version",expected+1);
    });
  }

  private void requirePlanningDetailOwner(long actor,long id,ObjectNode body) {
    for(String f:List.of("activity_id","poi_id","start_at","end_at","location","origin_location","destination_location","origin_city","destination_city","origin_name","destination_name","service_number","time_mode","duration_minutes"))if(body.has(f)){ctx.owned(actor,id);return;}
  }
  private void saveCommitmentDetails(long trip,long id,ObjectNode body) {
    var saved=ctx.row("SELECT * FROM trip_commitments WHERE id=? AND trip_id=?",id,trip);
    var merged=json.createObjectNode();
    for(String f:List.of("activity_id","poi_id","start_at","end_at"))if(saved.get(f)!=null)merged.put(f,saved.get(f) instanceof Timestamp t?t.toInstant().toString():saved.get(f).toString());
    if(saved.get("location_json")!=null)merged.set("location",json.readTree(saved.get("location_json").toString()));
    var details=(ObjectNode)json.readTree(saved.get("details_json").toString());
    for(String f:List.of("activity_id","poi_id","start_at","end_at","location"))if(body.has(f))merged.set(f,body.path(f));
    for(String f:List.of("origin_location","destination_location","origin_city","destination_city","origin_name","destination_name","service_number","time_mode","duration_minutes"))if(body.has(f))details.set(f,body.path(f));
    java.time.OffsetDateTime start=null,end=null;
    try {
      if(merged.hasNonNull("start_at")&&!merged.path("start_at").asText().isBlank())start=java.time.OffsetDateTime.parse(merged.path("start_at").asText());
      if(merged.hasNonNull("end_at")&&!merged.path("end_at").asText().isBlank())end=java.time.OffsetDateTime.parse(merged.path("end_at").asText());
      if((start==null)!=(end==null)||start!=null&&!end.isAfter(start))throw new IllegalArgumentException();
    }catch(Exception ex){throw new ApiException(422,"预约日期时间须包含时区且结束晚于开始");}
    if(merged.path("activity_id").asText("").length()>160||merged.path("poi_id").asText("").length()>64)throw new ApiException(422,"地点或活动编号过长");
    for(String field:List.of("location","origin_location","destination_location")) {
      var loc=field.equals("location")?merged.path(field):details.path(field);
      if(!loc.isMissingNode()&&!loc.isNull()&&(!loc.path("longitude").isNumber()||!loc.path("latitude").isNumber()||Math.abs(loc.path("longitude").asDouble())>180||Math.abs(loc.path("latitude").asDouble())>90))throw new ApiException(422,"地点坐标无效");
    }
    if(!Set.of("fixed","entry_window").contains(details.path("time_mode").asText("fixed")))throw new ApiException(422,"预约时间模式无效");
    if(details.has("duration_minutes")&&(!details.path("duration_minutes").isIntegralNumber()||details.path("duration_minutes").asInt()<1||details.path("duration_minutes").asInt()>1440))throw new ApiException(422,"活动时长无效");
    if(json.writeValueAsString(details).length()>4000)throw new ApiException(422,"交通信息过长");
    db.update("UPDATE trip_commitments SET activity_id=?,poi_id=?,location_json=?::jsonb,start_at=?,end_at=?,details_json=?::jsonb WHERE id=? AND trip_id=?",merged.path("activity_id").asText(""),merged.path("poi_id").asText(""),merged.hasNonNull("location")?json.writeValueAsString(merged.path("location")):null,start,end,json.writeValueAsString(details),id,trip);
  }

  public Object expenses(long actor,long id) {
    var found=ctx.access(actor,id,false);
    var entries=db.queryForList("SELECT id,commitment_id,day_index,category,amount_cents,note,created_by,created_at," +
        "voided_at,void_reason FROM trip_expenses WHERE trip_id=? ORDER BY day_index,id",id);
    long actual=entries.stream().filter(e->e.get("voided_at")==null)
        .mapToLong(e->((Number)e.get("amount_cents")).longValue()).sum();
    Long confirmed=db.queryForObject("SELECT COALESCE(sum(amount_cents),0) FROM trip_commitments " +
        "WHERE trip_id=? AND status='confirmed' AND amount_cents IS NOT NULL",Long.class,id);
    Long unknown=db.queryForObject("SELECT count(*) FROM trip_commitments WHERE trip_id=? " +
        "AND status='confirmed' AND amount_cents IS NULL",Long.class,id);
    var plan=json.readTree(found.get("plan_json").toString());
    var summary=new LinkedHashMap<String,Object>();
    summary.put("budget_limit_cents",found.get("budget_total")==null?null:
        ((Number)found.get("budget_total")).longValue()*100);
    summary.put("plan_estimate_cents",plan.path("budget").path("total").asLong(0)*100);
    summary.put("confirmed_cents",confirmed);
    summary.put("actual_cents",actual);
    summary.put("confirmed_unknown_count",unknown);
    summary.put("currency","CNY");
    return Map.of("success",true,"data",entries,"summary",summary);
  }

  public Object addExpense(long actor,long id,ObjectNode body,String requestKey,String requestId) {
    var found=ctx.access(actor,id,true);
    int day=ctx.day(found,body);
    String category=ctx.choice(body,"category",Set.of("ticket","lodging","transport","meal","other"));
    Long amount=ctx.cents(body,true);
    String note=ctx.optional(body,"note",500);
    long commitment=body.path("commitment_id").asLong(0);
    if(commitment>0 && ctx.row("SELECT id FROM trip_commitments WHERE id=? AND trip_id=?",commitment,id)==null)
      throw new ApiException(422,"预订事项不属于当前行程");
    String key=ctx.key(requestKey), fp=ctx.fingerprint(body);
    return tx.execute(s -> {
      ctx.writeAccessLocked(actor,id);
      int inserted=db.update("INSERT INTO trip_expenses(trip_id,commitment_id,day_index,category,amount_cents,note,created_by,request_key,fingerprint) " +
          "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(trip_id,request_key) WHERE request_key IS NOT NULL DO NOTHING",
          id,commitment>0?commitment:null,day,category,amount,note,actor,key,fp);
      var saved=ctx.row("SELECT id,fingerprint FROM trip_expenses WHERE trip_id=? AND request_key=?",id,key);
      if(!fp.equals(saved.get("fingerprint"))) throw new ApiException(409,"幂等键已用于不同内容");
      if(inserted==1) audit.success(actor,"trip.expense_add","trip_expense",saved.get("id"),null,requestId);
      return Map.of("success",true,"id",saved.get("id"));
    });
  }

  public Object voidExpense(long actor,long id,long expense,ObjectNode body,String requestId) {
    var found=ctx.access(actor,id,true);
    String reason=ctx.required(body,"reason",300);
    var entry=ctx.row("SELECT created_by FROM trip_expenses WHERE id=? AND trip_id=? AND voided_at IS NULL",expense,id);
    if(entry==null) throw new ApiException(404,"费用记录不存在");
    if(((Number)entry.get("created_by")).longValue()!=actor &&
        ((Number)found.get("user_id")).longValue()!=actor)
      throw new ApiException(403,"只能撤销自己的费用记录");
    return tx.execute(s -> {
      ctx.writeAccessLocked(actor,id);
      int changed=db.update("UPDATE trip_expenses SET voided_at=timezone('UTC',now()),voided_by=?,void_reason=? " +
          "WHERE id=? AND trip_id=? AND voided_at IS NULL",actor,reason,expense,id);
      if(changed!=1) throw new ApiException(409,"费用记录已撤销");
      audit.success(actor,"trip.expense_void","trip_expense",expense,null,requestId);
      return Map.of("success",true);
    });
  }

}
