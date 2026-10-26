package com.tripplanner.domain;

import java.time.*;
import java.util.*;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.*;

/** Rechecks the recorded timeline; it does not claim to solve or alter its activities. */
public final class TimelineVerificationService {
  private final JsonMapper json;
  public TimelineVerificationService(JsonMapper json){this.json=json;}
  public void apply(ObjectNode plan,ObjectNode request,PlanRules.Routes routes,ObjectNode quality){
    var inputs=request.path("planning_input");var reports=quality.putArray("time_reverification");
    var zone=ZoneId.of(inputs.path("timezone").asText("Asia/Shanghai"));
    for(var day:plan.path("days")){
      int index=day.path("day_index").asInt(0);String city=PlanningSemantics.city(request,day),mode=PlanRules.routeType(day.path("transportation").asText(request.path("transportation").asText("")));
      var report=reports.addObject().put("day_index",index).put("city",city).put("route_type",mode).put("contract_version",PlanningSemantics.CONTRACT.path("version").asText(""));
      var unknown=report.putArray("unknown");var conflicts=report.putArray("conflicts");var verifiedRoutes=report.putArray("routes");
      boolean provenConflict=false;
      String date=day.path("date").asText("");LocalDate localDate;
      try{localDate=LocalDate.parse(date);}catch(Exception ex){unknown.add("date_unknown");report.put("status","unverified");continue;}
      try{if(!LocalDate.parse(request.path("start_date").asText("")).plusDays(index).equals(localDate)){conflicts.add("date_mismatch");provenConflict=true;}}catch(Exception ex){unknown.add("request_date_unknown");}
      for(var attraction:day.path("attractions")){
        String poi=attraction.path("poi_id").asText("");
        var openings=inputs.path("opening_windows").valueStream().filter(f->poi.equals(f.path("poi_id").asText(""))&&date.equals(f.path("date").asText(""))&&PlanningSemantics.current(f,"opening")).toList();
        if(openings.size()!=1)unknown.add("opening_unknown_or_stale:"+poi);else if(openings.getFirst().path("closed").asBoolean(false)){conflicts.add("venue_closed:"+poi);provenConflict=true;}
        var reservations=inputs.path("reservation_facts").valueStream().filter(f->poi.equals(f.path("poi_id").asText(""))&&date.equals(f.path("date").asText(""))&&PlanningSemantics.current(f,"verification")).toList();
        if(reservations.size()!=1)unknown.add("reservation_unverified:"+poi);else if("unavailable".equals(reservations.getFirst().path("status").asText(""))){conflicts.add("reservation_unavailable:"+poi);provenConflict=true;}
      }
      var selected=new HashSet<String>();day.path("attractions").forEach(a->selected.add(a.path("poi_id").asText("")));
      for(var relation:inputs.path("poi_relations"))if(selected.contains(relation.path("parent_poi_id").asText(""))&&selected.contains(relation.path("child_poi_id").asText(""))){
        if(!PlanningSemantics.current(relation,"verification"))unknown.add("poi_relationship_unverified");else if("included".equals(relation.path("relation").asText(""))){conflicts.add("parent_child_duplicate_visit");provenConflict=true;}
      }
      var rows=new ArrayList<JsonNode>();day.path("timeline").forEach(rows::add);
      rows.sort(Comparator.comparing(row->row.path("start_at").asText("")));
      if(rows.isEmpty())unknown.add("schedule_unknown_or_stale");
      JsonNode previous=null;Instant first=null,last=null,knownFirst=null,knownLast=null;
      for(var row:rows){
        try{
          var start=OffsetDateTime.parse(row.path("start_at").asText("")).toInstant();var end=OffsetDateTime.parse(row.path("end_at").asText("")).toInstant();
          if(first==null||start.isBefore(first))first=start;if(last==null||end.isAfter(last))last=end;
          if("confirmed".equals(row.path("status").asText(""))){if(knownFirst==null||start.isBefore(knownFirst))knownFirst=start;if(knownLast==null||end.isAfter(knownLast))knownLast=end;}
          if(!end.isAfter(start))conflicts.add("invalid_timeline_interval");
          if(!"confirmed".equals(row.path("status").asText("")))unknown.add("timeline_condition_unverified:"+row.path("activity_id").asText(""));
          var location=row.path("location");
          if(location.isObject()){
            if(previous!=null){
              try{var route=routes==null?null:routes.get(previous.path("location"),location,mode,city);
                if(route==null||!route.path("duration").isNumber()||route.path("duration").asDouble()<0)throw new IllegalArgumentException();
                double seconds=route.path("duration").asDouble();
                var available=Duration.between(OffsetDateTime.parse(previous.path("end_at").asText("")).toInstant(),start).getSeconds();
                verifiedRoutes.addObject().put("from",previous.path("activity_id").asText("")).put("to",row.path("activity_id").asText("")).put("city",city).put("seconds",seconds);
                if(seconds>available){conflicts.add("recorded_transfer_too_short:"+row.path("activity_id").asText(""));if("confirmed".equals(row.path("status").asText(""))&&"confirmed".equals(previous.path("status").asText("")))provenConflict=true;}
              }catch(Exception ex){unknown.add("route_unverified:"+row.path("activity_id").asText(""));}
            }
            previous=row;
          }
        }catch(Exception ex){unknown.add("timeline_time_unknown");}
      }
      for(var trip:inputs.path("commitments")){
        if(!"transport".equals(trip.path("category").asText(""))||!"confirmed".equals(trip.path("status").asText("")))continue;
        try{
          var start=OffsetDateTime.parse(trip.path("start_at").asText("")).toInstant();var end=OffsetDateTime.parse(trip.path("end_at").asText("")).toInstant();
          if(!start.atZone(zone).toLocalDate().equals(localDate)&&!end.atZone(zone).toLocalDate().equals(localDate))continue;
          boolean relevant=city.equals(trip.path("origin_city").asText(""))||city.equals(trip.path("destination_city").asText(""));if(!relevant)continue;
          String begin=inputs.path("day_start").asText("09:00"),finish=inputs.path("day_end").asText("20:00");
          for(var window:inputs.path("day_windows"))if(window.path("day_index").asInt(-1)==index){begin=window.path("start").asText(begin);finish=window.path("end").asText(finish);}
          var earliest=localDate.atTime(LocalTime.parse(begin)).atZone(zone).toInstant();var latest=localDate.atTime(LocalTime.parse(finish)).atZone(zone).toInstant();
          if(start.isBefore(earliest)||end.isAfter(latest)){conflicts.add("fixed_transport_outside_day_window");provenConflict=true;}
          for(var activity:rows)if(Set.of("attraction","meal").contains(activity.path("kind").asText(""))){
            try{var a=OffsetDateTime.parse(activity.path("start_at").asText("")).toInstant();var b=OffsetDateTime.parse(activity.path("end_at").asText("")).toInstant();if(a.isBefore(end)&&b.isAfter(start)){conflicts.add("activity_overlaps_fixed_transport:"+activity.path("activity_id").asText(""));if("confirmed".equals(activity.path("status").asText("")))provenConflict=true;else unknown.add("activity_time_unverified");}}catch(Exception ex){unknown.add("timeline_time_unknown");}
          }
          if(first==null||start.isBefore(first))first=start;if(last==null||end.isAfter(last))last=end;
          if(knownFirst==null||start.isBefore(knownFirst))knownFirst=start;if(knownLast==null||end.isAfter(knownLast))knownLast=end;
          var buffer=inputs.path("transport_buffers").valueStream().filter(f->(f.path("commitment_id").asText("").equals(trip.path("id").asText(""))||f.path("commitment_id").asText("").equals(trip.path("input_commitment_id").asText("")))&&PlanningSemantics.current(f,"verification")).toList();
          int minutes=buffer.size()==1?buffer.getFirst().path("minutes").asInt(0):0;if(buffer.size()!=1)unknown.add("transport_predeparture_buffer_unverified:"+trip.path("id").asText(""));
          JsonNode hotel=null;int hotelDay=Math.max(0,index-1);
          for(var h:inputs.path("hotel_stays"))if(h.path("day_index").asInt(-1)==hotelDay)hotel=h;
          String hotelCity=hotel==null?"":hotel.path("city").asText("");if(hotelCity.isBlank())hotelCity=PlanningSemantics.city(request,json.createObjectNode().put("day_index",hotelDay));
          if(hotel==null||!hotel.path("location").isObject()||!trip.path("origin_location").isObject()||!hotelCity.equals(trip.path("origin_city").asText(""))||(hotel.has("source")&&!PlanningSemantics.current(hotel,"verification")))unknown.add("origin_hotel_endpoint_unknown");
          else try{
            var originCity=trip.path("origin_city").asText("");var route=routes==null?null:routes.get(hotel.path("location"),trip.path("origin_location"),mode,originCity);
            if(route==null||!route.path("duration").isNumber()||route.path("duration").asDouble()<0)throw new IllegalArgumentException();
            var leave=start.minusSeconds((long)Math.ceil(route.path("duration").asDouble())+minutes*60L);if(leave.isBefore(first))first=leave;
            if(knownFirst==null||leave.isBefore(knownFirst))knownFirst=leave;
            if(leave.isBefore(earliest)){conflicts.add("origin_connection_outside_day_window");provenConflict=true;}
            verifiedRoutes.addObject().put("kind","origin_connection").put("city",originCity).put("seconds",route.path("duration").asDouble()).put("boarding_buffer_minutes",minutes);
          }catch(Exception ex){unknown.add("origin_connection_unverified");}
        }catch(Exception ex){unknown.add("fixed_transport_time_unknown");}
      }
      if(first!=null&&last!=null){long elapsed=Duration.between(first,last).toMinutes();report.put("whole_day_minutes",elapsed);if(elapsed>request.path("constraints").path("daily_minutes").asInt(600))conflicts.add("daily_duration_limit_exceeded");}
      if(knownFirst!=null&&knownLast!=null){long knownMinutes=Duration.between(knownFirst,knownLast).toMinutes();report.put("known_whole_day_minutes",knownMinutes);if(knownMinutes>request.path("constraints").path("daily_minutes").asInt(600))provenConflict=true;}
      String status=provenConflict?"infeasible":unknown.isEmpty()?conflicts.isEmpty()?"feasible":"infeasible":"unverified";
      report.put("status",status);
      if(!unknown.isEmpty()){quality.put("facts_complete",false).put("feasibility_status","unverified");if("complete".equals(quality.path("outcome").asText("")))quality.put("outcome","degraded");}
      if("infeasible".equals(status)){
        quality.put("outcome","draft").put("feasibility_status","infeasible");
        quality.withArray("issues").addObject().put("code","RECORDED_TIMELINE_CONFLICT").put("scope","day:"+index)
            .put("reason","已记录时间轴与已知路线、固定交通或整日时长冲突").put("action","补齐事实后对本日局部重验与求解").put("blocking",true).put("retryable",false);
      }
    }
  }
}
