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

/** User-scoped inbox, reported usage accounting and deduplicated reminders. */
@Service
public class TripNotificationUsageService {
  private final TripOperationAccess ctx;
  private final JdbcTemplate db;
  private final JsonMapper json;
  private final TransactionTemplate tx;
  private final BusinessAuditService audit;
  private final SingleInstance singleInstance;
  private final BigDecimal inputPrice;
  private final BigDecimal outputPrice;
  private final String reminderZone;
  public TripNotificationUsageService(TripOperationAccess ctx,TransactionTemplate tx,BusinessAuditService audit,SingleInstance singleInstance,
      @Value("${TRIP_INPUT_PRICE_PER_MILLION_USD:0}") BigDecimal inputPrice,
      @Value("${TRIP_OUTPUT_PRICE_PER_MILLION_USD:0}") BigDecimal outputPrice,
      @Value("${TRIP_REMINDER_ZONE:Asia/Shanghai}") String reminderZone){
    this.ctx=ctx;this.db=ctx.db;this.json=ctx.json;this.tx=tx;this.audit=audit;this.singleInstance=singleInstance;
    this.inputPrice=inputPrice;this.outputPrice=outputPrice;this.reminderZone=reminderZone;
  }
  void notifyUser(long user,Long tripId,String eventKey,String kind,String title,String message) {
    db.update("INSERT INTO trip_notifications(user_id,trip_id,event_key,kind,title,message) " +
        "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,event_key) DO NOTHING",
        user,tripId,eventKey,kind,title,message);
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
    var totals=ctx.row("SELECT count(*) AS tasks, " +
        "COALESCE(sum((usage_json::jsonb->>'calls')::bigint),0) AS calls," +
        "COALESCE(sum((usage_json::jsonb->>'input_tokens')::bigint),0) AS input_tokens," +
        "COALESCE(sum((usage_json::jsonb->>'output_tokens')::bigint),0) AS output_tokens," +
        "count(*) FILTER (WHERE usage_json='{}') AS missing_usage FROM trip_tasks " +
        "WHERE user_id=? AND created_at>=date_trunc('month',timezone('UTC',now()))",actor);
    var policy=ctx.row("SELECT monthly_token_warning_limit FROM trip_usage_policies WHERE user_id=?",actor);
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
  public void scheduledUsageWarnings(){if(singleInstance.owned())usageWarnings();}
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
  public void scheduledDepartureReminders(){if(singleInstance.owned())departureReminders();}
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
  }}
