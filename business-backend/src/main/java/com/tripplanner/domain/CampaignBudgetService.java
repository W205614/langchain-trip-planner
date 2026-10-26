package com.tripplanner.domain;

import com.tripplanner.api.ApiException;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/** One server-pinned, persistent campaign. Calls and token reservations never decrease. */
@Service
public class CampaignBudgetService {
  private final JdbcTemplate db;private final String run,phase;private final int textMax,embeddingMax,amapMax;private final long tokenMax;
  private final List<Map<String,String>> samples;
  @org.springframework.beans.factory.annotation.Autowired
  public CampaignBudgetService(JdbcTemplate db,@Value("${FINAL_RUN_ID:${FINAL_ACCEPTANCE_RUN_ID:}}")String run,
      @Value("${FINAL_RUN_PHASE:development}")String phase,@Value("${FINAL_CAMPAIGN_TEXT_LIMIT:12}")int text,
      @Value("${FINAL_CAMPAIGN_EMBEDDING_LIMIT:40}")int embedding,@Value("${FINAL_CAMPAIGN_AMAP_LIMIT:120}")int amap,
      @Value("${FINAL_CAMPAIGN_TOKEN_LIMIT:800000}")long tokens,
      @Value("${FINAL_CAMPAIGN_SAMPLES_JSON:[]}")String registry){
    this.db=db;this.run=run.strip();this.phase=phase;this.textMax=clamp(text,12);this.embeddingMax=clamp(embedding,40);this.amapMax=clamp(amap,120);this.tokenMax=Math.max(1,Math.min(tokens,800000));
    if(!this.run.matches("[A-Za-z0-9_.:-]{0,120}")||!Set.of("development","acceptance").contains(phase))throw new IllegalArgumentException("Invalid server campaign configuration");
    var metadata=new ArrayList<Map<String,String>>();
    try{
      var parsed=new tools.jackson.databind.json.JsonMapper().readTree(registry);
      if(!parsed.isArray()||parsed.size()>4)throw new IllegalArgumentException();
      var names=new HashSet<String>();var fingerprints=new HashSet<String>();var counts=new HashMap<String,Integer>();
      for(var sample:parsed){String id=sample.path("sample_id").asText(""),fingerprint=sample.path("fingerprint").asText(""),group=sample.path("phase").asText("");
        if(!id.matches("[A-Za-z0-9_.:-]{1,64}")||!fingerprint.matches("[A-Za-z0-9_.:-]{1,128}")||!Set.of("development","acceptance").contains(group)||!names.add(id)||!fingerprints.add(fingerprint)||counts.merge(group,1,Integer::sum)>2)throw new IllegalArgumentException();
        metadata.add(Map.of("sample_id",id,"fingerprint",fingerprint,"phase",group));
      }
    }catch(Exception ex){throw new IllegalArgumentException("Invalid server-pinned campaign sample registry",ex);}
    this.samples=List.copyOf(metadata);
  }
  public CampaignBudgetService(JdbcTemplate db,String run,String phase,int text,int embedding,int amap,long tokens){this(db,run,phase,text,embedding,amap,tokens,"[]");}
  private static int clamp(int value,int max){return Math.max(1,Math.min(value,max));}
  public String runId(){return run;}
  public boolean enabled(){return !run.isBlank();}
  private Map<String,Object> locked(){
    if(!enabled())throw new ApiException(409,"服务端未启用固定验收批次","CAMPAIGN_NOT_CONFIGURED");
    // Import existing count evidence for this exact ID; do not reset old ledgers.
    db.update("""
INSERT INTO outbound_campaigns(id,text_limit,embedding_limit,amap_limit,token_limit,text_used,embedding_used,amap_used,reserved_tokens)
VALUES(?,?,?,?,?,
 COALESCE((SELECT used FROM outbound_budget_counters WHERE scope=? AND kind='text'),0),
 COALESCE((SELECT used FROM outbound_budget_counters WHERE scope=? AND kind='embedding'),0),
 COALESCE((SELECT used FROM outbound_budget_counters WHERE scope=? AND kind='amap'),0),
 CASE WHEN EXISTS(SELECT 1 FROM outbound_reservations WHERE run_id=? AND kind IN ('text','embedding')) THEN ? ELSE 0 END)
ON CONFLICT(id) DO NOTHING
""",run,textMax,embeddingMax,amapMax,tokenMax,"acceptance:"+run,"acceptance:"+run,"acceptance:"+run,run,tokenMax);
    db.update("INSERT INTO outbound_campaign_binding(singleton,campaign_id) VALUES(1,?) ON CONFLICT(singleton) DO NOTHING",run);
    var bound=db.queryForObject("SELECT campaign_id FROM outbound_campaign_binding WHERE singleton=1 FOR UPDATE",String.class);
    if(!run.equals(bound))throw new ApiException(409,"持久验收批次已固定，不能更换编号重新获得额度","CAMPAIGN_ID_MISMATCH");
    for(var sample:samples){
      var named=db.queryForList("SELECT * FROM outbound_campaign_samples WHERE campaign_id=? AND sample_id=?",run,sample.get("sample_id"));
      if(!named.isEmpty()&&(!sample.get("fingerprint").equals(named.getFirst().get("sample_fingerprint"))||!sample.get("phase").equals(named.getFirst().get("phase"))))throw new ApiException(409,"已注册样本不能改编号、指纹或开发/留出分组","CAMPAIGN_SAMPLE_REGISTRY_CHANGED");
      db.update("INSERT INTO outbound_campaign_samples(campaign_id,sample_id,sample_fingerprint,phase) VALUES(?,?,?,?) ON CONFLICT(campaign_id,sample_fingerprint) DO NOTHING",run,sample.get("sample_id"),sample.get("fingerprint"),sample.get("phase"));
      var stored=db.queryForMap("SELECT * FROM outbound_campaign_samples WHERE campaign_id=? AND sample_fingerprint=?",run,sample.get("fingerprint"));
      if(!sample.get("sample_id").equals(stored.get("sample_id"))||!sample.get("phase").equals(stored.get("phase")))throw new ApiException(409,"已注册样本元数据不可更换","CAMPAIGN_SAMPLE_REGISTRY_CHANGED");
    }
    if(db.queryForObject("SELECT count(*) FROM outbound_campaign_samples WHERE campaign_id=?",Long.class,run)>4||!db.queryForList("SELECT phase FROM outbound_campaign_samples WHERE campaign_id=? GROUP BY phase HAVING count(*)>2",run).isEmpty())throw new ApiException(409,"注册样本最多四份且开发与留出各两份","CAMPAIGN_SAMPLE_REGISTRY_EXHAUSTED");
    db.update("UPDATE outbound_campaigns SET text_limit=LEAST(text_limit,?),embedding_limit=LEAST(embedding_limit,?),amap_limit=LEAST(amap_limit,?),token_limit=LEAST(token_limit,?) WHERE id=?",textMax,embeddingMax,amapMax,tokenMax,run);
    return db.queryForMap("SELECT * FROM outbound_campaigns WHERE id=? FOR UPDATE",run);
  }
  public void reserve(String kind,long tokens){
    if(!enabled())return;
    if(!Set.of("text","embedding","amap").contains(kind)||tokens<0||(!kind.equals("amap")&&tokens<1))throw new ApiException(422,"验收调用必须先提供有界Token预留","TOKEN_RESERVATION_REQUIRED");
    var row=locked();String column=kind+"_used",limit=kind+"_limit";
    if(((Number)row.get(column)).longValue()>=((Number)row.get(limit)).longValue()||tokens>((Number)row.get("token_limit")).longValue()-((Number)row.get("reserved_tokens")).longValue())
      throw new ApiException(429,"固定验收批次的调用或Token预留额度已用完","CAMPAIGN_BUDGET_EXHAUSTED");
    db.update("UPDATE outbound_campaigns SET "+column+"="+column+"+1,reserved_tokens=reserved_tokens+? WHERE id=?",tokens,run);
  }
  public void workflow(String rootTask,String sampleFingerprint){
    if(!enabled())return;
    locked();
    var registration=db.queryForList("SELECT phase FROM outbound_campaign_samples WHERE campaign_id=? AND sample_fingerprint=?",run,sampleFingerprint);
    if(registration.isEmpty())throw new ApiException(409,"完整流程样本尚未由业务服务注册，禁止临时换输入绕过槽位","CAMPAIGN_SAMPLE_NOT_REGISTERED");
    String registeredPhase=registration.getFirst().get("phase").toString();
    if(!registeredPhase.equals(phase))throw new ApiException(409,"样本属于另一服务端固定分组，当前阶段不能执行","CAMPAIGN_PHASE_MISMATCH");
    var existing=db.queryForList("SELECT sample_fingerprint FROM outbound_campaign_workflows WHERE campaign_id=? AND root_task_id=?",run,rootTask);
    if(!existing.isEmpty()){
      if(!sampleFingerprint.equals(existing.getFirst().get("sample_fingerprint")))throw new ApiException(409,"流程槽位已绑定不同样本","CAMPAIGN_SAMPLE_MISMATCH");
      return;
    }
    if(!db.queryForList("SELECT root_task_id FROM outbound_campaign_workflows WHERE campaign_id=? AND sample_fingerprint=?",run,sampleFingerprint).isEmpty())throw new ApiException(409,"本批次同一样本已保留完整流程槽位，请查看原任务或显式恢复","CAMPAIGN_SAMPLE_REUSED");
    long total=db.queryForObject("SELECT count(*) FROM outbound_campaign_workflows WHERE campaign_id=?",Long.class,run);
    long used=db.queryForObject("SELECT count(*) FROM outbound_campaign_workflows WHERE campaign_id=? AND phase=?",Long.class,run,registeredPhase);
    if(total>=4||used>=2)throw new ApiException(429,"完整流程槽位已用完；开发与最终验收各保留两次","CAMPAIGN_WORKFLOW_EXHAUSTED");
    db.update("INSERT INTO outbound_campaign_workflows(campaign_id,root_task_id,sample_fingerprint,phase) VALUES(?,?,?,?)",run,rootTask,sampleFingerprint,registeredPhase);
  }
  public Map<String,Object> status(){
    if(!enabled())return Map.of("enabled",false,"browser_sdk_included",false);
    var rows=db.queryForList("SELECT * FROM outbound_campaigns WHERE id=?",run);
    var result=new LinkedHashMap<String,Object>(Map.of("enabled",true,"run_id",run,"phase",phase,"persisted",!rows.isEmpty(),"counters",rows.isEmpty()?Map.of():rows.getFirst(),
        "workflow_limit",4,"phase_workflow_limit",2,"workflow_used",db.queryForList("SELECT phase,count(*) AS used FROM outbound_campaign_workflows WHERE campaign_id=? GROUP BY phase",run),
        "browser_sdk_included",false,"token_note","发送前的保守预留，不是模型供应商账单；失败与不确定结果不退额度"));
    result.put("registered_samples",db.queryForList("SELECT sample_id,sample_fingerprint,phase FROM outbound_campaign_samples WHERE campaign_id=? ORDER BY phase,sample_id",run));
    result.put("configured_samples",samples);
    result.put("configured_limits",Map.of("text",textMax,"embedding",embeddingMax,"amap_rest",amapMax,"reserved_tokens",tokenMax));
    result.put("map_transport","rest");return result;
  }
}
