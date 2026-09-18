package com.tripplanner.resilience;

import static org.junit.jupiter.api.Assertions.*;

import com.tripplanner.api.ApiException;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class UpstreamGuardTest {
  @Test
  void opensAfterConsecutiveFailuresAndRecoversThroughOneProbe() throws Exception {
    var guard =
        new UpstreamGuard(
            "fixture", 2, 2, Duration.ofMillis(15), Duration.ZERO, new SimpleMeterRegistry());
    var calls = new AtomicInteger();
    for (int index = 0; index < 2; index++)
      assertThrows(
          IOException.class,
          () ->
              guard.execute(
                  () -> {
                    calls.incrementAndGet();
                    throw new IOException("down");
                  }));

    var open = assertThrows(ApiException.class, () -> guard.execute(() -> "unexpected"));
    assertEquals("FIXTURE_CIRCUIT_OPEN", open.code);
    assertEquals(2, calls.get(), "open circuit must reject without calling the dependency");

    Thread.sleep(25);
    assertEquals("ok", guard.execute(() -> "ok"));
    assertEquals("closed", guard.execute(() -> "closed"));
  }

  @Test
  void saturatedDependencyFailsFastInsteadOfQueuingRequestThreads() throws Exception {
    var guard =
        new UpstreamGuard(
            "fixture", 1, 5, Duration.ofSeconds(1), Duration.ZERO, new SimpleMeterRegistry());
    var entered = new CountDownLatch(1);
    var release = new CountDownLatch(1);
    try (var pool = Executors.newSingleThreadExecutor()) {
      var first =
          pool.submit(
              () ->
                  guard.execute(
                      () -> {
                        entered.countDown();
                        release.await();
                        return "done";
                      }));
      assertTrue(entered.await(2, TimeUnit.SECONDS));
      for (int index = 0; index < 10; index++) {
        var busy = assertThrows(ApiException.class, () -> guard.execute(() -> "unexpected"));
        assertEquals("FIXTURE_BUSY", busy.code);
      }
      release.countDown();
      assertEquals("done", first.get(2, TimeUnit.SECONDS));
      assertEquals("still-closed", guard.execute(() -> "still-closed"),
          "local bulkhead rejection must not count as an upstream failure");
    }
  }
}
