package com.tripplanner.domain;

import io.micrometer.core.instrument.*;
import jakarta.annotation.PostConstruct;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/** Low-cardinality operational metrics. Database state is aggregated once per refresh, not per scrape. */
@Service
public class BusinessMetrics {
  private final JdbcTemplate jdbc;
  private final MeterRegistry registry;
  private final MultiGauge jobs;
  private final MultiGauge failures;
  private final Path storage;
  private final AtomicReference<Double> oldestQueueAge = new AtomicReference<>(0d);
  private final AtomicReference<Double> oldestSyncAge = new AtomicReference<>(0d);
  private final AtomicReference<Double> diskFreeRatio = new AtomicReference<>(1d);

  public BusinessMetrics(
      JdbcTemplate jdbc,
      MeterRegistry registry,
      AmapGateway amap,
      @Value("${UPLOAD_DIR:../backend/data/knowledge_uploads}") String storage) {
    this.jdbc = jdbc;
    this.registry = registry;
    this.storage = Path.of(storage);
    jobs = MultiGauge.builder("trip.durable.jobs").description("Durable work by kind and status").register(registry);
    failures = MultiGauge.builder("trip.task.failures").description("Persisted task failures by code").register(registry);
    Gauge.builder("trip.oldest.queue.age.seconds", oldestQueueAge, AtomicReference::get).register(registry);
    Gauge.builder("trip.oldest.sync.age.seconds", oldestSyncAge, AtomicReference::get).register(registry);
    Gauge.builder("trip.data.disk.free.ratio", diskFreeRatio, AtomicReference::get).register(registry);
    for (String metric : List.of("requests", "failures", "poi_cache_size", "weather_cache_size", "detail_cache_size", "route_cache_size"))
      Gauge.builder("trip.amap.rest." + metric, amap, value -> value.metrics().getOrDefault(metric, 0L))
          .register(registry);
  }

  @PostConstruct
  void initialRefresh() {
    refresh();
  }

  @Scheduled(fixedDelayString = "${METRICS_REFRESH_MILLIS:15000}")
  public void refresh() {
    var counts = new HashMap<String, Number>();
    jdbc.queryForList(
            """
            SELECT kind,status,count(*) AS value FROM (
              SELECT 'task' AS kind,status FROM trip_tasks
              UNION ALL SELECT 'history',status FROM rag_sync_jobs
              UNION ALL SELECT 'knowledge',status FROM knowledge_ingest_jobs
            ) jobs GROUP BY kind,status
            """)
        .forEach(
            row ->
                counts.put(
                    row.get("kind") + "\n" + row.get("status"), (Number) row.get("value")));
    var rows = new ArrayList<MultiGauge.Row<?>>();
    for (String kind : List.of("task", "history", "knowledge"))
      for (String status :
          List.of(
              "queued", "running", "succeeded", "needs_attention", "failed", "cancelled",
              "pending", "waiting", "retry"))
        rows.add(
            MultiGauge.Row.of(
                Tags.of("kind", kind, "status", status),
                counts.getOrDefault(kind + "\n" + status, 0)));
    jobs.register(rows, true);

    var failureRows = new ArrayList<MultiGauge.Row<?>>();
    var failureCounts = new HashMap<String, Number>();
    jdbc.queryForList(
            "SELECT error_code AS code,count(*) AS value FROM trip_tasks WHERE status='failed' AND error_code<>'' GROUP BY error_code")
        .forEach(
            row -> failureCounts.put(row.get("code").toString(), (Number) row.get("value")));
    for (String code :
        List.of(
            "TASK_TIMEOUT",
            "PROCESS_INTERRUPTED",
            "AGENT_CONNECTION_LOST",
            "GENERATION_FAILED",
            "VERSION_CONFLICT"))
      failureRows.add(
          MultiGauge.Row.of(Tags.of("code", code), failureCounts.getOrDefault(code, 0)));
    failures.register(failureRows, true);
    oldestQueueAge.set(
        age("SELECT min(created_at) FROM trip_tasks WHERE status='queued'"));
    oldestSyncAge.set(
        age(
            "SELECT min(created_at) FROM (SELECT created_at FROM rag_sync_jobs WHERE status<>'succeeded' UNION ALL SELECT created_at FROM knowledge_ingest_jobs WHERE status<>'succeeded') q"));
    try {
      if (Files.exists(storage)) {
        var disk = Files.getFileStore(storage);
        diskFreeRatio.set((double) disk.getUsableSpace() / Math.max(1L, disk.getTotalSpace()));
      }
    } catch (Exception ignored) {
      // A missing disk metric must not make the business service unavailable.
    }
  }

  public void recordPlanOutcomeAfterCommit(String outcome) {
    Runnable increment =
        () -> registry.counter("trip.plan.total", "outcome", "draft".equals(outcome) ? "draft" : "complete").increment();
    if (TransactionSynchronizationManager.isSynchronizationActive())
      TransactionSynchronizationManager.registerSynchronization(
          new TransactionSynchronization() {
            @Override
            public void afterCommit() {
              increment.run();
            }
          });
    else increment.run();
  }

  private double age(String query) {
    Double value =
        jdbc.queryForObject(
            "SELECT COALESCE(EXTRACT(EPOCH FROM (timezone('UTC',now())-oldest)),0) FROM ("
                + query
                + ") t(oldest)",
            Double.class);
    return value == null ? 0 : value;
  }
}
