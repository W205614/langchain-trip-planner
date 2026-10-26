package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.tripplanner.api.ApiException;
import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.*;
import java.util.*;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/** Isolated database only. Counters are deliberately never reset for another run. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={"trip.jwt-secret=integration-secret-only-01234567890123456789","trip.internal-key=integration-internal-only-01234567890123456789","spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip","spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false","FINAL_RUN_ID=optimization-budget-offline-test","FINAL_RUN_PHASE=development","FINAL_CAMPAIGN_SAMPLES_JSON=[{\"sample_id\":\"dev-a\",\"fingerprint\":\"sample-A\",\"phase\":\"development\"},{\"sample_id\":\"dev-b\",\"fingerprint\":\"sample-B\",\"phase\":\"development\"},{\"sample_id\":\"holdout-c\",\"fingerprint\":\"sample-C\",\"phase\":\"acceptance\"},{\"sample_id\":\"holdout-d\",\"fingerprint\":\"sample-D\",\"phase\":\"acceptance\"}]"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class CampaignBudgetIntegrationTest {
  static final String RUN="optimization-budget-offline-test";
  @Autowired JdbcTemplate db;@Autowired ExecutionGovernanceService governance;@Autowired CampaignBudgetService campaign;@Autowired TransactionTemplate tx;@Autowired JsonMapper json;
  @MockitoBean SingleInstance owner;@MockitoBean AgentClient agent;
  int concurrent(String kind,int attempts,long tokens)throws Exception{
    var start=new CountDownLatch(1);
    try(var pool=Executors.newFixedThreadPool(8)){
      var futures=new ArrayList<Future<Boolean>>();
      for(int i=0;i<attempts;i++)futures.add(pool.submit(()->{start.await();try{governance.reserve(json.createObjectNode().put("kind",kind).put("reserved_tokens",tokens).put("request_key",UUID.randomUUID().toString()));return true;}catch(ApiException ex){assertEquals(429,ex.status);return false;}}));
      start.countDown();int accepted=0;for(var future:futures)if(future.get(30,TimeUnit.SECONDS))accepted++;return accepted;
    }
  }
  String task(long user,String fingerprint){String task=UUID.randomUUID().toString();db.update("INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,status,execution_id,deadline_at) VALUES(?,?,?,?,?,'running',?,timezone('UTC',now())+interval '5 minutes')",task,user,task,fingerprint,"{}",UUID.randomUUID().toString());return task;}
  @Test void onePinnedCampaignEnforcesAtomicCallsTokensWorkflowHoldoutAndNeverResets()throws Exception{
    when(owner.owned()).thenReturn(true);
    assertEquals(0,db.queryForObject("SELECT count(*) FROM outbound_campaigns WHERE id=?",Integer.class,RUN),"Use a fresh isolated test database; a previous campaign must never be reset");
    var forged=json.createObjectNode().put("kind","amap").put("run_id","different-client-campaign").put("request_key","forged");
    assertEquals("CAMPAIGN_ID_CLIENT_CONTROLLED",assertThrows(ApiException.class,()->governance.reserve(forged)).code);
    var missing=json.createObjectNode().put("kind","text").put("request_key","missing-tokens");
    assertEquals("TOKEN_RESERVATION_REQUIRED",assertThrows(ApiException.class,()->governance.reserve(missing)).code);
    var mcp=json.createObjectNode().put("kind","amap").put("map_protocol","mcp").put("request_key","mcp-stopped");
    assertEquals("CAMPAIGN_REST_REQUIRED",assertThrows(ApiException.class,()->governance.reserve(mcp)).code);
    assertEquals(0,db.queryForObject("SELECT count(*) FROM outbound_reservations WHERE request_key='mcp-stopped'",Integer.class));
    assertEquals(12,concurrent("text",20,50000));
    assertEquals(600000L,db.queryForObject("SELECT reserved_tokens FROM outbound_campaigns WHERE id=?",Long.class,RUN));
    var tooMany=json.createObjectNode().put("kind","embedding").put("reserved_tokens",200001).put("request_key","too-many-tokens");
    assertEquals("CAMPAIGN_BUDGET_EXHAUSTED",assertThrows(ApiException.class,()->governance.reserve(tooMany)).code);
    assertEquals(0L,db.queryForObject("SELECT embedding_used FROM outbound_campaigns WHERE id=?",Long.class,RUN));
    assertEquals(40,concurrent("embedding",50,5000));
    assertEquals(120,concurrent("amap",136,0));
    var count=db.queryForMap("SELECT * FROM outbound_campaigns WHERE id=?",RUN);
    assertEquals(800000L,((Number)count.get("reserved_tokens")).longValue());assertEquals(12L,((Number)count.get("text_used")).longValue());assertEquals(40L,((Number)count.get("embedding_used")).longValue());assertEquals(120L,((Number)count.get("amap_used")).longValue());
    String used=db.queryForObject("SELECT request_key FROM outbound_reservations WHERE run_id=? ORDER BY id LIMIT 1",String.class,RUN);
    var replay=json.createObjectNode().put("kind","text").put("reserved_tokens",1).put("request_key",used);
    assertEquals("OUTBOUND_RESERVATION_REUSED",assertThrows(ApiException.class,()->governance.reserve(replay)).code);assertEquals(count,db.queryForMap("SELECT * FROM outbound_campaigns WHERE id=?",RUN));
    var restarted=new CampaignBudgetService(db,RUN,"development",80,80,400,900000);
    assertEquals("CAMPAIGN_BUDGET_EXHAUSTED",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->restarted.reserve("amap",0))).code);
    var changed=new CampaignBudgetService(db,"another-server-id","development",12,40,120,800000);
    assertEquals("CAMPAIGN_ID_MISMATCH",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->changed.reserve("amap",0))).code);
    assertEquals(0,db.queryForObject("SELECT count(*) FROM outbound_campaigns WHERE id='another-server-id'",Integer.class));
    long user=db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'test-only') RETURNING id",Long.class,"budget_"+UUID.randomUUID());
    String first=task(user,"sample-A"),second=task(user,"sample-B"),third=task(user,"sample-C"),fourth=task(user,"sample-D"),fifth=task(user,"sample-E"),duplicate=task(user,"sample-A");
    tx.executeWithoutResult(s->campaign.workflow(first,"sample-A"));tx.executeWithoutResult(s->campaign.workflow(first,"sample-A"));tx.executeWithoutResult(s->campaign.workflow(second,"sample-B"));
    assertEquals("CAMPAIGN_SAMPLE_REUSED",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->campaign.workflow(duplicate,"sample-A"))).code);
    assertEquals("CAMPAIGN_PHASE_MISMATCH",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->campaign.workflow(third,"sample-C"))).code);
    var acceptance=new CampaignBudgetService(db,RUN,"acceptance",12,40,120,800000);
    tx.executeWithoutResult(s->acceptance.workflow(third,"sample-C"));tx.executeWithoutResult(s->acceptance.workflow(fourth,"sample-D"));
    assertEquals("CAMPAIGN_SAMPLE_NOT_REGISTERED",assertThrows(ApiException.class,()->tx.executeWithoutResult(s->acceptance.workflow(fifth,"sample-E"))).code);
    assertEquals(4,db.queryForObject("SELECT count(*) FROM outbound_campaign_workflows WHERE campaign_id=?",Integer.class,RUN));
    assertEquals(2,db.queryForObject("SELECT count(*) FROM outbound_campaign_workflows WHERE campaign_id=? AND phase='acceptance'",Integer.class,RUN));
    verifyNoInteractions(agent);
  }
}
