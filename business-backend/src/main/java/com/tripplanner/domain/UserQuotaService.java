package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.security.AccountAccess;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** All reservations are made in the caller's transaction before any external send. */
@Service
public class UserQuotaService {
  private final JdbcTemplate db;
  private final AccountAccess access;
  private final Map<String,Integer> defaults;
  public UserQuotaService(JdbcTemplate db,AccountAccess access,
      @Value("${TRIP_USER_DAILY_LIMIT:50}") int tasks,
      @Value("${TRIP_USER_ACTIVE_LIMIT:4}") int active,
      @Value("${OUTBOUND_USER_DAILY_TEXT_LIMIT:100}") int text,
      @Value("${OUTBOUND_USER_DAILY_EMBEDDING_LIMIT:500}") int embedding,
      @Value("${OUTBOUND_USER_DAILY_AMAP_LIMIT:1000}") int amap) {
    this.db=db;this.access=access;
    this.defaults=Map.of("daily_tasks",tasks,"active_tasks",active,"daily_text",text,"daily_embedding",embedding,"daily_amap",amap);
    if(defaults.values().stream().anyMatch(v->v<0))throw new IllegalArgumentException("User quotas cannot be negative");
  }
  public int limit(long uid,String name) {
    if(!defaults.containsKey(name))throw new IllegalArgumentException("Unknown quota");
    var rows=db.queryForList("SELECT "+name+" AS quota FROM user_operation_quotas WHERE user_id=?",uid);
    return rows.isEmpty()||rows.getFirst().get("quota")==null?defaults.get(name):((Number)rows.getFirst().get("quota")).intValue();
  }
  public void lockActive(long uid) {
    var rows=db.queryForList("SELECT account_state FROM users WHERE id=? FOR SHARE",uid);
    if(rows.isEmpty()||!"active".equals(rows.getFirst().get("account_state")))
      throw new ApiException(403,"账号已停用或正在注销，操作已停止","ACCOUNT_NOT_ACTIVE");
  }
  public void reserve(long uid,String kind,long tokens,boolean verified) {
    lockActive(uid);
    if(verified)access.requireVerified(uid);
    LocalDate today=LocalDate.now(ZoneOffset.UTC);
    db.update("INSERT INTO outbound_user_usage(usage_date,user_id,kind) VALUES(?,?,?) ON CONFLICT DO NOTHING",today,uid,kind);
    if(db.update("UPDATE outbound_user_usage SET reserved_calls=reserved_calls+1,reserved_tokens=reserved_tokens+? WHERE usage_date=? AND user_id=? AND kind=? AND reserved_calls<?",
        tokens,today,uid,kind,limit(uid,"daily_"+kind))!=1)
      throw new ApiException(429,"今日该类外部调用额度已用完（UTC日界）","USER_OUTBOUND_QUOTA");
  }
  public Map<String,Object> summary(long uid) {
    if(!Boolean.TRUE.equals(db.queryForObject("SELECT EXISTS(SELECT 1 FROM users WHERE id=?)",Boolean.class,uid)))throw new ApiException(404,"用户不存在");
    var limits=new LinkedHashMap<String,Object>();defaults.keySet().stream().sorted().forEach(k->limits.put(k,limit(uid,k)));
    var calls=db.queryForList("SELECT kind,reserved_calls,reserved_tokens FROM outbound_user_usage WHERE user_id=? AND usage_date=? ORDER BY kind",uid,LocalDate.now(ZoneOffset.UTC));
    long tasks=db.queryForObject("SELECT count(*) FROM trip_tasks WHERE user_id=? AND created_at>=date_trunc('day',timezone('UTC',now()))",Long.class,uid);
    long active=db.queryForObject("SELECT count(*) FROM trip_tasks WHERE user_id=? AND status IN ('queued','running')",Long.class,uid);
    var rows=db.queryForList("SELECT daily_tasks,active_tasks,daily_text,daily_embedding,daily_amap FROM user_operation_quotas WHERE user_id=?",uid);
    Map<String,Object> overrides=new LinkedHashMap<>();defaults.keySet().forEach(key->overrides.put(key,rows.isEmpty()?null:rows.getFirst().get(key)));
    var supplier=db.queryForMap("SELECT COALESCE(sum(supplier_input_tokens),0) AS input_tokens,COALESCE(sum(supplier_output_tokens),0) AS output_tokens,"+
        "count(*) FILTER(WHERE supplier_reported_at IS NOT NULL) AS reported_calls,count(*) FILTER(WHERE supplier_reported_at IS NULL) AS unreported_calls,"+
        "count(*) FILTER(WHERE supplier_reported_at IS NOT NULL AND execution_id IS NULL) AS taskless_reported_calls,"+
        "count(*) FILTER(WHERE supplier_reported_at IS NOT NULL AND execution_id IS NOT NULL) AS execution_reported_calls "+
        "FROM outbound_reservations WHERE user_id=? AND kind IN ('text','embedding') AND reserved_at>=?",uid,LocalDate.now(ZoneOffset.UTC).atStartOfDay().atOffset(ZoneOffset.UTC));
    supplier.put("note","仅汇总供应商已报告的调用用量，不估算缺失数据。规划流式用量另见usage/summary；两处可能描述同一次调用，不能相加计费。");
    return Map.of("limits",limits,"overrides",overrides,"reserved_calls",calls,"tasks_today",tasks,"active_tasks",active,
        "usage_date",LocalDate.now(ZoneOffset.UTC).toString(),"supplier_usage",supplier,"note","调用预留在发送前计入；失败不推断未计费，不等同供应商实际账单。视觉调用归入text额度。");
  }
  public Object update(long uid,Map<String,Integer> changes) {
    if(changes.size()!=defaults.size()||!changes.keySet().equals(defaults.keySet()))throw new ApiException(422,"请提交完整配额字段，null表示继承部署默认值");
    for(var entry:changes.entrySet())if(entry.getValue()!=null&&(entry.getValue()<0||entry.getValue()>(entry.getKey().equals("active_tasks")?1000:entry.getKey().equals("daily_tasks")?100000:1000000)))
      throw new ApiException(422,"配额超出允许范围");
    db.update("INSERT INTO user_operation_quotas(user_id,daily_tasks,active_tasks,daily_text,daily_embedding,daily_amap) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET daily_tasks=excluded.daily_tasks,active_tasks=excluded.active_tasks,daily_text=excluded.daily_text,daily_embedding=excluded.daily_embedding,daily_amap=excluded.daily_amap,updated_at=timezone('UTC',now())",
        uid,changes.get("daily_tasks"),changes.get("active_tasks"),changes.get("daily_text"),changes.get("daily_embedding"),changes.get("daily_amap"));
    return summary(uid);
  }
}
