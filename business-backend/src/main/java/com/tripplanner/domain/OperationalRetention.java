package com.tripplanner.domain;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/** Batch only disposable payloads. Budget/audit rows, pending jobs and tombstones are retained. */
@Service
public class OperationalRetention {
  private final JdbcTemplate db;private final SingleInstance owner;
  private final int terminalDays,checkpointGraceHours;
  public OperationalRetention(JdbcTemplate db,SingleInstance owner,
      @Value("${TERMINAL_PAYLOAD_RETENTION_DAYS:90}")int terminalDays,
      @Value("${CHECKPOINT_EXPIRY_GRACE_HOURS:24}")int checkpointGraceHours){
    this.db=db;this.owner=owner;this.terminalDays=Math.max(7,terminalDays);this.checkpointGraceHours=Math.max(1,checkpointGraceHours);
  }
  @Scheduled(fixedDelayString="${RETENTION_INTERVAL_MILLIS:3600000}")
  public void tick(){if(owner.owned())purgeBatch();}
  public Map<String,Integer> purgeBatch(){
    int checkpoints=db.update("DELETE FROM execution_checkpoints WHERE (task_id,stage) IN (SELECT task_id,stage FROM execution_checkpoints WHERE expires_at<now()-make_interval(hours=>?) ORDER BY expires_at LIMIT 200)",checkpointGraceHours);
    // Keep task IDs, status, lineage, fingerprints and call budget accounting; old resume has a clear terminal reason.
    int payloads=db.update("UPDATE trip_tasks SET request_json='{\"_payload_expired\":true}' WHERE id IN (SELECT id FROM trip_tasks WHERE status IN ('succeeded','needs_attention','failed','cancelled') AND updated_at<timezone('UTC',now())-make_interval(days=>?) AND request_json::jsonb->>'_payload_expired' IS DISTINCT FROM 'true' ORDER BY updated_at LIMIT 100)",terminalDays);
    // Preserve the latest success per entity and all unsuccessful work, including deletion work.
    int history=db.update("DELETE FROM rag_sync_jobs WHERE id IN (SELECT j.id FROM rag_sync_jobs j WHERE j.status='succeeded' AND j.updated_at<timezone('UTC',now())-make_interval(days=>?) AND EXISTS(SELECT 1 FROM rag_sync_jobs newer WHERE newer.record_id=j.record_id AND newer.id>j.id AND newer.status='succeeded') ORDER BY j.id LIMIT 200)",terminalDays);
    int knowledge=db.update("DELETE FROM knowledge_ingest_jobs WHERE id IN (SELECT j.id FROM knowledge_ingest_jobs j WHERE j.status='succeeded' AND j.updated_at<timezone('UTC',now())-make_interval(days=>?) AND EXISTS(SELECT 1 FROM knowledge_ingest_jobs newer WHERE newer.document_id=j.document_id AND newer.id>j.id AND newer.status='succeeded') ORDER BY j.id LIMIT 200)",terminalDays);
    return Map.of("expired_checkpoints",checkpoints,"terminal_payloads",payloads,"old_history_successes",history,"old_knowledge_successes",knowledge);
  }
}
