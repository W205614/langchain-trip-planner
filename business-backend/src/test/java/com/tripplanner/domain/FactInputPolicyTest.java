package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import com.tripplanner.api.ApiException;
import java.time.*;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

class FactInputPolicyTest {
  final JsonMapper json=new JsonMapper();
  ObjectNode input(){
    var input=json.createObjectNode();var fact=input.putArray("transport_buffers").addObject();
    fact.put("commitment_id","inline-id").put("minutes",45).put("source","用户提供的承运方通知")
        .put("confirmed",true).put("observed_at",Instant.now().minusSeconds(10).toString())
        .put("expires_at",Instant.now().plusSeconds(3600).toString()).put("authority","official");return input;
  }
  @Test void userFactsNeverBecomeOfficialAndDuplicateDeclarationsFail(){
    var input=input();FactInputPolicy.validate(input,1);
    assertEquals("user_declared",input.path("transport_buffers").get(0).path("authority").asText());
    input.withArray("transport_buffers").add(input.path("transport_buffers").get(0).deepCopy());
    assertEquals(422,assertThrows(ApiException.class,()->FactInputPolicy.validate(input,1)).status);
  }
  @Test void missingFreshnessAndInvalidLeadTimeAreRejected(){
    var input=input();((ObjectNode)input.path("transport_buffers").get(0)).remove("expires_at");assertThrows(ApiException.class,()->FactInputPolicy.validate(input,1));
    var bad=input();((ObjectNode)bad.path("transport_buffers").get(0)).put("minutes",361);assertThrows(ApiException.class,()->FactInputPolicy.validate(bad,1));
  }
}
