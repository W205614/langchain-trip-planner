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

/** Execution-time facts and collaboration remain business data owned by Java. */
@Service
public class TripOperationsService {
  private final JdbcTemplate db;
  private final TransactionTemplate tx;
  private final HistoryMapper history;
  private final AmapGateway amap;
  private final PlanRules rules;
  private final TripLedgerService ledger;
  private final BusinessAuditService audit;
  private final JsonMapper json;
  private final BigDecimal inputPrice;
  private final BigDecimal outputPrice;
  @Value("${TRIP_REMINDER_ZONE:Asia/Shanghai}")
  private String reminderZone;

  public TripOperationsService(JdbcTemplate db, TransactionTemplate tx, HistoryMapper history,
      AmapGateway amap, PlanRules rules, TripLedgerService ledger, BusinessAuditService audit,
      JsonMapper json,
      @Value("${TRIP_INPUT_PRICE_PER_MILLION_USD:0}") BigDecimal inputPrice,
      @Value("${TRIP_OUTPUT_PRICE_PER_MILLION_USD:0}") BigDecimal outputPrice) {
    this.db=db; this.tx=tx; this.history=history; this.amap=amap; this.rules=rules;
    this.ledger=ledger; this.audit=audit; this.json=json;
    this.inputPrice=inputPrice; this.outputPrice=outputPrice;
  }

  private Map<String,Object> row(String sql, Object... args) {
    var rows=db.queryForList(sql,args);
    return rows.isEmpty()?null:rows.getFirst();
  }

  private Map<String,Object> trip(long id) {
    var found=row("SELECT * FROM trip_records WHERE id=?",id);
    if(found==null) throw new ApiException(404,"行程不存在");
    return found;
  }

  private Map<String,Object> access(long actor,long id,boolean edit) {
    var found=trip(id);
    long owner=((Number)found.get("user_id")).longValue();
    if(owner==actor) return found;
    var member=row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=?",id,actor);
    if(member==null || !"accepted".equals(member.get("status")) ||
        (edit && !"editor".equals(member.get("role"))))
      throw new ApiException(404,"行程不存在或无权访问");
    return found;
  }

  private Map<String,Object> owned(long actor,long id) {
    var found=trip(id);
    if(((Number)found.get("user_id")).longValue()!=actor)
      throw new ApiException(404,"行程不存在或无权访问");
    return found;
  }

  /** Keeps an accepted editor row locked until the business write commits. */
  private void writeAccessLocked(long actor,long id) {
    var found=trip(id);
    if(((Number)found.get("user_id")).longValue()==actor) return;
    var member=row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=? FOR SHARE",id,actor);
    if(member==null || !"accepted".equals(member.get("status")) || !"editor".equals(member.get("role")))
      throw new ApiException(404,"行程不存在或无权访问");
  }

  private void version(Map<String,Object> trip,int expected) {
    if(expected<1 || ((Number)trip.get("version")).intValue()!=expected)
      throw new ApiException(409,"行程版本冲突，请重新加载","VERSION_CONFLICT");
  }

  private int day(Map<String,Object> trip, JsonNode payload) {
    int index=payload.path("day_index").asInt(-1);
    if(index<0 || index>=((Number)trip.get("travel_days")).intValue())
      throw new ApiException(422,"目标日期无效");
    return index;
  }

  private String required(JsonNode body,String field,int max) {
    String value=body.path(field).asText("").strip();
    if(value.isEmpty() || value.length()>max) throw new ApiException(422,field+"无效");
    return value;
  }

  private String optional(JsonNode body,String field,int max) {
    String value=body.path(field).asText("").strip();
    if(value.length()>max) throw new ApiException(422,field+"过长");
    return value;
  }

  private String choice(JsonNode body,String field,Set<String> allowed) {
    String value=body.path(field).asText("");
    if(!allowed.contains(value)) throw new ApiException(422,field+"无效");
    return value;
  }

  private Long cents(JsonNode body,boolean required) {
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

  private String key(String key) {
    if(key==null || !key.matches("[A-Za-z0-9_-]{8,128}"))
      throw new ApiException(422,"Idempotency-Key 无效");
    return key;
  }

  private String fingerprint(JsonNode body) {
    try {
      byte[] digest=MessageDigest.getInstance("SHA-256").digest(json.writeValueAsBytes(body));
      return HexFormat.of().formatHex(digest);
    } catch(Exception ex) { throw new IllegalStateException(ex); }
  }

  private void notifyUser(long user,Long tripId,String eventKey,String kind,String title,String message) {
    db.update("INSERT INTO trip_notifications(user_id,trip_id,event_key,kind,title,message) " +
        "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,event_key) DO NOTHING",
        user,tripId,eventKey,kind,title,message);
  }

  public Map<String,Object> runCheck(long actor,long id,int expected) {
    return runCheck(actor,id,expected,null);
  }

  public Map<String,Object> runCheck(long actor,long id,int expected,Integer onlyDay) {
    var original=owned(actor,id);
    version(original,expected);
    if(onlyDay!=null && (onlyDay<0 || onlyDay>=((Number)original.get("travel_days")).intValue()))
      throw new ApiException(422,"目标日期无效");
    JsonNode plan=json.readTree(original.get("plan_json").toString());
    int total=0;
    for(JsonNode d:plan.path("days")) {
      if(onlyDay!=null && d.path("day_index").asInt(-1)!=onlyDay) continue;
      total+=d.path("attractions").size();
    }
    if(total>60) throw new ApiException(422,"单次核验最多 60 个景点，请指定 day_index 按天核验");
    var risks=new ArrayList<Risk>();
    var facts=json.createObjectNode();
    var poiFacts=facts.putArray("poi");
    var weatherFacts=facts.putArray("weather");
    Instant callBudgetEnd=Instant.now().plusSeconds(30);
    try {
      ArrayNode forecast=amap.weather(original.get("city").toString());
      for(JsonNode day:plan.path("days")) {
        int dayIndex=day.path("day_index").asInt(-1);
        if(dayIndex<0 || dayIndex>=((Number)original.get("travel_days")).intValue()) continue;
        if(onlyDay!=null && dayIndex!=onlyDay) continue;
        String date=day.path("date").asText("");
        JsonNode current=null, saved=null;
        for(JsonNode entry:forecast) if(date.equals(entry.path("date").asText(""))) { current=entry; break; }
        for(JsonNode entry:plan.path("weather_info")) if(date.equals(entry.path("date").asText(""))) { saved=entry; break; }
        if(current==null) {
          risks.add(new Risk(dayIndex,"","forecast_unavailable","该日期暂无可用天气预报，请临行前再确认","amap_rest"));
          continue;
        }
        weatherFacts.add(current.deepCopy());
        String condition=current.path("day_weather").asText("")+"/"+current.path("night_weather").asText("");
        if(condition.matches(".*(暴雨|大雨|暴雪|台风|雷暴|雷阵雨).*"))
          risks.add(new Risk(dayIndex,"","weather_attention","预报含强降雨或恶劣天气，请核对户外活动","amap_rest"));
        else if(saved!=null && (!current.path("day_weather").asText("").equals(saved.path("day_weather").asText(""))
            || !current.path("night_weather").asText("").equals(saved.path("night_weather").asText(""))))
          risks.add(new Risk(dayIndex,"","weather_changed","天气预报已变化，请核对当天安排","amap_rest"));
      }
    } catch(Exception ex) {
      risks.add(new Risk(onlyDay==null?0:onlyDay,"","weather_unavailable","天气暂不可核验，原行程未被修改","amap_rest"));
    }
    for(JsonNode d:plan.path("days")) {
      int day=d.path("day_index").asInt(-1);
      if(day<0 || day>=((Number)original.get("travel_days")).intValue()) continue;
      if(onlyDay!=null && day!=onlyDay) continue;
      for(JsonNode a:d.path("attractions")) {
        String poiId=a.path("poi_id").asText("");
        if(Instant.now().isAfter(callBudgetEnd)) {
          risks.add(new Risk(day,poiId,"check_time_budget","核验已达到时间预算，此景点仍需人工确认","system"));
          continue;
        }
        if(!poiId.matches("[A-Za-z0-9_-]{1,64}")) {
          risks.add(new Risk(day,"","poi_unknown","景点缺少可信地图编号，请人工核对","stored_plan"));
          continue;
        }
        try {
          ObjectNode current=amap.detail(poiId);
          String returnedId=current.path("id").asText("");
          if(!returnedId.isEmpty() && !poiId.equals(returnedId)) {
            risks.add(new Risk(day,poiId,"poi_mismatch","地图返回的景点编号与原行程不同，请人工确认","amap_rest"));
            continue;
          }
          String hours=current.path("opening_hours").asText("").strip();
          poiFacts.addObject().put("day_index",day).put("poi_id",poiId)
              .put("name",current.path("name").asText(""))
              .put("opening_hours",hours).put("source","amap_rest");
          if(hours.isEmpty())
            risks.add(new Risk(day,poiId,"hours_unknown","地图未提供营业时间，请到官方渠道确认出行当日安排","amap_rest"));
          else if(!hours.equals(a.path("opening_hours").asText("").strip()))
            risks.add(new Risk(day,poiId,"hours_changed","地图营业时间与保存时不同，请核对具体出行日期","amap_rest"));
        } catch(Exception ex) {
          risks.add(new Risk(day,poiId,"poi_unavailable","暂时无法核验景点，原行程未被修改","amap_rest"));
        }
      }
    }
    facts.put("note","地图营业信息未必适用于出行日期；天气及预约须以对应日期的官方信息为准");
    boolean uncertain=!risks.isEmpty();
    return tx.execute(s -> {
      version(owned(actor,id),expected);
      Long checkId=db.queryForObject("INSERT INTO trip_checks(trip_id,trip_version,checked_by,expires_at,status,facts_json,day_index) " +
          "VALUES(?,?,?,timezone('UTC',now())+interval '6 hours',?,?::jsonb,?) RETURNING id",
          Long.class,id,expected,actor,uncertain?"uncertain":"current",json.writeValueAsString(facts),onlyDay);
      for(Risk r:risks) db.update("INSERT INTO trip_risks(check_id,trip_id,day_index,poi_id,code,message,source) VALUES(?,?,?,?,?,?,?)",
          checkId,id,r.day,r.poiId,r.code,r.message,r.source);
      audit.success(actor,"trip.check","trip",id,expected,"",Map.of("risk_count",risks.size()));
      if(uncertain) notifyUser(actor,id,"check:"+checkId,"trip_risk","行程有待核验事项","请查看行前核验结果，再决定是否改排。");
      return Map.of("success",true,"check_id",checkId,"trip_version",expected,
          "status",uncertain?"uncertain":"current","risks",risks);
    });
  }

  private record Risk(int day,String poiId,String code,String message,String source) {}

  public Object checks(long actor,long id) {
    return checks(actor,id,null);
  }

  public Object checks(long actor,long id,Integer onlyDay) {
    access(actor,id,false);
    var latest=onlyDay==null?
        row("SELECT * FROM trip_checks WHERE trip_id=? AND day_index IS NULL ORDER BY checked_at DESC,id DESC LIMIT 1",id):
        row("SELECT * FROM trip_checks WHERE trip_id=? AND day_index=? ORDER BY checked_at DESC,id DESC LIMIT 1",id,onlyDay);
    if(latest==null) return Map.of("success",true,"data",Map.of("status","never_checked","risks",List.of()));
    var result=new LinkedHashMap<>(latest);
    result.put("risks",db.queryForList("SELECT id,day_index,poi_id,code,message,source,observed_at,acknowledged_at " +
        "FROM trip_risks WHERE check_id=? ORDER BY day_index,id",latest.get("id")));
    int current=((Number)trip(id).get("version")).intValue();
    result.put("stale",current!=((Number)latest.get("trip_version")).intValue() ||
        ((Timestamp)latest.get("expires_at")).toInstant().isBefore(Instant.now()));
    return Map.of("success",true,"data",result);
  }

  public Object acknowledge(long actor,long id,long riskId,int expected) {
    return tx.execute(s -> {
      writeAccessLocked(actor,id);
      version(access(actor,id,true),expected);
      int changed=db.update("UPDATE trip_risks SET acknowledged_by=?,acknowledged_at=timezone('UTC',now()) " +
          "WHERE id=? AND trip_id=? AND acknowledged_at IS NULL AND check_id IN " +
          "(SELECT id FROM trip_checks WHERE trip_id=? AND trip_version=?)",
          actor,riskId,id,id,expected);
      if(changed!=1) throw new ApiException(409,"风险已处理或核验结果已过期");
      audit.success(actor,"trip.risk_acknowledge","trip_risk",riskId,expected,"");
      return Map.of("success",true);
    });
  }

  public Object workspace(long actor,long id) {
    var trip=access(actor,id,false);
    var result=new LinkedHashMap<String,Object>();
    for(String f:List.of("id","city","start_date","end_date","travel_days","version","title","budget_total"))
      result.put(f,trip.get(f));
    result.put("plan",json.readTree(trip.get("plan_json").toString()));
    result.put("quality",json.readTree(trip.get("quality_json").toString()));
    result.put("viewer_id",actor);
    if(((Number)trip.get("user_id")).longValue()==actor) result.put("role","owner");
    else {
      var membership=row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=?",id,actor);
      if(membership==null || !"accepted".equals(membership.get("status")))
        throw new ApiException(404,"行程不存在或无权访问");
      result.put("role",membership.get("role"));
    }
    return Map.of("success",true,"data",result);
  }

  public Object reorder(long actor,long id,int expected,ObjectNode input,String requestId) {
    var found=access(actor,id,true);
    version(found,expected);
    int dayIndex=day(found,input);
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
    if(input.has("description")) ((ObjectNode)target).put("description",optional(input,"description",500));
    var request=json.createObjectNode();
    for(String f:List.of("departure_city","city","start_date","end_date","travel_days","transportation",
        "accommodation","traveler_count","room_count","budget_total","free_text_input"))
      request.set(f,json.valueToTree(found.get(f)));
    request.set("preferences",json.readTree(found.get("preferences").toString()));
    request.set("constraints",plan.path("constraints").isObject()?plan.path("constraints").deepCopy():json.createObjectNode());
    ObjectNode quality=rules.finish(plan,TripRequests.normalize(request),null,false);
    quality.withArray("data_gaps").add("shared_reorder_route_unverified");
    long owner=((Number)found.get("user_id")).longValue();
    return tx.execute(s -> {
      writeAccessLocked(actor,id);
      if(history.update(Map.of("id",id,"user_id",owner,"version",expected,
          "plan_json",json.writeValueAsString(plan),"quality_json",json.writeValueAsString(quality)))!=1)
        throw new ApiException(409,"行程版本冲突，请重新加载","VERSION_CONFLICT");
      history.outbox(id,owner,"draft".equals(quality.path("outcome").asText(""))?"delete":"upsert");
      ledger.captureOnBehalf(owner,actor,id,"user_edit",requestId);
      notifyUser(owner,id,"reorder:"+id+":"+(expected+1),"trip_change","同行人调整了行程","第"+(dayIndex+1)+"天顺序已变更，请核对路线。");
      return Map.of("success",true,"id",id,"version",expected+1,"quality",quality,"data",plan);
    });
  }

  public Object invite(long actor,long id,ObjectNode body,String requestId) {
    owned(actor,id);
    String username=required(body,"username",64);
    String role=choice(body,"role",Set.of("viewer","editor"));
    var invited=row("SELECT id FROM users WHERE username=?",username);
    if(invited==null) throw new ApiException(404,"用户不存在");
    long member=((Number)invited.get("id")).longValue();
    if(member==actor) throw new ApiException(422,"不能邀请自己");
    return tx.execute(s -> {
      var existing=row("SELECT role,status FROM trip_members WHERE trip_id=? AND user_id=? FOR UPDATE",id,member);
      if(existing!=null && role.equals(existing.get("role")))
        return Map.of("success",true,"user_id",member,"role",role);
      db.update("INSERT INTO trip_members(trip_id,user_id,role,status,invited_by) VALUES(?,?,?,'pending',?) " +
          "ON CONFLICT(trip_id,user_id) DO UPDATE SET role=EXCLUDED.role,status='pending'," +
          "invited_by=EXCLUDED.invited_by,created_at=timezone('UTC',now()),accepted_at=NULL",
          id,member,role,actor);
      notifyUser(member,id,"invite:"+id+":"+member+":"+UUID.randomUUID(),"invitation",
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
    owned(actor,id);
    return Map.of("success",true,"data",db.queryForList(
        "SELECT m.user_id,u.username,m.role,m.status,m.created_at,m.accepted_at " +
        "FROM trip_members m JOIN users u ON u.id=m.user_id WHERE m.trip_id=? ORDER BY m.created_at",id));
  }

  public Object accept(long actor,long id,String requestId) {
    return tx.execute(s -> {
      int changed=db.update("UPDATE trip_members SET status='accepted',accepted_at=timezone('UTC',now()) " +
          "WHERE trip_id=? AND user_id=? AND status='pending'",id,actor);
      if(changed!=1) throw new ApiException(404,"待接受邀请不存在");
      long owner=((Number)trip(id).get("user_id")).longValue();
      notifyUser(owner,id,"accept:"+id+":"+actor+":"+UUID.randomUUID(),"invitation",
          "同行邀请已接受","成员已加入行程。");
      audit.success(actor,"trip.member_accept","trip",id,null,requestId);
      return Map.of("success",true);
    });
  }

  public Object removeMember(long actor,long id,long member,String requestId) {
    return tx.execute(s -> {
      if(actor!=member) owned(actor,id);
      int changed=db.update("DELETE FROM trip_members WHERE trip_id=? AND user_id=?",id,member);
      if(changed!=1) throw new ApiException(404,"成员不存在");
      audit.success(actor,"trip.member_remove","trip",id,null,requestId,Map.of("member_id",member));
      return Map.of("success",true);
    });
  }

  public Object commitments(long actor,long id) {
    access(actor,id,false);
    return Map.of("success",true,"data",db.queryForList(
        "SELECT id,day_index,category,title,status,amount_cents,note,version,created_by,created_at,updated_at " +
        "FROM trip_commitments WHERE trip_id=? ORDER BY day_index,id",id));
  }

  public Object addCommitment(long actor,long id,ObjectNode body,String requestKey,String requestId) {
    var found=access(actor,id,true);
    int day=day(found,body);
    String category=choice(body,"category",Set.of("ticket","lodging","transport","meal","other"));
    String title=required(body,"title",160);
    String status=choice(body,"status",Set.of("planned","confirmed","cancelled"));
    String note=optional(body,"note",500);
    Long amount=cents(body,false);
    String key=key(requestKey), fp=fingerprint(body);
    return tx.execute(s -> {
      writeAccessLocked(actor,id);
      int inserted=db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,amount_cents,note,created_by,request_key,fingerprint) " +
          "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trip_id,request_key) WHERE request_key IS NOT NULL DO NOTHING",
          id,day,category,title,status,amount,note,actor,key,fp);
      var saved=row("SELECT id,fingerprint FROM trip_commitments WHERE trip_id=? AND request_key=?",id,key);
      if(!fp.equals(saved.get("fingerprint"))) throw new ApiException(409,"幂等键已用于不同内容");
      if(inserted==1) audit.success(actor,"trip.commitment_add","trip_commitment",saved.get("id"),null,requestId);
      return Map.of("success",true,"id",saved.get("id"));
    });
  }

  public Object updateCommitment(long actor,long id,long commitment,int expected,ObjectNode body,String requestId) {
    access(actor,id,true);
    String status=choice(body,"status",Set.of("planned","confirmed","cancelled"));
    String note=optional(body,"note",500);
    Long amount=cents(body,false);
    return tx.execute(s -> {
      writeAccessLocked(actor,id);
      int changed=db.update("UPDATE trip_commitments SET status=?,amount_cents=?,note=?,version=version+1," +
          "updated_at=timezone('UTC',now()) WHERE id=? AND trip_id=? AND version=?",
          status,amount,note,commitment,id,expected);
      if(changed!=1) throw new ApiException(409,"预订事项版本冲突","VERSION_CONFLICT");
      audit.success(actor,"trip.commitment_update","trip_commitment",commitment,expected+1,requestId);
      return Map.of("success",true,"version",expected+1);
    });
  }

  public Object expenses(long actor,long id) {
    var found=access(actor,id,false);
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
    var found=access(actor,id,true);
    int day=day(found,body);
    String category=choice(body,"category",Set.of("ticket","lodging","transport","meal","other"));
    Long amount=cents(body,true);
    String note=optional(body,"note",500);
    long commitment=body.path("commitment_id").asLong(0);
    if(commitment>0 && row("SELECT id FROM trip_commitments WHERE id=? AND trip_id=?",commitment,id)==null)
      throw new ApiException(422,"预订事项不属于当前行程");
    String key=key(requestKey), fp=fingerprint(body);
    return tx.execute(s -> {
      writeAccessLocked(actor,id);
      int inserted=db.update("INSERT INTO trip_expenses(trip_id,commitment_id,day_index,category,amount_cents,note,created_by,request_key,fingerprint) " +
          "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(trip_id,request_key) WHERE request_key IS NOT NULL DO NOTHING",
          id,commitment>0?commitment:null,day,category,amount,note,actor,key,fp);
      var saved=row("SELECT id,fingerprint FROM trip_expenses WHERE trip_id=? AND request_key=?",id,key);
      if(!fp.equals(saved.get("fingerprint"))) throw new ApiException(409,"幂等键已用于不同内容");
      if(inserted==1) audit.success(actor,"trip.expense_add","trip_expense",saved.get("id"),null,requestId);
      return Map.of("success",true,"id",saved.get("id"));
    });
  }

  public Object voidExpense(long actor,long id,long expense,ObjectNode body,String requestId) {
    var found=access(actor,id,true);
    String reason=required(body,"reason",300);
    var entry=row("SELECT created_by FROM trip_expenses WHERE id=? AND trip_id=? AND voided_at IS NULL",expense,id);
    if(entry==null) throw new ApiException(404,"费用记录不存在");
    if(((Number)entry.get("created_by")).longValue()!=actor &&
        ((Number)found.get("user_id")).longValue()!=actor)
      throw new ApiException(403,"只能撤销自己的费用记录");
    return tx.execute(s -> {
      writeAccessLocked(actor,id);
      int changed=db.update("UPDATE trip_expenses SET voided_at=timezone('UTC',now()),voided_by=?,void_reason=? " +
          "WHERE id=? AND trip_id=? AND voided_at IS NULL",actor,reason,expense,id);
      if(changed!=1) throw new ApiException(409,"费用记录已撤销");
      audit.success(actor,"trip.expense_void","trip_expense",expense,null,requestId);
      return Map.of("success",true);
    });
  }

  public Object notifications(long actor) {
    return Map.of("success",true,"data",db.queryForList("SELECT id,trip_id,kind,title,message,created_at,read_at " +
        "FROM trip_notifications WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 100",actor));
  }

  public Object readNotification(long actor,long notification) {
    if(db.update("UPDATE trip_notifications SET read_at=COALESCE(read_at,timezone('UTC',now())) " +
        "WHERE id=? AND user_id=?",notification,actor)!=1) throw new ApiException(404,"通知不存在");
    return Map.of("success",true);
  }

  public Object usage(long actor) {
    var totals=row("SELECT count(*) AS tasks, " +
        "COALESCE(sum((usage_json::jsonb->>'calls')::bigint),0) AS calls," +
        "COALESCE(sum((usage_json::jsonb->>'input_tokens')::bigint),0) AS input_tokens," +
        "COALESCE(sum((usage_json::jsonb->>'output_tokens')::bigint),0) AS output_tokens," +
        "count(*) FILTER (WHERE usage_json='{}') AS missing_usage FROM trip_tasks " +
        "WHERE user_id=? AND created_at>=date_trunc('month',timezone('UTC',now()))",actor);
    var policy=row("SELECT monthly_token_warning_limit FROM trip_usage_policies WHERE user_id=?",actor);
    long tokens=((Number)totals.get("input_tokens")).longValue()+((Number)totals.get("output_tokens")).longValue();
    var result=new LinkedHashMap<>(totals);
    result.put("warning_limit",policy==null?null:policy.get("monthly_token_warning_limit"));
    result.put("over_warning_limit",policy!=null && tokens>=((Number)policy.get("monthly_token_warning_limit")).longValue());
    result.put("estimated_cost_usd",inputPrice.signum()>0 && outputPrice.signum()>0?
        inputPrice.multiply(BigDecimal.valueOf(((Number)totals.get("input_tokens")).longValue()))
            .add(outputPrice.multiply(BigDecimal.valueOf(((Number)totals.get("output_tokens")).longValue())))
            .divide(BigDecimal.valueOf(1_000_000),6,RoundingMode.HALF_UP):null);
    result.put("cost_note","仅统计已报告 usage 的行程任务；单价未配置时不估价，取消后迟到的上游费用可能缺失");
    return Map.of("success",true,"data",result);
  }

  public Object usagePolicy(long actor,ObjectNode body) {
    return usagePolicy(actor,body,"");
  }

  public Object usagePolicy(long actor,ObjectNode body,String requestId) {
    long limit=body.path("monthly_token_warning_limit").asLong(-1);
    if(limit<1000 || limit>1_000_000_000L) throw new ApiException(422,"Token 提醒阈值无效");
    tx.executeWithoutResult(s -> {
      db.update("INSERT INTO trip_usage_policies(user_id,monthly_token_warning_limit) VALUES(?,?) " +
          "ON CONFLICT(user_id) DO UPDATE SET monthly_token_warning_limit=EXCLUDED.monthly_token_warning_limit," +
          "updated_at=timezone('UTC',now())",actor,limit);
      audit.success(actor,"usage.policy_update","usage_policy",actor,null,requestId,Map.of("token_limit",limit));
    });
    return usage(actor);
  }

  @Scheduled(fixedDelayString="${TRIP_USAGE_SCAN_MILLIS:900000}")
  public void usageWarnings() {
    for(var p:db.queryForList("SELECT p.user_id,p.monthly_token_warning_limit FROM trip_usage_policies p " +
        "JOIN trip_tasks t ON t.user_id=p.user_id AND t.created_at>=date_trunc('month',timezone('UTC',now())) " +
        "GROUP BY p.user_id,p.monthly_token_warning_limit " +
        "HAVING COALESCE(sum(COALESCE((t.usage_json::jsonb->>'input_tokens')::bigint,0)+" +
        "COALESCE((t.usage_json::jsonb->>'output_tokens')::bigint,0)),0)>=p.monthly_token_warning_limit")) {
      long user=((Number)p.get("user_id")).longValue();
      String month=YearMonth.now(ZoneOffset.UTC).toString();
      notifyUser(user,null,"usage:"+month+":"+p.get("monthly_token_warning_limit"),"usage_warning",
          "本月 Token 用量达到提醒阈值","请查看用量；这是提醒阈值，不会自动停止任务。");
    }
  }

  @Scheduled(fixedDelayString="${TRIP_REMINDER_SCAN_MILLIS:3600000}")
  public void departureReminders() {
    String tomorrow=LocalDate.now(ZoneId.of(reminderZone)).plusDays(1).toString();
    for(var trip:db.queryForList("SELECT id,user_id,city,start_date FROM trip_records WHERE start_date=?",tomorrow)) {
      long id=((Number)trip.get("id")).longValue();
      long owner=((Number)trip.get("user_id")).longValue();
      String title="明日出发："+trip.get("city");
      String event="departure:"+id+":"+tomorrow;
      notifyUser(owner,id,event,"departure",title,"请查看行前核验、预订与费用事项。");
      for(var member:db.queryForList("SELECT user_id FROM trip_members WHERE trip_id=? AND status='accepted'",id))
        notifyUser(((Number)member.get("user_id")).longValue(),id,event,"departure",title,
            "请查看同行行程及行前核验事项。");
    }
  }
}
