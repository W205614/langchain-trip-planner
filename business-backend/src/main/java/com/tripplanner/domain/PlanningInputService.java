package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.time.*;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.*;

/** Authoritative planning inputs and deterministic write boundaries. No external calls. */
@Service
public class PlanningInputService {
  private final JdbcTemplate db; private final JsonMapper json; private final TransactionTemplate tx;
  public PlanningInputService(JdbcTemplate db, JsonMapper json, TransactionTemplate tx) {this.db=db;this.json=json;this.tx=tx;}
  public Map<String,Object> row(long uid,long id,boolean lock) {
    var rows=db.queryForList("SELECT * FROM trip_records WHERE user_id=? AND id=?"+(lock?" FOR UPDATE":""),uid,id);
    if(rows.isEmpty())throw new ApiException(404,"行程不存在");
    return rows.getFirst();
  }
  public ObjectNode input(Map<String,Object> row) {
    var node=normalize(json.readTree(Objects.toString(row.get("planning_input"),"{}")),((Number)row.get("travel_days")).intValue());
    var commitments=node.putArray("commitments");
    for(var c:db.queryForList("SELECT * FROM trip_commitments WHERE trip_id=? ORDER BY id",row.get("id"))) {
      var item=commitments.addObject();
      for(String key:List.of("id","version","activity_id","day_index","category","status","title","poi_id","amount_cents"))item.set(key,json.valueToTree(c.get(key)));
      for(String key:List.of("start_at","end_at"))if(c.get(key)!=null)item.put(key,c.get(key) instanceof java.sql.Timestamp t?t.toInstant().toString():c.get(key).toString());
      if(c.get("details_json")!=null){var details=json.readTree(c.get("details_json").toString());for(String key:details.propertyNames())if(!Set.of("id","version","status","title","day_index","activity_id","poi_id","amount_cents","start_at","end_at").contains(key))item.set(key,details.path(key));}
      if(c.get("location_json")!=null)item.set("location",json.readTree(c.get("location_json").toString()));
    }
    // Inline commitments are valid for new plans, while persisted ledger items win on ID.
    var stored=json.readTree(Objects.toString(row.get("planning_input"),"{}"));
    for(var c:stored.path("commitments"))if(commitments.valueStream().noneMatch(x->x.path("id").asText().equals(c.path("id").asText())))commitments.add(c);
    return node;
  }
  public Map<String,Object> get(long uid,long id) {return response(row(uid,id,false));}
  private Map<String,Object> response(Map<String,Object> row) {
    return Map.of("success",true,"data",Map.of("planning_input",input(row),"planning_revision",row.get("planning_revision"),"trip_version",row.get("version"),"plan",stableIds((ObjectNode)json.readTree(row.get("plan_json").toString()))));
  }
  public Map<String,Object> put(long uid,long id,int version,int revision,JsonNode raw) {
    return tx.execute(s->{var row=row(uid,id,true);check(row,version,revision);
      var normalized=normalize(raw,((Number)row.get("travel_days")).intValue());
      if(!normalized.path("commitments").equals(input(row).path("commitments")))
        throw new ApiException(422,"预订事项请通过预订账本修改");
      var plan=stableIds((ObjectNode)json.readTree(row.get("plan_json").toString()));
      normalized.remove("commitments");
      db.update("UPDATE trip_records SET planning_input=?::jsonb,planning_revision=planning_revision+1,plan_json=? WHERE id=?",json.writeValueAsString(normalized),json.writeValueAsString(plan),id);
      return response(row(uid,id,false));});
  }
  public Map<String,Object> state(long uid,long id,int version,String activityId,ObjectNode body) {
    return tx.execute(s->{var row=row(uid,id,true);check(row,version,body.path("planning_revision").asInt(-1));
      if(!body.has("locked")&&!body.has("completed"))throw new ApiException(422,"请指定状态");
      for(String flag:List.of("locked","completed"))if(body.has(flag)&&!body.path(flag).isBoolean())throw new ApiException(422,"活动状态无效");
      var plan=stableIds((ObjectNode)json.readTree(row.get("plan_json").toString()));
      if(activities(plan).get(activityId)==null)throw new ApiException(404,"活动不存在");
      var activityDay=plan.path("days").valueStream().filter(d->activitiesForDay(d).containsKey(activityId)).findFirst().orElseThrow();
      if(activityDay.path("schedule_stale").asBoolean(false)&&(body.path("completed").asBoolean(false)||body.path("locked").asBoolean(false)&&"time".equals(body.path("lock_mode").asText())))throw new ApiException(409,"时间轴已过期，请先重新规划","SCHEDULE_STALE");
      var input=input(row);var locks=json.createArrayNode();
      for(var lock:input.path("locks"))if(!activityId.equals(lock.path("activity_id").asText()))locks.add(lock);
      if(body.path("locked").asBoolean(false)) {
        String mode=body.path("lock_mode").asText("content");if(!Set.of("content","time").contains(mode))throw new ApiException(422,"锁定类型无效");
        var lock=locks.addObject().put("activity_id",activityId).put("mode",mode);
        if(mode.equals("time")){var a=timeline(plan).getOrDefault(activityId,activities(plan).get(activityId));if(a.path("start_at").asText("").isBlank()||a.path("end_at").asText("").isBlank())throw new ApiException(422,"活动尚无可锁定时间");lock.set("start_at",a.path("start_at"));lock.set("end_at",a.path("end_at"));}
      }else if(!body.has("locked"))for(var lock:input.path("locks"))if(activityId.equals(lock.path("activity_id").asText()))locks.add(lock);
      input.set("locks",locks);var done=new LinkedHashSet<String>();for(var x:input.path("completed_activity_ids"))done.add(x.asText());
      if(body.has("completed")){if(body.path("completed").asBoolean())done.add(activityId);else done.remove(activityId);}
      input.set("completed_activity_ids",json.valueToTree(done));input.remove("commitments");
      db.update("UPDATE trip_records SET planning_input=?::jsonb,planning_revision=planning_revision+1,plan_json=? WHERE id=?",json.writeValueAsString(input),json.writeValueAsString(plan),id);
      return response(row(uid,id,false));});
  }
  private void check(Map<String,Object> row,int version,int revision) {
    if(version!=((Number)row.get("version")).intValue()||revision!=((Number)row.get("planning_revision")).intValue())throw new ApiException(409,"行程或规划输入已变化","VERSION_CONFLICT");
    if(!json.readTree(row.get("quality_json").toString()).path("assistant_proposal_status").asText("").isBlank())throw new ApiException(409,"提案不能直接修改规划输入");
  }
  public static ObjectNode normalize(JsonNode raw,int days) {
    if(!raw.isObject())throw new ApiException(422,"planning_input 必须为对象");
    ObjectNode p=((ObjectNode)raw).deepCopy();
    if(!p.has("timezone"))p.put("timezone","Asia/Shanghai");
    try{ZoneId.of(p.path("timezone").asText());}catch(Exception ex){throw new ApiException(422,"时区无效");}
    if(!p.has("day_start"))p.put("day_start","09:00");if(!p.has("day_end"))p.put("day_end","20:00");
    interval(p,"day_start","day_end");
    for(String name:List.of("day_windows","city_segments","commitments","hotel_stays","meal_windows","opening_windows","locks","completed_activity_ids")){
      if(!p.has(name))p.putArray(name);if(!p.path(name).isArray()||p.path(name).size()>300)throw new ApiException(422,name+" 无效");
    }
    for(String name:List.of("day_windows","commitments","hotel_stays"))for(var item:p.path(name)){
      if(!item.isObject()||!item.path("day_index").isIntegralNumber()||item.path("day_index").asInt(-1)<0||item.path("day_index").asInt()>=days)throw new ApiException(422,name+" 日期无效");
      if(name.equals("day_windows"))interval(item,"start","end");
    }
    for(var seg:p.path("city_segments"))if(seg.path("city").asText("").isBlank()||seg.path("start_day").asInt(-1)<0||seg.path("end_day").asInt(-1)<seg.path("start_day").asInt()||seg.path("end_day").asInt()>=days)throw new ApiException(422,"城市分段无效");
    var occupied=new HashSet<Integer>();for(var seg:p.path("city_segments"))for(int i=seg.path("start_day").asInt();i<=seg.path("end_day").asInt();i++)if(!occupied.add(i))throw new ApiException(422,"城市分段不可重叠");
    for(var meal:p.path("meal_windows")){interval(meal,"start","end");if(!Set.of("breakfast","lunch","dinner","snack").contains(meal.path("type").asText())||!meal.path("duration_minutes").isIntegralNumber()||meal.path("duration_minutes").asInt(0)<=0||meal.path("duration_minutes").asInt()>240)throw new ApiException(422,"用餐时长无效");}
    for(var c:p.path("commitments")) {
      if(!c.isObject()||!Set.of("ticket","lodging","transport","meal","other").contains(c.path("category").asText("other"))||!Set.of("planned","confirmed","cancelled").contains(c.path("status").asText("planned")))throw new ApiException(422,"预订事项类别或状态无效");
      if(c.path("title").asText("").length()>160||c.path("activity_id").asText("").length()>160||c.path("poi_id").asText("").length()>64)throw new ApiException(422,"预订事项字段过长");
      if(c.path("id").asText("").isBlank())throw new ApiException(422,"预订事项必须包含 ID");
      if(!Set.of("fixed","entry_window").contains(c.path("time_mode").asText("fixed")))throw new ApiException(422,"预约时间模式无效");
      dateInterval(c);
      if(c.hasNonNull("amount_cents")&&(!c.path("amount_cents").isIntegralNumber()||c.path("amount_cents").asLong(-1)<0))throw new ApiException(422,"金额必须为非负整数分");
    }
    for(String field:List.of("arrival_at","departure_at"))if(p.hasNonNull(field))awareDate(p.path(field).asText());
    if(p.hasNonNull("arrival_at")&&p.hasNonNull("departure_at")&&!parseDate(p.path("departure_at").asText(),p.path("timezone").asText()).isAfter(parseDate(p.path("arrival_at").asText(),p.path("timezone").asText())))throw new ApiException(422,"到离时间顺序无效");
    for(var fact:p.path("opening_windows")){
      if(fact.path("poi_id").asText("").isBlank()||!fact.path("windows").isArray())throw new ApiException(422,"营业事实必须包含地点与时间窗口");
      try{LocalDate.parse(fact.path("date").asText());}catch(Exception ex){throw new ApiException(422,"营业事实适用日期无效");}
      for(var w:fact.path("windows"))interval(w,"start","end");
      for(String f:List.of("observed_at","expires_at"))if(fact.hasNonNull(f))parseDate(fact.path(f).asText(),p.path("timezone").asText());
    }
    if(p.hasNonNull("revision_scope")){
      if(!p.path("revision_scope").isObject())throw new ApiException(422,"改排范围无效");
      for(String f:List.of("day_indices","activity_ids"))if(p.path("revision_scope").has(f)&&!p.path("revision_scope").path(f).isArray())throw new ApiException(422,"改排范围无效");
      var scope=p.path("revision_scope");if(scope.hasNonNull("start_at")!=scope.hasNonNull("end_at"))throw new ApiException(422,"修改时间段必须包含起止时间");if(scope.hasNonNull("start_at")&&!awareDate(scope.path("end_at").asText()).isAfter(awareDate(scope.path("start_at").asText())))throw new ApiException(422,"修改时间段无效");
      for(var d:p.path("revision_scope").path("day_indices"))if(!d.isIntegralNumber()||d.asInt()<0||d.asInt()>=days)throw new ApiException(422,"改排日期无效");
    }
    for(var lock:p.path("locks"))if(lock.path("activity_id").asText("").isBlank()||!Set.of("content","time").contains(lock.path("mode").asText("content")))throw new ApiException(422,"锁定事项无效");
    if(!p.has("budget_mode"))p.put("budget_mode","estimate");if(!Set.of("estimate","confirmed").contains(p.path("budget_mode").asText()))throw new ApiException(422,"预算模式无效");
    if(p.hasNonNull("budget_cents")&&(!p.path("budget_cents").isIntegralNumber()||p.path("budget_cents").asLong(-1)<0))throw new ApiException(422,"预算必须为非负整数分");
    return p;
  }
  private static void interval(JsonNode p,String a,String b){try{if(!p.path(a).asText().matches("\\d{2}:\\d{2}")||!p.path(b).asText().matches("\\d{2}:\\d{2}")||!LocalTime.parse(p.path(b).asText()).isAfter(LocalTime.parse(p.path(a).asText())))throw new IllegalArgumentException();}catch(Exception ex){throw new ApiException(422,"时间窗口无效");}}
  private static Instant parseDate(String s,String zone){try{return OffsetDateTime.parse(s).toInstant();}catch(Exception ex){try{return LocalDateTime.parse(s).atZone(ZoneId.of(zone)).toInstant();}catch(Exception e){throw new ApiException(422,"日期时间无效");}}}
  private static Instant awareDate(String value){try{return OffsetDateTime.parse(value).toInstant();}catch(Exception ex){throw new ApiException(422,"日期时间须包含有效时区");}}
  private static void dateInterval(JsonNode c){if(c.hasNonNull("start_at")!=c.hasNonNull("end_at"))throw new ApiException(422,"预约必须包含起止时间");if(c.hasNonNull("start_at")&&!awareDate(c.path("end_at").asText()).isAfter(awareDate(c.path("start_at").asText())))throw new ApiException(422,"预约时间顺序无效");}
  private static void defaultText(ObjectNode node,String field,String value){if(!node.has(field))node.put(field,value);}
  private static void normalizeLocation(JsonNode value){if(value instanceof ObjectNode location)for(String field:List.of("longitude","latitude"))if(location.path(field).isNumber())location.put(field,location.path(field).asDouble());}
  public static ObjectNode stableIds(ObjectNode plan) {
    var used=new HashSet<String>();
    for(var d:plan.path("days"))if(d instanceof ObjectNode day){
      for(String field:List.of("city","theme"))defaultText(day,field,"");defaultText(day,"generation_mode","llm");
      for(String field:List.of("timeline","activities","attractions","meals"))if(!day.has(field))day.putArray(field);
      if(!day.has("feasibility"))day.putObject("feasibility");if(!day.has("schedule_stale"))day.put("schedule_stale",false);
      for(String field:List.of("hotel","fallback_reason"))if(!day.has(field))day.putNull(field);
      if(day.path("hotel") instanceof ObjectNode hotel){for(String field:List.of("address","price_range","rating","distance","type"))defaultText(hotel,field,"");if(!hotel.has("location"))hotel.putNull("location");if(!hotel.has("estimated_cost"))hotel.put("estimated_cost",0);normalizeLocation(hotel.path("location"));}
      for(var meal:day.path("meals"))if(meal instanceof ObjectNode m){for(String field:List.of("address","location","description"))if(!m.has(field))m.putNull(field);for(String field:List.of("poi_id","opening_hours","fact_source"))defaultText(m,field,"");if(!m.has("estimated_cost"))m.put("estimated_cost",0);normalizeLocation(m.path("location"));}
      for(var attr:day.path("attractions"))if(attr instanceof ObjectNode a){defaultText(a,"category","景点");defaultText(a,"price_source","unknown");for(String field:List.of("opening_hours","fact_source"))defaultText(a,field,"");for(String field:List.of("rating","image_url"))if(!a.has(field))a.putNull(field);for(String field:List.of("photos","requested_names"))if(!a.has(field))a.putArray(field);if(!a.has("ticket_price"))a.put("ticket_price",0);if(a.path("rating").isNumber())a.put("rating",a.path("rating").asDouble());normalizeLocation(a.path("location"));}
    }

    for(var day:plan.path("days"))for(String group:List.of("attractions","meals"))for(var a:day.path(group))if(a instanceof ObjectNode o){
      if(group.equals("attractions")){if(!o.has("optional"))o.put("optional",true);if(!o.has("priority"))o.put("priority",50);}
      String id=a.path("activity_id").asText("");
      if(id.isBlank()){id=group.equals("attractions")?"poi:"+day.path("day_index").asInt()+":"+a.path("poi_id").asText("unknown"):"meal:"+day.path("day_index").asInt()+":"+a.path("type").asText("meal")+":"+a.path("poi_id").asText("unknown");o.put("activity_id",id);}
      if(id.length()>160)throw new ApiException(422,"活动 ID 过长");
      if(!used.add(id))throw new ApiException(422,"活动 ID 重复","DUPLICATE_ACTIVITY_ID");
    }return plan;
  }
  public static Map<String,JsonNode> activities(JsonNode plan){var r=new LinkedHashMap<String,JsonNode>();for(var d:plan.path("days"))for(String group:List.of("attractions","meals"))for(var a:d.path(group))r.put(a.path("activity_id").asText(),a);return r;}
  private static Map<String,JsonNode> timeline(JsonNode plan){var out=new HashMap<String,JsonNode>();for(var d:plan.path("days"))for(var a:d.path("timeline"))if(!a.path("activity_id").asText("").isBlank())out.put(a.path("activity_id").asText(),a);return out;}
  private static Map<String,Integer> placement(JsonNode plan){var out=new HashMap<String,Integer>();for(var d:plan.path("days")){for(var a:activitiesForDay(d).keySet())out.put(a,d.path("day_index").asInt());for(var a:d.path("timeline"))out.putIfAbsent(a.path("activity_id").asText(),d.path("day_index").asInt());}return out;}
  private static ApiException boundary(){return new ApiException(409,"改排修改了授权范围外内容","REVISION_SCOPE_VIOLATION");}
  public void protect(long uid,long id,JsonNode proposed,JsonNode scope,boolean revision) {
    var row=row(uid,id,true);var original=stableIds((ObjectNode)json.readTree(row.get("plan_json").toString()));
    var next=stableIds((ObjectNode)proposed);var input=input(row);var old=activities(original);var changed=activities(next);
    var oldTimes=timeline(original);var newTimes=timeline(next);var oldDays=placement(original);var newDays=placement(next);
    var protectedIds=new HashSet<String>();var timeIds=new HashSet<String>();var immutableDays=new HashSet<String>();var commitmentDays=new HashMap<String,Integer>();var commitmentTimes=new HashMap<String,JsonNode>();
    for(var v:input.path("completed_activity_ids")){protectedIds.add(v.asText());timeIds.add(v.asText());}
    for(var l:input.path("locks")){String key=l.path("activity_id").asText();protectedIds.add(key);if(l.path("mode").asText().equals("time"))timeIds.add(key);}
    immutableDays.addAll(protectedIds);
    for(var c:input.path("commitments"))if("confirmed".equals(c.path("status").asText())){
      var keys=new HashSet<String>();String specified=c.path("activity_id").asText("");if(!specified.isBlank()&&(old.containsKey(specified)||oldTimes.containsKey(specified)||newTimes.containsKey(specified)))keys.add(specified);
      if(!c.path("poi_id").asText("").isBlank())old.forEach((key,a)->{if(c.path("poi_id").equals(a.path("poi_id")))keys.add(key);});
      if(oldTimes.containsKey("commitment:"+c.path("id").asText()))keys.add("commitment:"+c.path("id").asText());
      if(keys.isEmpty()&&!specified.isBlank())keys.add(specified);
      protectedIds.addAll(keys);for(String key:keys){commitmentDays.put(key,c.path("day_index").asInt());if(c.hasNonNull("start_at")&&c.hasNonNull("end_at"))commitmentTimes.put(key,c);}
    }
    for(String activity:protectedIds)if(old.containsKey(activity)&&(!old.get(activity).equals(changed.get(activity))||!Objects.equals(immutableDays.contains(activity)?oldDays.get(activity):commitmentDays.getOrDefault(activity,oldDays.get(activity)),newDays.get(activity))))throw new ApiException(409,"锁定、已完成或预约活动发生变化","PROTECTED_ACTIVITY_CHANGED");
    for(String activity:timeIds)if(oldTimes.containsKey(activity)) {
      var a=oldTimes.get(activity);var b=newTimes.get(activity);
      if(b==null||!Objects.equals(oldDays.get(activity),newDays.get(activity))||(!old.containsKey(activity)&&!a.equals(b))||!a.path("start_at").equals(b.path("start_at"))||!a.path("end_at").equals(b.path("end_at")))throw new ApiException(409,"固定活动时间发生变化","PROTECTED_ACTIVITY_CHANGED");
    }
    for(var entry:commitmentTimes.entrySet()){
      var actual=newTimes.get(entry.getKey());var expected=entry.getValue();
      if(actual==null||!Objects.equals(commitmentDays.get(entry.getKey()),newDays.get(entry.getKey())))throw new ApiException(409,"提案未保留当前已确认预约时间","COMMITMENT_TIME_VIOLATION");
      String zone=input.path("timezone").asText();var actualStart=parseDate(actual.path("start_at").asText(),zone);var actualEnd=parseDate(actual.path("end_at").asText(),zone);var expectedStart=parseDate(expected.path("start_at").asText(),zone);var expectedEnd=parseDate(expected.path("end_at").asText(),zone);
      boolean valid=expected.path("time_mode").asText("fixed").equals("entry_window")?!actualStart.isBefore(expectedStart)&&!actualStart.isAfter(expectedEnd)&&actualEnd.isAfter(actualStart):actualStart.equals(expectedStart)&&actualEnd.equals(expectedEnd);
      if(!valid)throw new ApiException(409,"提案未保留当前已确认预约时间","COMMITMENT_TIME_VIOLATION");
    }
    // Protected items keep their relative order even when unprotected items are replaced.
    for(var day:original.path("days")){
      var before=new ArrayList<String>();var after=new ArrayList<String>();
      for(var a:day.path("attractions"))if(protectedIds.contains(a.path("activity_id").asText())&&(immutableDays.contains(a.path("activity_id").asText())||!commitmentDays.containsKey(a.path("activity_id").asText())||Objects.equals(commitmentDays.get(a.path("activity_id").asText()),oldDays.get(a.path("activity_id").asText()))))before.add(a.path("activity_id").asText());
      var nd=next.path("days").get(day.path("day_index").asInt());if(nd!=null)for(var a:nd.path("attractions"))if(protectedIds.contains(a.path("activity_id").asText())&&(immutableDays.contains(a.path("activity_id").asText())||!commitmentDays.containsKey(a.path("activity_id").asText())||Objects.equals(commitmentDays.get(a.path("activity_id").asText()),oldDays.get(a.path("activity_id").asText()))))after.add(a.path("activity_id").asText());
      if(!before.equals(after))throw new ApiException(409,"固定活动顺序发生变化","PROTECTED_ACTIVITY_CHANGED");
    }
    if(!revision)return;
    var days=new HashSet<Integer>();for(var d:scope.path("day_indices")){if(!d.isIntegralNumber()||d.asInt()<0||d.asInt()>=original.path("days").size())throw new ApiException(422,"修改范围日期无效");days.add(d.asInt());}
    var ids=new HashSet<String>();for(var a:scope.path("activity_ids")){if(!old.containsKey(a.asText()))throw new ApiException(422,"修改范围活动不存在");ids.add(a.asText());days.add(oldDays.get(a.asText()));}
    if(days.isEmpty())throw new ApiException(422,"修改范围不能为空");
    Instant start=scope.hasNonNull("start_at")?parseDate(scope.path("start_at").asText(),input.path("timezone").asText()):null;
    Instant end=scope.hasNonNull("end_at")?parseDate(scope.path("end_at").asText(),input.path("timezone").asText()):null;
    if(start!=null&&end!=null&&!end.isAfter(start))throw new ApiException(422,"修改时间范围无效");
    var allowed=new HashSet<String>();
    for(var day:original.path("days")){
      int index=day.path("day_index").asInt();var after=next.path("days").get(index);
      if(!days.contains(index)){if(!day.equals(after))throw boundary();continue;}
      for(String f:List.of("day_index","date","city"))if(!day.path(f).equals(after.path(f)))throw boundary();
      for(var a:activitiesForDay(day).entrySet()){
        String aid=a.getKey();boolean inside=ids.isEmpty()||ids.contains(aid);
        if(start!=null||end!=null){var t=oldTimes.get(aid);if(t==null)inside=false;else{var begin=parseDate(t.path("start_at").asText(),input.path("timezone").asText());var finish=parseDate(t.path("end_at").asText(),input.path("timezone").asText());inside&=(start==null||!begin.isBefore(start))&&(end==null||!finish.isAfter(end));}}
        if(inside)allowed.add(aid);
        else if(!a.getValue().equals(changed.get(aid))||!Objects.equals(oldDays.get(aid),newDays.get(aid))||!Objects.equals(oldTimes.get(aid),newTimes.get(aid)))throw boundary();
      }
    }
    for(var entry:changed.entrySet())if(!old.containsKey(entry.getKey())) {
      if(!days.contains(newDays.get(entry.getKey())))throw boundary();
      if(start!=null||end!=null){var t=newTimes.get(entry.getKey());if(t==null)throw boundary();var begin=parseDate(t.path("start_at").asText(),input.path("timezone").asText());var finish=parseDate(t.path("end_at").asText(),input.path("timezone").asText());if(start!=null&&begin.isBefore(start)||end!=null&&finish.isAfter(end))throw boundary();}
    }
  }
  private static Map<String,JsonNode> activitiesForDay(JsonNode day){var out=new HashMap<String,JsonNode>();if(day!=null)for(String group:List.of("attractions","meals"))for(var a:day.path(group))out.put(a.path("activity_id").asText(),a);return out;}
  /** Manual edits may retain an old timeline for comparison, but never its validated status. */
  public void invalidateManualSchedule(JsonNode original,ObjectNode plan,ObjectNode quality,Set<Integer> restricted) {
    original=stableIds((ObjectNode)original.deepCopy());
    boolean stale=false;
    for(var raw:plan.path("days"))if(raw instanceof ObjectNode day){
      int index=day.path("day_index").asInt();if(restricted!=null&&!restricted.contains(index))continue;
      var old=original.path("days").get(index);if(old==null||day.path("timeline").isEmpty())continue;
      boolean changed=false;for(String field:List.of("date","city","attractions","meals","route_legs","hotel"))if(!old.path(field).equals(day.path(field)))changed=true;
      if(!changed)continue;
      stale=true;day.put("schedule_stale",true);
      var feasibility=day.path("feasibility") instanceof ObjectNode f?f:day.putObject("feasibility");feasibility.put("status","unverified").put("solver_status","MANUAL_CHANGE");feasibility.putArray("conflicts").add("手工调整或事实更新后，原时间轴仅供对照，请重新规划");
      for(var entry:day.path("timeline"))if(entry instanceof ObjectNode e){e.put("status","unverified");e.withArray("notes").add("schedule_stale_after_manual_change");}
    }
    if(stale){quality.put("feasibility_status","unverified").put("facts_complete",false);if("complete".equals(quality.path("outcome").asText()))quality.put("outcome","degraded");quality.withArray("data_gaps").add("schedule_stale_after_manual_change");}
  }
  public ObjectNode dependencies(JsonNode plan,JsonNode input) {
    var deps=json.createObjectNode();var facts=deps.putArray("facts");var now=Instant.now();
    for(var day:plan.path("days"))for(var entry:day.path("timeline"))if(Set.of("attraction","meal").contains(entry.path("kind").asText("")))for(var ref:entry.path("evidence_refs"))for(var fact:input.path("opening_windows")){
      if(ref.asText().equals(fact.path("poi_id").asText())&&day.path("date").asText().equals(fact.path("date").asText())&&fact.path("confirmed").asBoolean(false)){
        try{var observed=parseDate(fact.path("observed_at").asText(),input.path("timezone").asText("Asia/Shanghai"));var expiry=observed.plus(Duration.ofHours(24));if(fact.hasNonNull("expires_at")){var declared=parseDate(fact.path("expires_at").asText(),input.path("timezone").asText("Asia/Shanghai"));if(declared.isBefore(expiry))expiry=declared;}if(expiry.isAfter(now)){var copy=((ObjectNode)fact).deepCopy();copy.put("expires_at",expiry.toString());facts.add(copy);}}catch(ApiException ignored){}
      }
    }
    return deps;
  }
  public void validateDependencies(JsonNode quality){
    for(var fact:quality.path("planning_dependencies").path("facts"))if(!parseDate(fact.path("expires_at").asText(),"Asia/Shanghai").isAfter(Instant.now()))throw new ApiException(409,"提案依赖事实已过期，请重新核验","FACTS_EXPIRED");
  }
  public void saveInput(long id,JsonNode input){
    var normalized=(ObjectNode)input.deepCopy();
    for(var c:input.path("commitments")){
      var detail=((ObjectNode)c).deepCopy();detail.put("input_commitment_id",c.path("id").asText());
      db.update("INSERT INTO trip_commitments(trip_id,day_index,category,title,status,amount_cents,created_by,activity_id,poi_id,location_json,start_at,end_at,details_json) SELECT ?,?,?,?,?,?,user_id,?,?,?::jsonb,?,?,?::jsonb FROM trip_records WHERE id=?",id,c.path("day_index").asInt(),c.path("category").asText("other"),c.path("title").asText("固定安排"),c.path("status").asText("planned"),c.hasNonNull("amount_cents")?c.path("amount_cents").asLong():null,c.path("activity_id").asText("").isBlank()?"commitment:"+c.path("id").asText():c.path("activity_id").asText(),c.path("poi_id").asText(""),c.hasNonNull("location")?json.writeValueAsString(c.path("location")):null,c.hasNonNull("start_at")?parseDate(c.path("start_at").asText(),input.path("timezone").asText("Asia/Shanghai")).atOffset(ZoneOffset.UTC):null,c.hasNonNull("end_at")?parseDate(c.path("end_at").asText(),input.path("timezone").asText("Asia/Shanghai")).atOffset(ZoneOffset.UTC):null,json.writeValueAsString(detail),id);
    }
    normalized.remove("commitments");
    db.update("UPDATE trip_records SET planning_input=?::jsonb WHERE id=?",json.writeValueAsString(normalized),id);
  }
  public void checkRevision(long uid,long id,int revision){if(((Number)row(uid,id,true).get("planning_revision")).intValue()!=revision)throw new ApiException(409,"规划约束或预订事项已变化","PLANNING_INPUT_CONFLICT");}
  public ObjectNode differences(JsonNode before,JsonNode after){
    var diff=json.createObjectNode();var added=diff.putArray("added");var removed=diff.putArray("removed");var modified=diff.putArray("modified");var days=diff.putArray("days");var a=activities(before);var b=activities(after);
    b.forEach((id,item)->{if(!a.containsKey(id))added.add(item);else if(!a.get(id).equals(item)){var change=modified.addObject().put("activity_id",id);change.set("before",a.get(id));change.set("after",item);}});
    a.forEach((id,item)->{if(!b.containsKey(id))removed.add(item);});
    for(int i=0;i<Math.max(before.path("days").size(),after.path("days").size());i++){
      var old=before.path("days").get(i);var next=after.path("days").get(i);if(!Objects.equals(old,next)){
        var day=days.addObject().put("day_index",i);day.set("before",old);day.set("after",next);
        var names=new ArrayList<String>();if(next!=null)for(var entry:next.path("attractions"))names.add(entry.path("name").asText());
        day.put("summary","第"+(i+1)+"天安排或时间发生变化："+String.join(" → ",names));
      }
    }
    if(before.path("budget").hasNonNull("total")&&after.path("budget").hasNonNull("total"))diff.put("cost_delta_cents",(after.path("budget").path("total").asLong()-before.path("budget").path("total").asLong())*100);
    return diff;
  }
}
