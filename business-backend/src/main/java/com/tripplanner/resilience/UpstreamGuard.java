package com.tripplanner.resilience;

import com.tripplanner.api.ApiException;
import io.micrometer.core.instrument.*;
import java.time.Duration;
import java.util.Locale;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Small single-instance bulkhead and circuit breaker for slow remote dependencies.
 *
 * <p>It deliberately does not retry: retrying a saturated model or map provider would multiply
 * load. The first requests fail with their original error; after a bounded consecutive-failure
 * threshold, later requests fail fast until one half-open probe succeeds.
 */
public final class UpstreamGuard {
  @FunctionalInterface
  public interface Operation<T> {
    T run() throws Exception;
  }

  private static final Logger log = LoggerFactory.getLogger(UpstreamGuard.class);
  private final String name;
  private final String codePrefix;
  private final Semaphore permits;
  private final int failureThreshold;
  private final long openNanos;
  private final long acquireMillis;
  private final AtomicInteger consecutiveFailures = new AtomicInteger();
  private final AtomicLong openUntil = new AtomicLong();
  private final AtomicBoolean halfOpenProbe = new AtomicBoolean();
  private final Counter capacityRejected;
  private final Counter circuitRejected;
  private final Counter opened;
  private final Timer duration;

  public UpstreamGuard(
      String name,
      int concurrency,
      int failureThreshold,
      Duration openDuration,
      Duration acquireWait,
      MeterRegistry registry) {
    this.name = name;
    this.codePrefix = name.toUpperCase(Locale.ROOT).replaceAll("[^A-Z0-9]+", "_");
    this.permits = new Semaphore(Math.max(1, concurrency), true);
    this.failureThreshold = Math.max(1, failureThreshold);
    this.openNanos = Math.max(1, openDuration.toNanos());
    this.acquireMillis = Math.max(0, acquireWait.toMillis());
    capacityRejected = counter(registry, "capacity");
    circuitRejected = counter(registry, "circuit_open");
    opened = Counter.builder("trip.upstream.circuit.opened").tag("upstream", name).register(registry);
    duration = Timer.builder("trip.upstream.call.duration").tag("upstream", name).register(registry);
    Gauge.builder("trip.upstream.available.permits", permits, Semaphore::availablePermits)
        .tag("upstream", name)
        .register(registry);
  }

  private Counter counter(MeterRegistry registry, String reason) {
    return Counter.builder("trip.upstream.rejected")
        .tag("upstream", name)
        .tag("reason", reason)
        .register(registry);
  }

  public <T> T execute(Operation<T> operation) throws Exception {
    long now = System.nanoTime();
    long until = openUntil.get();
    boolean probe = until != 0 && now >= until;
    if (until > now || (probe && !halfOpenProbe.compareAndSet(false, true))) {
      circuitRejected.increment();
      throw new ApiException(503, "上游服务熔断中，请稍后重试", codePrefix + "_CIRCUIT_OPEN");
    }

    boolean acquired = false;
    Timer.Sample sample = Timer.start();
    try {
      acquired = permits.tryAcquire(acquireMillis, TimeUnit.MILLISECONDS);
      if (!acquired) {
        capacityRejected.increment();
        throw new ApiException(503, "上游服务繁忙，请稍后重试", codePrefix + "_BUSY");
      }
      T result = operation.run();
      boolean recovered = openUntil.getAndSet(0) != 0;
      consecutiveFailures.set(0);
      if (recovered) log.info("upstream_circuit_recovered upstream={}", name);
      return result;
    } catch (InterruptedException ex) {
      Thread.currentThread().interrupt();
      throw ex;
    } catch (Exception ex) {
      if (countsAsFailure(ex)) {
        int failures = consecutiveFailures.incrementAndGet();
        if (probe || failures >= failureThreshold) {
          long deadline = System.nanoTime() + openNanos;
          boolean changed = probe || openUntil.compareAndSet(0, deadline);
          if (probe) openUntil.set(deadline);
          if (changed) {
            consecutiveFailures.set(0);
            opened.increment();
            log.warn(
                "upstream_circuit_open upstream={} reason={} open_ms={}",
                name,
                ex.getClass().getSimpleName(),
                TimeUnit.NANOSECONDS.toMillis(openNanos));
          }
        }
      }
      throw ex;
    } finally {
      if (acquired) permits.release();
      if (probe) halfOpenProbe.set(false);
      sample.stop(duration);
    }
  }

  private boolean countsAsFailure(Exception exception) {
    if (exception instanceof ApiException api) {
      if ((codePrefix + "_BUSY").equals(api.code)) return false;
      return api.status == 429 || api.status >= 500;
    }
    return true;
  }
}
