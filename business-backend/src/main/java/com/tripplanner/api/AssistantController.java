package com.tripplanner.api;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.*;
import com.tripplanner.persistence.*;
import jakarta.servlet.http.HttpServletRequest;
import java.util.*;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/assistant/conversations")
public class AssistantController {
  private final AssistantMapper conversations; private final HistoryMapper history;
  private final TaskService tasks; private final AgentClient agent; private final JsonMapper json;
  private final TransactionTemplate tx;
  public AssistantController(AssistantMapper conversations,HistoryMapper history,TaskService tasks,AgentClient agent,JsonMapper json,TransactionTemplate tx){
    this.conversations=conversations;this.history=history;this.tasks=tasks;this.agent=agent;this.json=json;this.tx=tx;
  }

  @PostMapping public Object create(HttpServletRequest req,@RequestBody(required=false) ObjectNode body){
    long uid=UsersController.uid(req); String id=UUID.randomUUID().toString();
    String title=body==null?"旅行助手":body.path("title").asText("旅行助手").strip();
    if(title.isBlank()||title.length()>160) throw new ApiException(422,"会话标题无效");
    Long trip=body!=null&&body.path("active_trip_id").canConvertToLong()?body.path("active_trip_id").asLong():null;
    if(trip!=null&&history.owned(uid,trip)==null) throw new ApiException(404,"行程不存在");
    var row=new HashMap<String,Object>(); row.put("id",id); row.put("user_id",uid); row.put("title",title); row.put("active_trip_id",trip);
    conversations.conversation(row);
    return Map.of("success",true,"id",id);
  }

  @GetMapping public Object list(HttpServletRequest req){return Map.of("success",true,"data",conversations.list(UsersController.uid(req)));}
  @GetMapping("/{id}/messages") public Object messages(HttpServletRequest req,@PathVariable String id){
    long uid=UsersController.uid(req); owned(uid,id); return Map.of("success",true,"data",conversations.messages(uid,id));
  }

  @PostMapping("/{id}/messages")
  @ResponseStatus(org.springframework.http.HttpStatus.ACCEPTED)
  public Object message(HttpServletRequest req,@PathVariable String id,@RequestHeader(value="Idempotency-Key",required=false)String key,@RequestBody ObjectNode body){
    long uid=UsersController.uid(req); owned(uid,id);
    String content=body.path("content").asText("").strip(),mode=body.path("mode").asText("");
    if(content.isBlank()||content.length()>500||!Set.of("help","research","plan","revise").contains(mode)) throw new ApiException(422,"助手消息无效");
    save(id,uid,"user",content,"","");
    if(mode.equals("help")){
      String answer="可以在景点发现页搜索和收藏，再创建手工行程；智能规划失败不会影响已保存行程。";
      save(id,uid,"assistant",answer,"help",""); return Map.of("success",true,"status","completed","message",answer);
    }
    if(mode.equals("research")){
      String city=body.path("city").asText(""); var result=agent.post("/capabilities/research",Map.of("city",city,"query",content));
      String answer=json.writeValueAsString(result.path("data")); save(id,uid,"assistant",answer,"research","");
      return Map.of("success",true,"status","completed","data",result.path("data"));
    }
    ObjectNode request;
    ObjectNode revision=json.createObjectNode().put("assistant_preview",true).put("conversation_id",id);
    Long activeTrip=null;
    if(mode.equals("plan")) {
      if(!body.path("trip_request").isObject()) throw new ApiException(422,"缺少行程请求");
      request=(ObjectNode)body.path("trip_request").deepCopy();
    }
    else{
      long recordId=body.path("record_id").asLong(0); var row=history.owned(uid,recordId);
      if(row==null) throw new ApiException(404,"行程不存在"); activeTrip=recordId;
      if(body.path("version").asInt(-1)<0||body.path("day_index").asInt(-1)<0) throw new ApiException(422,"改排版本或日期无效");
      request=request(row); revision.put("record_id",recordId)
          .put("version",body.path("version").asInt(0)).put("day_index",body.path("day_index").asInt(-1)).put("instruction",content);
    }
    ObjectNode created=tasks.submit(uid,request,key==null?UUID.randomUUID().toString():key,req.getHeader("X-Request-ID"),revision);
    String taskId=created.path("data").path("id").asText(""); save(id,uid,"system_event","已创建智能规划任务","trip_task",taskId);
    conversations.touch(uid,id,activeTrip); return Map.of("success",true,"status","accepted","task_id",taskId);
  }

  @PostMapping("/{id}/proposals/{recordId}/confirm")
  public Object confirm(HttpServletRequest req,@PathVariable String id,@PathVariable long recordId,
      @RequestHeader("If-Match") int version){
    long uid=UsersController.uid(req); owned(uid,id); var proposal=history.owned(uid,recordId);
    if(proposal==null)throw new ApiException(404,"助手方案不存在");
    var quality=(ObjectNode)json.readTree(proposal.get("quality_json").toString());
    if(!quality.path("assistant_confirmation_required").asBoolean(false)
        ||!id.equals(quality.path("assistant_conversation_id").asText("")))
      throw new ApiException(409,"该记录不是此会话的待确认方案");
    if(((Number)proposal.get("version")).intValue()!=version)throw new ApiException(409,"助手方案版本冲突","VERSION_CONFLICT");
    var plan=(ObjectNode)json.readTree(proposal.get("plan_json").toString());
    String validated=quality.path("validated_outcome").asText("draft");
    quality.put("outcome",validated); quality.remove("validated_outcome");
    quality.remove("assistant_confirmation_required"); quality.remove("assistant_conversation_id");
    if(quality.path("issues") instanceof tools.jackson.databind.node.ArrayNode issues)
      for(int i=issues.size()-1;i>=0;i--)if("ASSISTANT_CONFIRMATION_REQUIRED".equals(issues.get(i).path("code").asText()))issues.remove(i);
    var parent=quality.remove("revision_parent");
    long target=parent!=null&&parent.path("record_id").asLong(0)>0?parent.path("record_id").asLong():recordId;
    int targetVersion=target==recordId?version:parent.path("version").asInt(0);
    tx.executeWithoutResult(s->{
      if(history.update(Map.of("id",target,"user_id",uid,"version",targetVersion,
          "plan_json",json.writeValueAsString(plan),"quality_json",json.writeValueAsString(quality)))!=1)
        throw new ApiException(409,"原行程已变更，请重新生成助手方案","VERSION_CONFLICT");
      history.outbox(target,uid,"draft".equals(validated)?"delete":"upsert");
    });
    return Map.of("success",true,"id",target,"version",targetVersion+1,"data",plan,"quality",quality,
        "message","已确认并保存助手方案");
  }

  private void owned(long uid,String id){if(conversations.owned(uid,id)==null)throw new ApiException(404,"助手会话不存在");}
  private void save(String id,long uid,String role,String content,String action,String ref){
    conversations.message(new HashMap<>(Map.of("conversation_id",id,"user_id",uid,"role",role,"content",content,"action_type",action,"action_ref",ref)));
  }
  private ObjectNode request(Map<String,Object> row){
    var body=json.createObjectNode(); for(String f:List.of("city","start_date","end_date","travel_days","transportation","accommodation","free_text_input"))body.set(f,json.valueToTree(row.get(f)));
    body.set("preferences",json.readTree(row.get("preferences").toString())); body.set("constraints",json.readTree(row.get("plan_json").toString()).path("constraints"));
    return TripRequests.normalize(body);
  }
}
