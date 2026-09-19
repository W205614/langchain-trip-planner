package com.tripplanner.api;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.*;
import com.tripplanner.persistence.*;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.transaction.support.TransactionTemplate;
import org.slf4j.MDC;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/assistant/conversations")
public class AssistantController {
  private final AssistantMapper conversations; private final HistoryMapper history;
  private final TaskService tasks; private final AgentClient agent; private final JsonMapper json;
  private final TransactionTemplate tx;
  private final TripLedgerService ledger;
  public AssistantController(AssistantMapper conversations,HistoryMapper history,TaskService tasks,AgentClient agent,JsonMapper json,TransactionTemplate tx,TripLedgerService ledger){
    this.conversations=conversations;this.history=history;this.tasks=tasks;this.agent=agent;this.json=json;this.tx=tx;this.ledger=ledger;
  }

  @PostMapping public Object create(HttpServletRequest req,@RequestBody(required=false) ObjectNode body){
    long uid=UsersController.uid(req); String id=UUID.randomUUID().toString();
    String title=body==null?"旅行助手":body.path("title").asText("旅行助手").strip();
    if(title.isBlank()||title.length()>160) throw new ApiException(422,"会话标题无效");
    Long trip=body!=null&&body.path("active_trip_id").canConvertToLong()?body.path("active_trip_id").asLong():null;
    if(trip!=null&&history.owned(uid,trip)==null) throw new ApiException(404,"行程不存在");
    if(trip!=null){
      var existing=conversations.latestForTrip(uid,trip);
      if(existing!=null){
        String existingId=existing.get("id").toString(); conversations.touch(uid,existingId,trip);
        return Map.of("success",true,"id",existingId,"reused",true);
      }
    }
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
    long uid=UsersController.uid(req); var conversation=owned(uid,id);
    String content=body.path("content").asText("").strip(),mode=body.path("mode").asText("");
    if(content.isBlank()||content.length()>500||!Set.of("research","revise","auto").contains(mode)) throw new ApiException(422,"Agent 操作无效");
    save(id,uid,"user",content,"","");
    if(mode.equals("auto")){
      Long activeTrip=conversation.get("active_trip_id") instanceof Number n?n.longValue():null;
      if(activeTrip==null)throw new ApiException(422,"请从我的行程中发起对话");
      var active=history.owned(uid,activeTrip); if(active==null)throw new ApiException(404,"行程不存在");
      if(body.path("city").asText("").isBlank()) body=((ObjectNode)body.deepCopy()).put("city",active.get("city").toString());
      var classified=agent.post("/capabilities/assistant-intent",Map.of(
          "content",content,"travel_days",((Number)active.get("travel_days")).intValue())).path("data");
      if(classified.path("missing_slots").size()>0){
        String reply=classified.path("reply").asText("请补充要调整的日期");
        save(id,uid,"assistant",reply,"clarification","");
        return Map.of("success",true,"status","needs_clarification","message",reply,"intent",classified);
      }
      if("read_only".equals(classified.path("operation").asText())) mode="research";
      else {
        mode="revise";
        body=body.deepCopy().put("record_id",activeTrip)
            .put("version",((Number)active.get("version")).intValue())
            .put("day_index",classified.path("target_day").asInt(-1));
        body.set("intent",classified.deepCopy());
      }
    }
    if(mode.equals("research")){
      String city=body.path("city").asText("");
      Long activeTrip=conversation.get("active_trip_id") instanceof Number n?n.longValue():null;
      if(activeTrip==null)throw new ApiException(422,"请从我的行程中发起攻略问答");
      String context="";
      var trip=history.owned(uid,activeTrip);
      if(trip==null)throw new ApiException(404,"行程不存在");
      context=tripContext(trip);
      var result=agent.post("/capabilities/research",Map.of("city",city,"query",content,"trip_context",context));
      var data=result.path("data");
      String answer=data.path("answer").asText("").strip();
      if(answer.isBlank()) answer="当前没有取得可展示的攻略回答，请稍后重试。";
      save(id,uid,"assistant",answer,"research","");
      return Map.of("success",true,"status","completed","message",answer,"data",data);
    }
    ObjectNode request;
    ObjectNode revision=json.createObjectNode();
    Long activeTrip=null;
    long recordId=body.path("record_id").asLong(0); var row=history.owned(uid,recordId);
    if(row==null) throw new ApiException(404,"行程不存在"); activeTrip=recordId;
    if(body.path("version").asInt(-1)<0||body.path("day_index").asInt(-1)<0) throw new ApiException(422,"改排版本或日期无效");
    request=request(row); revision.put("record_id",recordId)
        .put("version",body.path("version").asInt(0)).put("day_index",body.path("day_index").asInt(-1)).put("instruction",content);
    if(body.path("intent").isObject()) revision.set("intent",body.path("intent").deepCopy());
    ObjectNode created=tasks.submit(uid,request,key==null?UUID.randomUUID().toString():key,req.getHeader("X-Request-ID"),revision);
    String taskId=created.path("data").path("id").asText(""); save(id,uid,"system_event","已创建智能规划任务","trip_task",taskId);
    conversations.touch(uid,id,activeTrip); return Map.of("success",true,"status","accepted","task_id",taskId);
  }

  @PostMapping(path="/{id}/messages/stream",produces="text/event-stream")
  public SseEmitter researchStream(HttpServletRequest req,@PathVariable String id,@RequestBody ObjectNode body){
    long uid=UsersController.uid(req); var conversation=owned(uid,id);
    String content=body.path("content").asText("").strip(),mode=body.path("mode").asText("");
    if(content.isBlank()||content.length()>500||!"research".equals(mode))
      throw new ApiException(422,"流式接口仅支持攻略问答");
    Long activeTrip=conversation.get("active_trip_id") instanceof Number n?n.longValue():null;
    if(activeTrip==null)throw new ApiException(422,"请从我的行程中发起攻略问答");
    var trip=history.owned(uid,activeTrip);
    if(trip==null)throw new ApiException(404,"行程不存在");
    String city=body.path("city").asText(""),context=tripContext(trip);
    save(id,uid,"user",content,"","");
    conversations.touch(uid,id,activeTrip);

    var emitter=new SseEmitter(30000L);
    var worker=new AtomicReference<Thread>();
    Runnable cancel=()->{var running=worker.get();if(running!=null)running.interrupt();};
    emitter.onCompletion(cancel); emitter.onTimeout(cancel); emitter.onError(ignored->cancel.run());
    String requestId=BusinessAuditService.safeRequestId(req.getHeader("X-Request-ID"));
    worker.set(Thread.startVirtualThread(()->{
      try(var ignored=MDC.putCloseable("request_id",requestId)){
        agent.stream("/capabilities/research/stream",Map.of("city",city,"query",content,"trip_context",context),
            Duration.ofSeconds(25),(event,data)->{
              try{
                if("result".equals(event)){
                  String answer=data.path("answer").asText("").strip();
                  if(!answer.isBlank())save(id,uid,"assistant",answer,"research","");
                }
                emitter.send(SseEmitter.event().name(event).data(data));
              }catch(IOException ex){throw new RuntimeException(ex);}
            });
        emitter.complete();
      }catch(Exception ex){
        try{emitter.send(SseEmitter.event().name("error").data(Map.of(
            "code","AGENT_UNAVAILABLE","message","攻略问答暂不可用，请稍后重试")));}catch(Exception ignored){}
        emitter.complete();
        if(ex instanceof InterruptedException)Thread.currentThread().interrupt();
      }
    }));
    return emitter;
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
      ledger.capture(uid,target,"assistant_confirm",req.getHeader("X-Request-ID"));
    });
    return Map.of("success",true,"id",target,"version",targetVersion+1,"data",plan,"quality",quality,
        "message","已确认并保存助手方案");
  }

  private Map<String,Object> owned(long uid,String id){
    var row=conversations.owned(uid,id);
    if(row==null)throw new ApiException(404,"助手会话不存在");
    return row;
  }
  private void save(String id,long uid,String role,String content,String action,String ref){
    conversations.message(new HashMap<>(Map.of("conversation_id",id,"user_id",uid,"role",role,"content",content,"action_type",action,"action_ref",ref)));
  }
  private ObjectNode request(Map<String,Object> row){
    var body=json.createObjectNode(); for(String f:List.of("departure_city","city","start_date","end_date","travel_days","transportation","accommodation","traveler_count","room_count","budget_total","free_text_input"))body.set(f,json.valueToTree(row.get(f)));
    body.set("preferences",json.readTree(row.get("preferences").toString())); body.set("constraints",json.readTree(row.get("plan_json").toString()).path("constraints"));
    return TripRequests.normalize(body);
  }

  private String tripContext(Map<String,Object> row){
    var summary=new StringBuilder()
        .append("目的地：").append(row.get("city"))
        .append("；日期：").append(row.get("start_date")).append(" 至 ").append(row.get("end_date"));
    try{
      var plan=json.readTree(row.get("plan_json").toString());
      for(var day:plan.path("days")){
        summary.append("\n第").append(day.path("day_index").asInt()+1).append("天：");
        var names=new ArrayList<String>();
        day.path("attractions").forEach(item->names.add(item.path("name").asText("")));
        summary.append(String.join("、",names));
      }
    }catch(Exception ignored){}
    return summary.length()>8000?summary.substring(0,8000):summary.toString();
  }
}
