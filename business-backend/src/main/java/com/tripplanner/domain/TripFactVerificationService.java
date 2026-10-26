package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.time.Instant;
import org.springframework.stereotype.Service;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.*;

/** Refresh facts without changing the itinerary's selected places or protected content. */
@Service
public class TripFactVerificationService {
  private final AmapGateway amap;private final JsonMapper json;
  public TripFactVerificationService(AmapGateway amap,JsonMapper json){this.amap=amap;this.json=json;}
  public ArrayNode verify(ObjectNode plan) {
    var findings=json.createArrayNode();
    for(var day:plan.path("days"))for(var raw:day.path("attractions"))if(raw instanceof ObjectNode attraction){
      String id=attraction.path("poi_id").asText("");
      var finding=findings.addObject().put("poi_id",id).put("activity_id",attraction.path("activity_id").asText(""))
          .put("day_index",day.path("day_index").asInt(0)).put("checked_at",Instant.now().toString());
      try {
        var current=amap.detail(id);
        // Keep user-owned name, position and time unchanged until an explicit edit/confirmation.
        finding.put("status","verified").put("message","地图资料已取得；出行日营业与预约仍需按来源确认")
            .set("observed",current.deepCopy());
      }catch(ApiException ex){
        boolean missing="POI_NOT_FOUND".equals(ex.code);
        finding.put("status",missing?"not_found":"lookup_failed").put("code",ex.code)
            .put("retryable",!missing).put("replacement_confirmation_required",missing)
            .put("message",missing?"地图已明确返回该地点不存在；原景点保留，请选择替代地点后确认保存":"本次地图核验失败；原景点和安排保留，可稍后重试");
      }catch(Exception ex){finding.put("status","lookup_failed").put("code","MAP_UNAVAILABLE").put("retryable",true)
          .put("message","本次地图连接失败；原景点和安排保留，可稍后重试");}
    }
    return findings;
  }
}
