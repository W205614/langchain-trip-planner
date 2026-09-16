package com.tripplanner.domain;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.persistence.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

class OutboxWorkerTest {
  @Test
  void failedPublishRetryNeverReparsesImage() {
    var jobs = mock(OutboxMapper.class);
    when(jobs.nextHistory()).thenReturn(null);
    var knowledge = mock(KnowledgeMapper.class);
    var agent = mock(AgentClient.class);
    when(agent.available()).thenReturn(true);
    var job =
        Map.<String, Object>of(
            "id", 10L, "attempts", 4, "document_id", 7L, "document_version", 3, "phase", "publish");
    when(jobs.nextKnowledge()).thenReturn(job);
    when(knowledge.get(7))
        .thenReturn(new HashMap<>(Map.of("id", 7L, "version", 3, "status", "failed")));
    var worker =
        new OutboxWorker(
            jobs,
            mock(HistoryMapper.class),
            knowledge,
            agent,
            mock(TransactionTemplate.class),
            JsonMapper.builder().build(),
            mock(KnowledgeService.class));
    ReflectionTestUtils.setField(worker, "workersEnabled", true);
    worker.tick();
    verify(agent).post(eq("/index/document"), any());
    verify(agent, never()).post(eq("/documents/extract"), any());
    verify(jobs).knowledge(10L, "succeeded", 5, "", 0);
  }

  @Test
  void staleJobCannotRestoreDeletedOrNewerVersion() {
    var jobs = mock(OutboxMapper.class);
    when(jobs.nextHistory()).thenReturn(null);
    var knowledge = mock(KnowledgeMapper.class);
    var agent = mock(AgentClient.class);
    when(agent.available()).thenReturn(true);
    when(jobs.nextKnowledge())
        .thenReturn(Map.of("id", 10L, "attempts", 0, "document_id", 7L, "document_version", 2));
    when(knowledge.get(7)).thenReturn(Map.of("id", 7L, "version", 3, "status", "deleted"));
    var worker =
        new OutboxWorker(
            jobs,
            mock(HistoryMapper.class),
            knowledge,
            agent,
            mock(TransactionTemplate.class),
            JsonMapper.builder().build(),
            mock(KnowledgeService.class));
    ReflectionTestUtils.setField(worker, "workersEnabled", true);
    worker.tick();
    verify(agent).available();
    verify(agent, never()).post(anyString(), any());
    verify(knowledge, never()).update(any());
    verify(jobs).knowledge(10L, "succeeded", 1, "", 0);
  }

  @Test
  void unavailableAgentLeavesDurableJobsPendingWithoutConsumingAttempts() {
    var jobs = mock(OutboxMapper.class);
    when(jobs.nextHistory()).thenReturn(Map.of("id", 1L, "attempts", 0));
    var agent = mock(AgentClient.class);
    when(agent.available()).thenReturn(false);
    var worker = new OutboxWorker(jobs, mock(HistoryMapper.class), mock(KnowledgeMapper.class),
        agent, mock(TransactionTemplate.class), JsonMapper.builder().build(), mock(KnowledgeService.class));
    ReflectionTestUtils.setField(worker, "workersEnabled", true);
    worker.tick();
    verify(agent).available();
    verify(jobs).nextHistory(); verify(jobs).nextKnowledge();
    verify(jobs, never()).history(anyLong(),anyString(),anyInt(),anyString(),anyInt());
    verify(jobs, never()).knowledge(anyLong(),anyString(),anyInt(),anyString(),anyInt());
  }
}
