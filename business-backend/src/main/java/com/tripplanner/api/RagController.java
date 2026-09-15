package com.tripplanner.api;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.persistence.*;
import java.util.Map;
import java.util.concurrent.locks.ReentrantLock;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/rag")
public class RagController {
  private final OutboxMapper jobs;
  private final AgentClient agent;
  private final ReentrantLock rebuild = new ReentrantLock();
  private final KnowledgeMapper knowledge;
  private final org.springframework.transaction.support.TransactionTemplate tx;

  public RagController(
      OutboxMapper jobs,
      AgentClient agent,
      KnowledgeMapper knowledge,
      org.springframework.transaction.support.TransactionTemplate tx) {
    this.jobs = jobs;
    this.agent = agent;
    this.knowledge = knowledge;
    this.tx = tx;
  }

  @PostMapping("/jobs/{kind}/{id}/replay")
  public Object replay(@PathVariable String kind, @PathVariable long id) {
    if (!java.util.Set.of("history", "knowledge").contains(kind))
      throw new ApiException(404, "任务类型不存在");
    return tx.execute(
        s -> {
          boolean isKnowledge = kind.equals("knowledge");
          var row = isKnowledge ? jobs.knowledgeJob(id) : jobs.historyJob(id);
          if (row == null) throw new ApiException(404, "任务不存在");
          if (!java.util.Set.of("failed", "waiting", "retry").contains(row.get("status")))
            throw new ApiException(409, "只有失败或等待任务可以重放");
          if (isKnowledge) {
            var document = knowledge.get(((Number) row.get("document_id")).longValue());
            if (document == null
                || !document.get("version").equals(row.get("document_version"))
                || java.util.Set.of("published", "rejected").contains(document.get("status")))
              throw new ApiException(409, "资料已失效或已发布");
            if (!java.util.Set.of("deleted", "publishing").contains(document.get("status"))) {
              document.put("status", "publish".equals(row.get("phase")) ? "publishing" : "queued");
              document.put("next_version", document.get("version"));
              if (knowledge.update(document) != 1) throw new ApiException(409, "资料版本已变更");
            }
            jobs.knowledge(id, "pending", 0, "", 0);
          } else jobs.history(id, "pending", 0, "", 0);
          return Map.of("success", true);
        });
  }

  @GetMapping("/jobs")
  public Object jobs() {
    return Map.of("history", jobs.historyFailures(), "knowledge", jobs.knowledgeFailures());
  }

  @PostMapping("/rebuild")
  public Object rebuild() {
    if (!rebuild.tryLock()) throw new ApiException(409, "已有重建任务运行中");
    try {
      return agent.post("/index/rebuild", Map.of());
    } finally {
      rebuild.unlock();
    }
  }
}
