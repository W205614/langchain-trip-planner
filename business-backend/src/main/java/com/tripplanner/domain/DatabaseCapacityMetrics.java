package com.tripplanner.domain;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.PostConstruct;
import java.util.concurrent.atomic.AtomicReference;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/** Current database files only: excludes WAL/other databases and is not filesystem free space. */
@Service
public class DatabaseCapacityMetrics {
  private record Sample(double bytes, boolean available, long lastSuccessNanos) {}
  private final JdbcTemplate sampler;
  private final AtomicReference<Sample> sample =
      new AtomicReference<>(new Sample(Double.NaN, false, 0));

  public DatabaseCapacityMetrics(
      DataSource source,
      MeterRegistry registry,
      @Value("${DB_SIZE_WARNING_BYTES:10737418240}") long warningBytes,
      @Value("${DB_CAPACITY_QUERY_TIMEOUT_SECONDS:2}") int timeoutSeconds) {
    if (warningBytes <= 0) throw new IllegalArgumentException("DB_SIZE_WARNING_BYTES must be positive");
    // A dedicated template leaves the business template's statement policy unchanged.
    sampler = new JdbcTemplate(source);
    sampler.setQueryTimeout(Math.max(1, Math.min(5, timeoutSeconds)));
    Gauge.builder("trip.database.size.bytes", sample, value -> value.get().bytes())
        .description("Current database files; not PostgreSQL volume free bytes or a quota")
        .register(registry);
    Gauge.builder("trip.database.size.warning.bytes", () -> warningBytes).register(registry);
    Gauge.builder("trip.database.capacity.sample.available", sample,
        value -> value.get().available() ? 1 : 0).register(registry);
    Gauge.builder("trip.database.capacity.last.success.age.seconds", sample, value -> {
      long last = value.get().lastSuccessNanos();
      return last == 0 ? Double.POSITIVE_INFINITY : Math.max(0, (System.nanoTime() - last) / 1e9);
    }).register(registry);
  }

  @PostConstruct
  void initialSample() { refresh(); }

  @Scheduled(fixedDelayString = "${DB_CAPACITY_METRICS_REFRESH_MILLIS:60000}")
  public void refresh() {
    try {
      Long bytes = sampler.queryForObject("SELECT pg_database_size(current_database())", Long.class);
      if (bytes == null || bytes <= 0) throw new IllegalStateException("Database size unavailable");
      sample.set(new Sample(bytes.doubleValue(), true, System.nanoTime()));
    } catch (Exception ignored) {
      // Never expose a previous successful value as current healthy capacity.
      sample.updateAndGet(previous -> new Sample(Double.NaN, false, previous.lastSuccessNanos()));
    }
  }
}
