package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.AssistantMapper;
import com.tripplanner.persistence.TaskMapper;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import java.util.function.*;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Durable slot continuation; no LLM memory and no plan mutation authority. */
@Service
public class AssistantClarificationService {
  private final AssistantMapper mapper;
  private final JsonMapper json;
  private final int ttl;
  @org.springframework.beans.factory.annotation.Autowired private TaskMapper tasks;
  private static final Pattern DAY=Pattern.compile("第\\s*([0-9]{1,2}|[一二三四五六七八九十]{1,3})\\s*天");
  private static final Set<String> CANCEL=Set.of("取消","算了","不改了","取消修改","不用调整了","取消调整");
  public record Resolution(String content,JsonNode intent,String status,String reply,int revision,String taskId) {}

  public AssistantClarificationService(AssistantMapper mapper,JsonMapper json,
      @Value("${ASSISTANT_CLARIFICATION_TTL_SECONDS:900}")int ttl){this.mapper=mapper;this.json=json;this.ttl=Math.max(60,Math.min(3600,ttl));}

  public Map<String,Object> snapshot(long uid,String id) {
    var row=mapper.clarification(uid,id);
    if(row==null)return Map.of("status","none");
    String status=row.get("status").toString();
    if(!expires(row).isAfter(Instant.now())&&Set.of("pending","ready").contains(status))status="expired";
    return Map.of("status",status,"operation",row.get("operation"),"original_request",row.get("original_request"),
        "missing_slots",json.readTree(row.get("missing_slots").toString()),"collected_slots",json.readTree(row.get("collected_slots").toString()),
        "expires_at",expires(row).toString(),"trip_id",row.get("trip_id"),"confirmation_required",true);
  }

  public Resolution resolve(long uid,String id,Map<String,Object> trip,String content,String key,Function<String,JsonNode> classifier) {
    var previous=mapper.clarification(uid,id);
    int expected=previous==null?0:((Number)previous.get("revision")).intValue();
    boolean sameTrip=previous!=null&&((Number)previous.get("trip_id")).longValue()==((Number)trip.get("id")).longValue();
    if(previous!=null&&!sameTrip)throw new ApiException(409,"澄清状态与行程不匹配","CLARIFICATION_SCOPE_CONFLICT");
    boolean replay=previous!=null&&key!=null&&key.equals(previous.get("accepted_key"));
    if(replay&&!content.equals(previous.get("last_input")))throw new ApiException(409,"同一幂等键不能用于不同消息","CLARIFICATION_KEY_CONFLICT");
    if(previous!=null&&"consumed".equals(previous.get("status"))&&replay)
      return new Resolution(content,json.createObjectNode(),"accepted","已创建待确认方案任务",expected,Objects.toString(previous.get("accepted_task"),""));
    boolean pending=previous!=null&&Set.of("pending","ready").contains(previous.get("status"));
    if(previous!=null&&"submitting".equals(previous.get("status"))) {
      if(!replay)throw new ApiException(409,"上次补充正在提交，请用相同请求重试或查看任务","CLARIFICATION_IN_PROGRESS");
      var accepted=tasks.byKey(uid,key);
      if(accepted!=null){
        mapper.clarificationAccepted(uid,id,expected,key,accepted.get("id").toString());
        return new Resolution(content,json.createObjectNode(),"accepted","已恢复已提交任务",expected,accepted.get("id").toString());
      }
      if(((Timestamp)previous.get("updated_at")).toInstant().plusSeconds(10).isAfter(Instant.now()))
        throw new ApiException(409,"上次补充正在提交，请稍后用同一请求重试","CLARIFICATION_IN_PROGRESS");
      change(uid,id,expected,"ready");
      // A process may stop between claiming a clarification and inserting its
      // task. Reuse the saved instruction and key; TaskService deduplicates it.
      return new Resolution(previous.get("instruction").toString(),json.readTree(previous.get("intent_json").toString()),"ready","正在恢复提交",expected+1,"");
    }
    String compact=content.replaceAll("[\\s。！!，,]","");
    if(CANCEL.contains(compact)) {
      if(pending)change(uid,id,expected,"cancelled");
      return new Resolution(content,json.createObjectNode(),"cancelled","已取消这次调整，原行程未改变。",0,"");
    }
    boolean expired=pending&&!expires(previous).isAfter(Instant.now());
    boolean changed=pending&&(((Number)previous.get("base_version")).intValue()!=((Number)trip.get("version")).intValue()
        ||((Number)previous.get("planning_revision")).intValue()!=((Number)trip.get("planning_revision")).intValue());
    if(expired||changed) {
      change(uid,id,expected,"cancelled");
      return new Resolution(content,json.createObjectNode(),"needs_clarification",expired?"上次调整澄清已过期，请重新描述完整需求。":"行程或规划输入已变化，请重新描述完整需求。",0,"");
    }
    String original=content;
    String classifiedContent=content;
    boolean dayReply=DAY.matcher(content).find();
    boolean replaces=content.matches("^(我改主意了?|换个需求|改为问|改成问).*");
    if(pending&&dayReply&&!replaces){
      original=previous.get("original_request").toString();
      // Put the explicit slot first so a corrected day overrides earlier text.
      classifiedContent=content+"；原调整需求："+original;
    }
    if(classifiedContent.length()>1000)throw new ApiException(422,"补充后的调整需求过长，请重新描述");
    JsonNode intent=classifier.apply(classifiedContent);
    boolean proposing="propose_change".equals(intent.path("operation").asText());
    if(!proposing){
      if(pending)change(uid,id,expected,"cancelled");
      return new Resolution(content,intent,"research","",0,"");
    }
    boolean missing=intent.path("missing_slots").size()>0;
    var row=new HashMap<String,Object>();
    row.put("conversation_id",id);row.put("user_id",uid);row.put("trip_id",trip.get("id"));row.put("original_request",original);
    row.put("last_input",content);row.put("instruction",classifiedContent);
    row.put("operation","propose_change");row.put("intent_json",json.writeValueAsString(intent));
    row.put("missing_slots",json.writeValueAsString(intent.path("missing_slots")));
    var collected=json.createObjectNode();if(!intent.path("target_day").isNull()&&intent.path("target_day").canConvertToInt())collected.set("target_day",intent.path("target_day"));
    collected.set("constraints_patch",intent.path("constraints_patch"));
    row.put("collected_slots",json.writeValueAsString(collected));row.put("base_version",trip.get("version"));
    row.put("planning_revision",trip.get("planning_revision"));row.put("status",missing?"pending":"ready");
    row.put("expires_at",Timestamp.from(Instant.now().plusSeconds(ttl)));row.put("expected_revision",expected);
    if(mapper.saveClarification(row)!=1)throw new ApiException(409,"澄清状态已变化，请刷新后继续","CLARIFICATION_CONFLICT");
    return new Resolution(classifiedContent,intent,missing?"needs_clarification":"ready",intent.path("reply").asText("请补充要调整的日期"),expected+1,"");
  }

  public ObjectNode submit(long uid,String id,int revision,String key,Supplier<ObjectNode> action) {
    if(mapper.claimClarification(uid,id,revision,key)!=1)
      throw new ApiException(409,"澄清状态已变化，请刷新后继续","CLARIFICATION_CONFLICT");
    try {
      ObjectNode result=action.get();
      if(mapper.clarificationAccepted(uid,id,revision+1,key,result.path("data").path("id").asText())!=1)
        throw new ApiException(409,"任务已提交，请在任务列表查看","CLARIFICATION_CONFLICT");
      return result;
    } catch(RuntimeException ex) {
      mapper.clarificationStatus(uid,id,revision+1,"ready");
      throw ex;
    }
  }

  private void change(long uid,String id,int revision,String status) {
    if(mapper.clarificationStatus(uid,id,revision,status)!=1)
      throw new ApiException(409,"澄清状态已变化，请刷新后继续","CLARIFICATION_CONFLICT");
  }
  private static Instant expires(Map<String,Object> row){return ((Timestamp)row.get("expires_at")).toInstant();}
}
