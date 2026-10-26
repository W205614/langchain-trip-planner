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

/** External fact checks, persisted uncertainty and risk acknowledgement. */
@Service
public class TripCheckService {
  private final TripOperationAccess ctx;
  private final JdbcTemplate db;
  private final JsonMapper json;
  private final TransactionTemplate tx;
  private final BusinessAuditService audit;
  private final AmapGateway amap;
  private final TripNotificationUsageService notifications;
  public TripCheckService(TripOperationAccess ctx,TransactionTemplate tx,BusinessAuditService audit,AmapGateway amap,TripNotificationUsageService notifications){
    this.ctx=ctx;this.db=ctx.db;this.json=ctx.json;this.tx=tx;this.audit=audit;this.amap=amap;this.notifications=notifications;
  }
  public Map<String,Object> runCheck(long actor,long id,int expected) {
    return runCheck(actor,id,expected,null);
  }

  public Map<String,Object> runCheck(long actor,long id,int expected,Integer onlyDay) {
    var original=ctx.owned(actor,id);
    ctx.version(original,expected);
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
      ctx.version(ctx.owned(actor,id),expected);
      Long checkId=db.queryForObject("INSERT INTO trip_checks(trip_id,trip_version,checked_by,expires_at,status,facts_json,day_index) " +
          "VALUES(?,?,?,timezone('UTC',now())+interval '6 hours',?,?::jsonb,?) RETURNING id",
          Long.class,id,expected,actor,uncertain?"uncertain":"current",json.writeValueAsString(facts),onlyDay);
      for(Risk r:risks) db.update("INSERT INTO trip_risks(check_id,trip_id,day_index,poi_id,code,message,source) VALUES(?,?,?,?,?,?,?)",
          checkId,id,r.day,r.poiId,r.code,r.message,r.source);
      audit.success(actor,"trip.check","trip",id,expected,"",Map.of("risk_count",risks.size()));
      if(uncertain) notifications.notifyUser(actor,id,"check:"+checkId,"trip_risk","行程有待核验事项","请查看行前核验结果，再决定是否改排。");
      return Map.of("success",true,"check_id",checkId,"trip_version",expected,
          "status",uncertain?"uncertain":"current","risks",risks);
    });
  }

  private record Risk(int day,String poiId,String code,String message,String source) {}

  public Object checks(long actor,long id) {
    return checks(actor,id,null);
  }

  public Object checks(long actor,long id,Integer onlyDay) {
    ctx.access(actor,id,false);
    var latest=onlyDay==null?
        ctx.row("SELECT * FROM trip_checks WHERE trip_id=? AND day_index IS NULL ORDER BY checked_at DESC,id DESC LIMIT 1",id):
        ctx.row("SELECT * FROM trip_checks WHERE trip_id=? AND day_index=? ORDER BY checked_at DESC,id DESC LIMIT 1",id,onlyDay);
    if(latest==null) return Map.of("success",true,"data",Map.of("status","never_checked","risks",List.of()));
    var result=new LinkedHashMap<>(latest);
    result.put("risks",db.queryForList("SELECT id,day_index,poi_id,code,message,source,observed_at,acknowledged_at " +
        "FROM trip_risks WHERE check_id=? ORDER BY day_index,id",latest.get("id")));
    int current=((Number)ctx.trip(id).get("version")).intValue();
    result.put("stale",current!=((Number)latest.get("trip_version")).intValue() ||
        ((Timestamp)latest.get("expires_at")).toInstant().isBefore(Instant.now()));
    return Map.of("success",true,"data",result);
  }

  public Object acknowledge(long actor,long id,long riskId,int expected) {
    return tx.execute(s -> {
      ctx.writeAccessLocked(actor,id);
      ctx.version(ctx.access(actor,id,true),expected);
      int changed=db.update("UPDATE trip_risks SET acknowledged_by=?,acknowledged_at=timezone('UTC',now()) " +
          "WHERE id=? AND trip_id=? AND acknowledged_at IS NULL AND check_id IN " +
          "(SELECT id FROM trip_checks WHERE trip_id=? AND trip_version=?)",
          actor,riskId,id,id,expected);
      if(changed!=1) throw new ApiException(409,"风险已处理或核验结果已过期");
      audit.success(actor,"trip.risk_acknowledge","trip_risk",riskId,expected,"");
      return Map.of("success",true);
    });
  }

}
