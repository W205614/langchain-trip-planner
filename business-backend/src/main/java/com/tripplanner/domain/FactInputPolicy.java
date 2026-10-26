package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.time.*;
import java.util.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** User facts have provenance and freshness; no user declaration becomes an official fact. */
public final class FactInputPolicy {
  private FactInputPolicy() {}
  public static void validate(ObjectNode input,int days) {
    for(String group:List.of("reservation_facts","transport_buffers","poi_relations","meal_resolutions")){
      if(!input.has(group))input.putArray(group);
      if(!input.path(group).isArray()||input.path(group).size()>300)throw bad("补充事实数量无效");
      var identities=new HashSet<String>();
      for(var raw:input.path(group)){
        if(!(raw instanceof ObjectNode fact))throw bad("补充事实必须为对象");
        provenance(fact);
        String identity;
        switch(group){
          case "reservation_facts" -> {String poi=text(fact,"poi_id",64);String date=text(fact,"date",10);try{LocalDate.parse(date);}catch(Exception ex){throw bad("预约适用日期无效");}choice(fact,"status",Set.of("confirmed","not_required","unavailable"));identity=poi+":"+date;}
          case "transport_buffers" -> {identity=text(fact,"commitment_id",160);integer(fact,"minutes",0,360);}
          case "poi_relations" -> {String parent=text(fact,"parent_poi_id",64),child=text(fact,"child_poi_id",64);if(parent.equals(child))throw bad("父子地点不能相同");choice(fact,"relation",Set.of("included","separate"));identity=parent.compareTo(child)<0?parent+":"+child:child+":"+parent;}
          case "meal_resolutions" -> {integer(fact,"day_index",0,days-1);choice(fact,"type",Set.of("breakfast","lunch","dinner","snack"));choice(fact,"mode",Set.of("self_arranged"));location(fact.path("location"));identity=fact.path("day_index").asText("")+":"+fact.path("type").asText("");}
          default -> throw bad("补充事实类型无效");
        }
        if(!identities.add(identity))throw bad("同一事项只保留一份补充事实，请合并冲突内容");
      }
    }
    for(var hotel:input.path("hotel_stays"))if(hotel instanceof ObjectNode h&&h.has("source"))provenance(h);
  }
  private static void provenance(ObjectNode fact){
    text(fact,"source",1000);
    if(!fact.path("confirmed").isBoolean())throw bad("请明确是否已核对补充事实");
    try{var observed=OffsetDateTime.parse(fact.path("observed_at").asText("")).toInstant();var expiry=OffsetDateTime.parse(fact.path("expires_at").asText("")).toInstant();if(observed.isAfter(Instant.now().plusSeconds(300))||!expiry.isAfter(observed))throw new IllegalArgumentException();}catch(Exception ex){throw bad("事实须包含带时区的获取时间和有效期，且有效期晚于获取时间");}
    fact.put("authority","user_declared");
  }
  private static String text(JsonNode fact,String field,int max){String value=fact.path(field).asText("").strip();if(!fact.path(field).isString()||value.isBlank()||value.length()>max)throw bad(field+" 无效");return value;}
  private static void choice(JsonNode fact,String field,Set<String> values){if(!values.contains(fact.path(field).asText("")))throw bad(field+" 无效");}
  private static void integer(JsonNode fact,String field,int min,int max){var n=fact.path(field);if(!n.isIntegralNumber()||!n.canConvertToInt()||n.asInt(0)<min||n.asInt(0)>max)throw bad(field+" 无效");}
  private static void location(JsonNode loc){if(!loc.path("longitude").isNumber()||!loc.path("latitude").isNumber()||!Double.isFinite(loc.path("longitude").asDouble())||!Double.isFinite(loc.path("latitude").asDouble())||Math.abs(loc.path("longitude").asDouble())>180||Math.abs(loc.path("latitude").asDouble())>90)throw bad("自理用餐须补充有效地点");}
  private static ApiException bad(String message){return new ApiException(422,message);}
}
