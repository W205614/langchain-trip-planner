package com.tripplanner.domain;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.persistence.*;
import java.util.*;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@Service
@org.springframework.context.annotation.DependsOn("singleInstance")
public class OutboxWorker {
  @org.springframework.beans.factory.annotation.Value("${WORKERS_ENABLED:true}")
  private boolean workersEnabled;

  private final OutboxMapper jobs;
  private final HistoryMapper history;
  private final KnowledgeMapper knowledge;
  private final AgentClient agent;
  private final TransactionTemplate tx;
  private final JsonMapper json;
  private final KnowledgeService files;

  public OutboxWorker(
      OutboxMapper jobs,
      HistoryMapper history,
      KnowledgeMapper knowledge,
      AgentClient agent,
      TransactionTemplate tx,
      JsonMapper json,
      KnowledgeService files) {
    this.jobs = jobs;
    this.history = history;
    this.knowledge = knowledge;
    this.agent = agent;
    this.tx = tx;
    this.json = json;
    this.files = files;
  }

  @Scheduled(fixedDelay = 1000, scheduler = "outboxScheduler")
  public synchronized void tick() {
    if (!workersEnabled) return;
    var h = jobs.nextHistory();
    var k = jobs.nextKnowledge();
    if (h == null && k == null) return;
    // Keep durable work pending while the AI service is offline. An outage must not burn through
    // retry attempts or move uploaded documents to a terminal failure state.
    if (!agent.available()) return;
    if (h != null) run(h, false);
    if (k != null) run(k, true);
  }

  private void status(
      boolean knowledge, long id, String status, int attempts, String error, int delay) {
    if (knowledge) jobs.knowledge(id, status, attempts, error, delay);
    else jobs.history(id, status, attempts, error, delay);
  }

  private void run(Map<String, Object> job, boolean isKnowledge) {
    long id = ((Number) job.get("id")).longValue();
    int attempts = ((Number) job.get("attempts")).intValue() + 1;
    status(isKnowledge, id, "running", attempts, "", 0);
    try {
      if (isKnowledge) processKnowledge(job);
      else {
        long uid = ((Number) job.get("user_id")).longValue(),
            recordId = ((Number) job.get("record_id")).longValue();
        var row = history.owned(uid, recordId);
        var request =
            json.createObjectNode()
                .put("record_id", recordId)
                .put("user_id", uid)
                .put("job_id", id);
        boolean deleted =
            row == null
                || json.readTree(row.get("quality_json").toString())
                    .path("outcome")
                    .asText("")
                    .equals("draft");
        request.put("operation", deleted ? "delete" : "upsert");
        if (row != null) request.set("record", json.valueToTree(row));
        agent.post("/index/history", request);
      }
      status(isKnowledge, id, "succeeded", attempts, "", 0);
    } catch (Exception ex) {
      status(
          isKnowledge,
          id,
          attempts >= 5 ? "failed" : "retry",
          attempts,
          "内部能力调用失败；请检查对应服务日志",
          Math.min(300, 1 << Math.min(8, attempts - 1)));
      if (isKnowledge && attempts >= 5)
        tx.executeWithoutResult(
            s -> {
              var current = knowledge.get(((Number) job.get("document_id")).longValue());
              if (current != null
                  && current.get("version").equals(job.get("document_version"))
                  && Set.of("queued", "processing", "publishing").contains(current.get("status"))) {
                current.put("status", "failed");
                current.put("review_note", "处理失败；请在索引作业列表检查并重放");
                current.put("next_version", current.get("version"));
                knowledge.update(current);
              }
            });
    }
  }

  private void processKnowledge(Map<String, Object> job) throws java.io.IOException {
    long id = ((Number) job.get("document_id")).longValue();
    int version = ((Number) job.get("document_version")).intValue();
    var document = knowledge.get(id);
    if (document == null || ((Number) document.get("version")).intValue() != version) return;
    String state = document.get("status").toString();
    if (state.equals("deleted")) {
      agent.post(
          "/index/document",
          Map.of("operation", "delete", "document_id", id, "document_version", version));
      files.removeDeletedOriginal(document);
      return;
    }
    if (!Set.of("queued", "processing", "failed", "publishing").contains(state)) return;
    if (state.equals("publishing")
        || (state.equals("failed") && "publish".equals(job.get("phase")))) {
      agent.post(
          "/index/document",
          Map.of(
              "operation",
              "upsert",
              "document_id",
              id,
              "document_version",
              version,
              "document",
              document));
      tx.executeWithoutResult(
          s -> {
            var current = knowledge.get(id);
            if (current == null
                || ((Number) current.get("version")).intValue() != version
                || !Set.of("publishing", "failed").contains(current.get("status"))) return;
            current.put("status", "published");
            current.put("next_version", version);
            knowledge.update(current);
          });
    } else {
      var result =
          agent.post(
              "/documents/extract",
              Map.of(
                  "document_id",
                  id,
                  "document_version",
                  version,
                  "city",
                  document.get("city"),
                  "title",
                  document.get("title")));
      tx.executeWithoutResult(
          s -> {
            var current = knowledge.get(id);
            if (current == null
                || ((Number) current.get("version")).intValue() != version
                || !Set.of("queued", "processing", "failed").contains(current.get("status")))
              return;
            current.put("status", "awaiting_review");
            current.put("page_count", result.path("pages").size());
            current.put("extracted_pages_json", result.path("pages").toString());
            current.put(
                "source_text",
                String.join(
                    "\n\n", result.path("pages").valueStream().map(p -> p.asText("")).toList()));
            current.put("next_version", version);
            knowledge.update(current);
          });
    }
  }
}
