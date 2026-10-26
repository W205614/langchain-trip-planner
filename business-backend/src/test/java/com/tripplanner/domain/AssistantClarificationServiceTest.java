package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.AssistantMapper;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

class AssistantClarificationServiceTest {
  final AssistantMapper mapper=mock(AssistantMapper.class);
  final JsonMapper json=JsonMapper.builder().build();
  final AtomicReference<Map<String,Object>> persisted=new AtomicReference<>();
  final Map<String,Object> trip=Map.of("id",12L,"version",1,"planning_revision",1);
  AssistantClarificationService service;
  JsonNode intent(boolean missing){return json.readTree(missing
      ?"{\"operation\":\"propose_change\",\"target_day\":null,\"missing_slots\":[\"target_day\"],\"constraints_patch\":{},\"reply\":\"第几天？\"}"
      :"{\"operation\":\"propose_change\",\"target_day\":1,\"missing_slots\":[],\"constraints_patch\":{\"pace\":\"relaxed\"}}");}
  @BeforeEach void setup(){
    service=new AssistantClarificationService(mapper,json,900);
    when(mapper.clarification(anyLong(),anyString())).thenAnswer(inv->{var row=persisted.get();return row!=null&&row.get("user_id").equals(inv.getArgument(0))?new HashMap<>(row):null;});
    when(mapper.saveClarification(anyMap())).thenAnswer(inv->{var next=new HashMap<String,Object>(inv.getArgument(0));var previous=persisted.get();
      int revision=previous==null?0:((Number)previous.get("revision")).intValue();
      if(revision!=((Number)next.get("expected_revision")).intValue())return 0;
      next.put("revision",revision+1);persisted.set(next);return 1;});
    when(mapper.clarificationStatus(anyLong(),anyString(),anyInt(),anyString())).thenAnswer(inv->{var row=persisted.get();
      if(row==null||((Number)row.get("revision")).intValue()!=inv.getArgument(2,Integer.class))return 0;
      row.put("status",inv.getArgument(3));row.put("revision",((Number)row.get("revision")).intValue()+1);return 1;});
  }
  @Test void processRestartKeepsOriginalRequestAndCollectsOnlyExplicitDay(){
    var first=service.resolve(1,"conversation",trip,"当天轻松一些","first",text->intent(true));
    assertEquals("needs_clarification",first.status());
    var restarted=new AssistantClarificationService(mapper,json,900);
    var second=restarted.resolve(1,"conversation",trip,"第2天","second",text->{assertEquals("第2天；原调整需求：当天轻松一些",text);return intent(false);});
    assertEquals("ready",second.status());assertTrue(second.content().contains("轻松"));
    assertEquals("当天轻松一些",persisted.get().get("original_request"));
    assertEquals(1,json.readTree(persisted.get().get("collected_slots").toString()).path("target_day").asInt());
    assertEquals("ready",persisted.get().get("status"));verify(mapper,never()).clarificationAccepted(anyLong(),anyString(),anyInt(),anyString(),anyString());
  }
  @Test void cancellationDoesNotCallClassifierOrSubmitTask(){
    service.resolve(1,"conversation",trip,"轻松一点","first",text->intent(true));
    var cancelled=service.resolve(1,"conversation",trip,"取消","second",text->{fail("cancel must not call classifier");return null;});
    assertEquals("cancelled",cancelled.status());assertEquals("cancelled",persisted.get().get("status"));
  }
  @Test void changedMindStartsReadOnlyQuestionInsteadOfApplyingPendingOperation(){
    service.resolve(1,"conversation",trip,"轻松一点","first",text->intent(true));
    var changed=service.resolve(1,"conversation",trip,"我改主意了，故宫如何预约？","second",text->{assertFalse(text.contains("轻松"));return json.readTree("{\"operation\":\"read_only\"}");});
    assertEquals("research",changed.status());assertEquals("cancelled",persisted.get().get("status"));
  }
  @Test void expiredOrChangedTripCannotConsumeOldSlots(){
    service.resolve(1,"conversation",trip,"轻松一点","first",text->intent(true));
    persisted.get().put("expires_at",Timestamp.from(Instant.now().minusSeconds(1)));
    var expired=service.resolve(1,"conversation",trip,"第2天","second",text->{fail("expired context must not classify");return null;});
    assertEquals("needs_clarification",expired.status());assertTrue(expired.reply().contains("过期"));
    service.resolve(1,"conversation",trip,"轻松一点","third",text->intent(true));
    var changed=service.resolve(1,"conversation",Map.of("id",12L,"version",2,"planning_revision",1),"第2天","fourth",text->{fail("stale context must not classify");return null;});
    assertTrue(changed.reply().contains("已变化"));
  }
  @Test void wrongTripIsRejectedAndAnotherUserCannotReadSlots(){
    service.resolve(1,"conversation",trip,"轻松一点","first",text->intent(true));
    assertEquals("none",service.snapshot(2,"conversation").get("status"));
    assertThrows(ApiException.class,()->service.resolve(1,"conversation",Map.of("id",99L,"version",1,"planning_revision",1),"第2天","second",text->intent(false)));
  }
}
