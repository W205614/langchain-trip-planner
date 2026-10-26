package com.tripplanner;
import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import com.tripplanner.domain.OperationalRetention;

@SpringBootTest(properties={"trip.jwt-secret=integration-secret-only-01234567890123456789","trip.internal-key=integration-internal-only-01234567890123456789","spring.datasource.url=${TEST_DATABASE_URL}","spring.datasource.username=trip","spring.datasource.password=${TEST_DATABASE_PASSWORD:isolated-test-only}","WORKERS_ENABLED=false","TERMINAL_PAYLOAD_RETENTION_DAYS=7"})
@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class OperationalRetentionIntegrationTest {
  @Autowired JdbcTemplate db;@Autowired OperationalRetention retention;
  @Test void disposablePayloadsExpireButBudgetLineageAndPendingWorkRemain(){
    long uid=db.queryForObject("INSERT INTO users(username,hashed_password) VALUES(?,'test-only') RETURNING id",Long.class,"retention_"+UUID.randomUUID());
    String task=UUID.randomUUID().toString(),execution=UUID.randomUUID().toString(),scope="retention-proof:"+task;
    db.update("INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,status,created_at,updated_at,deadline_at) VALUES(?,?,?,'fixture','{\"planning_input\":{\"_payload_expired\":false},\"private_note\":\"synthetic\"}','failed',timezone('UTC',now())-interval '12 days',timezone('UTC',now())-interval '10 days',timezone('UTC',now())-interval '11 days')",task,uid,task);
    db.update("INSERT INTO execution_checkpoints(task_id,execution_id,stage,input_hash,payload,compatibility,expires_at) VALUES(?,?,'old','hash','{}','{}',now()-interval '2 days')",task,execution);
    db.update("INSERT INTO outbound_budget_counters(scope,kind,used) VALUES(?,'text',5)",scope);
    long job=db.queryForObject("INSERT INTO rag_sync_jobs(record_id,user_id,operation,status,created_at,updated_at) VALUES(999999,?,'delete','pending',timezone('UTC',now())-interval '12 days',timezone('UTC',now())-interval '10 days') RETURNING id",Long.class,uid);
    retention.purgeBatch();
    assertEquals("{\"_payload_expired\":true}",db.queryForObject("SELECT request_json FROM trip_tasks WHERE id=?",String.class,task));
    assertEquals(0,db.queryForObject("SELECT count(*) FROM execution_checkpoints WHERE task_id=?",Integer.class,task));
    assertEquals(5,db.queryForObject("SELECT used FROM outbound_budget_counters WHERE scope=?",Integer.class,scope));
    assertEquals("pending",db.queryForObject("SELECT status FROM rag_sync_jobs WHERE id=?",String.class,job));
    assertEquals(task,db.queryForObject("SELECT id FROM trip_tasks WHERE id=?",String.class,task));
  }
}
