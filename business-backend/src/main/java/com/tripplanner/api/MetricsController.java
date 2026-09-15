package com.tripplanner.api;

import java.nio.file.*;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

/** Internal-network metrics: no user IDs, prompts, tokens, or provider credentials. */
@RestController
public class MetricsController {
  private final JdbcTemplate jdbc;
  private final Path storage;

  public MetricsController(
      JdbcTemplate jdbc, @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}") String storage) {
    this.jdbc = jdbc;
    this.storage = Path.of(storage);
  }

  @GetMapping(value = "/metrics", produces = "text/plain;version=0.0.4")
  public String metrics() throws Exception {
    var output = new StringBuilder();
    output
        .append("# TYPE trip_plan_total counter\ntrip_plan_total{outcome=\"draft\"} ")
        .append(
            jdbc.queryForObject(
                "SELECT count(*) FROM trip_tasks WHERE status='needs_attention'", Long.class))
        .append('\n');
    for (var entry :
        Map.of(
                "task",
                "trip_tasks",
                "history",
                "rag_sync_jobs",
                "knowledge",
                "knowledge_ingest_jobs")
            .entrySet()) {
      for (String status :
          List.of(
              "queued",
              "running",
              "succeeded",
              "needs_attention",
              "failed",
              "cancelled",
              "pending",
              "waiting",
              "retry")) {
        long count =
            jdbc.queryForObject(
                "SELECT count(*) FROM " + entry.getValue() + " WHERE status=?", Long.class, status);
        output
            .append("trip_durable_jobs{kind=\"")
            .append(entry.getKey())
            .append("\",status=\"")
            .append(status)
            .append("\"} ")
            .append(count)
            .append('\n');
      }
    }
    output
        .append("trip_oldest_queue_age_seconds ")
        .append(age("SELECT min(created_at) FROM trip_tasks WHERE status='queued'"))
        .append('\n');
    output
        .append("trip_oldest_sync_age_seconds ")
        .append(
            age(
                "SELECT min(created_at) FROM (SELECT created_at FROM rag_sync_jobs WHERE"
                    + " status<>'succeeded' UNION ALL SELECT created_at FROM knowledge_ingest_jobs"
                    + " WHERE status<>'succeeded') q"))
        .append('\n');
    for (String code :
        List.of(
            "TASK_TIMEOUT",
            "PROCESS_INTERRUPTED",
            "AGENT_CONNECTION_LOST",
            "GENERATION_FAILED",
            "VERSION_CONFLICT")) {
      output
          .append("trip_task_failures{code=\"")
          .append(code)
          .append("\"} ")
          .append(
              jdbc.queryForObject(
                  "SELECT count(*) FROM trip_tasks WHERE status='failed' AND error_code=?",
                  Long.class,
                  code))
          .append('\n');
    }
    if (Files.exists(storage)) {
      var disk = Files.getFileStore(storage);
      output
          .append("trip_data_disk_free_ratio ")
          .append((double) disk.getUsableSpace() / disk.getTotalSpace())
          .append('\n');
    }
    return output.toString();
  }

  private double age(String query) {
    return jdbc.queryForObject(
        "SELECT COALESCE(EXTRACT(EPOCH FROM (timezone('UTC',now())-oldest)),0) FROM ("
            + query
            + ") t(oldest)",
        Double.class);
  }
}
