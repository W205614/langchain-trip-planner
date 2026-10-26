package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.tripplanner.api.ApiException;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class TripFactVerificationTest {
  final JsonMapper json=new JsonMapper();
  @Test void failureAndExplicitMissingNeverChangeTheSelectedPlace(){
    for(var failure:new ApiException[]{new ApiException(429,"limited","MAP_RATE_LIMITED"),new ApiException(503,"timeout","MAP_UNAVAILABLE"),new ApiException(404,"missing","POI_NOT_FOUND")}){
      var gateway=mock(AmapGateway.class);when(gateway.detail("p")).thenThrow(failure);
      var plan=(ObjectNode)json.readTree("{\"days\":[{\"day_index\":0,\"attractions\":[{\"poi_id\":\"p\",\"activity_id\":\"a\",\"name\":\"原景点\",\"location\":{\"longitude\":1,\"latitude\":2}}]}]}");
      var before=plan.deepCopy();var result=new TripFactVerificationService(gateway,json).verify(plan);
      assertEquals(before,plan);assertEquals("POI_NOT_FOUND".equals(failure.code)?"not_found":"lookup_failed",result.get(0).path("status").asText());
    }
  }
  @Test void successfulLookupProvidesObservedFactsWithoutSilentlyReplacingUserContent(){
    var gateway=mock(AmapGateway.class);when(gateway.detail("p")).thenReturn((ObjectNode)json.readTree("{\"name\":\"新名称\"}"));
    var plan=(ObjectNode)json.readTree("{\"days\":[{\"attractions\":[{\"poi_id\":\"p\",\"name\":\"原名称\"}]}]}");var before=plan.deepCopy();
    var result=new TripFactVerificationService(gateway,json).verify(plan);assertEquals(before,plan);assertEquals("verified",result.get(0).path("status").asText());assertEquals("新名称",result.get(0).path("observed").path("name").asText());
  }
}
