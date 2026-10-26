package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.TaskMapper;
import java.sql.Timestamp;
import java.time.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Durable checkpoints and irrevocable pre-send reservations. Transaction contains no network I/O. */
@Service
public class ExecutionGovernanceService {
  @org.springframework.beans.factory.annotation.Autowired private SingleInstance singleInstance;
  @org.springframework.beans.factory.annotation.Autowired private CampaignBudgetService campaign;
  @Value("${OUTBOUND_DAILY_TEXT_LIMIT:1000}") private int dailyText;
  @Value("${OUTBOUND_DAILY_EMBEDDING_LIMIT:5000}") private int dailyEmbedding;
  @Value("${OUTBOUND_DAILY_AMAP_LIMIT:10000}") private int dailyAmap;
  @Value("${OUTBOUND_TASK_TEXT_MAX:128}") private int taskTextMax;
  @Value("${OUTBOUND_TASK_EMBEDDING_MAX:1000}") private int taskEmbeddingMax;
  @Value("${OUTBOUND_TASK_AMAP_MAX:5000}") private int taskAmapMax;
  private final JdbcTemplate db;private final TransactionTemplate tx;private final TaskMapper tasks;private final JsonMapper json;private final String acceptanceRun;
  public ExecutionGovernanceService(JdbcTemplate db,TransactionTemplate tx,TaskMapper tasks,JsonMapper json,@Value("${FINAL_ACCEPTANCE_RUN_ID:}")String run){this.db=db;this.tx=tx;this.tasks=tasks;this.json=json;this.acceptanceRun=run;}
  private Map<String,Object> running(String execution,boolean lock){
    if(!singleInstance.owned())throw new ApiException(503,"调度器所有权丢失，执行写回已停止","SCHEDULER_OWNERSHIP_LOST");
    var rows=db.queryForList("SELECT * FROM trip_tasks WHERE execution_id=? AND status='running' AND deadline_at>timezone('UTC',now())"+(lock?" FOR UPDATE":""),execution);
    if(rows.isEmpty())throw new ApiException(409,"执行已结束或已过期","EXECUTION_EXPIRED");return rows.getFirst();
  }
  public Object save(String execution,ObjectNode body){return tx.execute(s->{var task=running(execution,true);
    String stage=body.path("stage").asText("");String hash=body.path("input_hash").asText("");
    if(!stage.matches("[a-zA-Z0-9_:-]{1,64}")||!hash.matches("[a-fA-F0-9]{64}")||!body.path("payload").isObject()||!body.path("compatibility").isObject()||json.writeValueAsString(body).length()>2_000_000)throw new ApiException(422,"执行快照无效");
    Instant expires;try{expires=Instant.parse(body.path("expires_at").asText());}catch(Exception ex){throw new ApiException(422,"快照有效期无效");}
    if(!expires.isAfter(Instant.now()))throw new ApiException(422,"快照已过期");
    db.update("INSERT INTO execution_checkpoints(task_id,execution_id,stage,input_hash,payload,compatibility,expires_at) VALUES(?,?,?,?,?::jsonb,?::jsonb,?) ON CONFLICT(task_id,stage) DO UPDATE SET execution_id=excluded.execution_id,input_hash=excluded.input_hash,payload=excluded.payload,compatibility=excluded.compatibility,expires_at=excluded.expires_at,created_at=now()",task.get("id"),execution,stage,hash,json.writeValueAsString(body.path("payload")),json.writeValueAsString(body.path("compatibility")),Timestamp.from(expires));
    return Map.of("success",true);});}
  public Object get(String execution){var task=running(execution,false);
    String query="""
WITH RECURSIVE ancestry AS (
 SELECT id,resumed_from,user_id,0 AS depth,ARRAY[id::text] AS seen FROM trip_tasks WHERE id=?
 UNION ALL
 SELECT p.id,p.resumed_from,p.user_id,a.depth+1,a.seen||p.id::text
 FROM trip_tasks p JOIN ancestry a ON p.id=a.resumed_from AND p.user_id=a.user_id
 WHERE a.depth<63 AND NOT p.id=ANY(a.seen)
)
SELECT DISTINCT ON(c.stage,c.input_hash,c.compatibility) c.*
FROM ancestry a JOIN execution_checkpoints c ON c.task_id=a.id
WHERE c.expires_at>now()
ORDER BY c.stage,c.input_hash,c.compatibility,a.depth,c.created_at DESC LIMIT 256
""";
    var items=new ArrayList<ObjectNode>();for(var row:db.queryForList(query,task.get("id"))){var n=json.createObjectNode();for(String f:List.of("stage","input_hash"))n.put(f,row.get(f).toString());n.set("payload",json.readTree(row.get("payload").toString()));n.set("compatibility",json.readTree(row.get("compatibility").toString()));n.put("expires_at",((Timestamp)row.get("expires_at")).toInstant().toString());items.add(n);}return Map.of("checkpoints",items);}
  public Object reserve(ObjectNode body){
    String kind=body.path("kind").asText("");if(!Set.of("text","embedding","amap").contains(kind))throw new ApiException(422,"调用类型无效");
    String key=body.path("request_key").asText("");if(!key.matches("[A-Za-z0-9_:/.-]{1,160}"))throw new ApiException(422,"调用编号无效");
    String run=campaign.runId();
    String requestedRun=body.path("run_id").asText("");
    if(!requestedRun.isBlank()&&!requestedRun.equals(run))throw new ApiException(422,"验收批次只能由业务服务配置，不能由调用方更换","CAMPAIGN_ID_CLIENT_CONTROLLED");
    if(run.length()>120||!run.matches("[A-Za-z0-9_.:-]*"))throw new ApiException(422,"验收批次无效");
    String execution=body.path("execution_id").asText("");
    if(campaign.enabled()&&kind.equals("amap")&&!"rest".equals(body.path("map_protocol").asText("rest")))throw new ApiException(409,"固定验收批次仅允许可逐次计数的REST地图调用；MCP工具内部请求数不可证明","CAMPAIGN_REST_REQUIRED");
    var tokenInput=body.path("reserved_tokens");
    if(!tokenInput.isMissingNode()&&(!tokenInput.isIntegralNumber()||!tokenInput.canConvertToLong()||tokenInput.asLong(-1)<0))throw new ApiException(422,"Token预留必须为非负整数");
    long reservedTokens=tokenInput.asLong(0);
    return tx.execute(s->{
      Map<String,Object> task=execution.isBlank()?null:running(execution,true);
      if(!db.queryForList("SELECT id FROM outbound_reservations WHERE run_id=? AND request_key=?",run,key).isEmpty())throw new ApiException(409,"调用编号已使用","OUTBOUND_RESERVATION_REUSED");
      if(task!=null&&campaign.enabled()){var root=budgetRootRow(task);campaign.workflow(root.get("id").toString(),root.get("fingerprint").toString());}
      campaign.reserve(kind,reservedTokens);
      // Serialize all calls of a run, including calls with no task (research, ingestion, map UI).
      String runScope=run.isBlank()?"daily:"+LocalDate.now(ZoneOffset.UTC):"acceptance:"+run;
      increment(runScope,kind,run.isBlank()?switch(kind){case "text"->dailyText;case "embedding"->dailyEmbedding;default->dailyAmap;}:switch(kind){case "text"->12;case "embedding"->40;default->400;});
      if(task!=null){int days=Math.max(1,json.readTree(task.get("request_json").toString()).path("travel_days").asInt(1));increment("task:"+budgetRoot(task),kind,switch(kind){case "text"->Math.min(taskTextMax,4+2*days);case "embedding"->Math.min(taskEmbeddingMax,40+10*days);default->Math.min(taskAmapMax,100+80*days);});}
      // Reusing a reservation cannot authorize another send. Every attempt gets a fresh key.
      if(db.update("INSERT INTO outbound_reservations(execution_id,kind,run_id,request_key,reserved_tokens) VALUES(?,?,?,?,?) ON CONFLICT(run_id,request_key) DO NOTHING",execution.isBlank()?null:execution,kind,run,key,reservedTokens)!=1)throw new ApiException(409,"调用编号已使用","OUTBOUND_RESERVATION_REUSED");
      return Map.of("success",true,"reserved",true,"outcome","unknown","run_id",run,"reserved_tokens",reservedTokens);
    });
  }
  private Map<String,Object> budgetRootRow(Map<String,Object> task){var current=task;var seen=new HashSet<String>();while(current.get("resumed_from")!=null&&seen.add(current.get("id").toString())){var parent=tasks.get(current.get("resumed_from").toString());if(parent==null)break;current=parent;}return current;}
  private String budgetRoot(Map<String,Object> task){return budgetRootRow(task).get("id").toString();}
  public Object campaignStatus(){return campaign.status();}
  public Object reserveWorkflow(ObjectNode body){if(!campaign.enabled())throw new ApiException(409,"服务端未启用固定验收批次","CAMPAIGN_NOT_CONFIGURED");return tx.execute(s->{var task=running(body.path("execution_id").asText(""),true);var root=budgetRootRow(task);campaign.workflow(root.get("id").toString(),root.get("fingerprint").toString());return Map.of("success",true,"run_id",campaign.runId(),"root_task_id",root.get("id"));});}
  private void increment(String scope,String kind,long limit){db.update("INSERT INTO outbound_budget_counters(scope,kind,used) VALUES(?,?,0) ON CONFLICT DO NOTHING",scope,kind);if(db.update("UPDATE outbound_budget_counters SET used=used+1 WHERE scope=? AND kind=? AND used<?",scope,kind,limit)!=1)throw new ApiException(429,"外部调用预算已用完","OUTBOUND_BUDGET_EXHAUSTED");}
  public void reserveAmap(){var n=json.createObjectNode().put("kind","amap").put("map_protocol","rest").put("request_key",UUID.randomUUID().toString());reserve(n);}
  public void linkResume(String child,String parent){if(db.update("UPDATE trip_tasks SET resumed_from=? WHERE id=? AND (resumed_from IS NULL OR resumed_from=?)",parent,child,parent)!=1)throw new ApiException(409,"恢复幂等键已用于其他任务");}
}
