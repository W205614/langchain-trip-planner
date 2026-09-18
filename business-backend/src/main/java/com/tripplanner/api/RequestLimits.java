package com.tripplanner.api;

import jakarta.servlet.http.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.*;

/** Bounded single-instance rate windows. Forwarded client identity is not trusted here. */
@Configuration
public class RequestLimits implements WebMvcConfigurer, HandlerInterceptor {
  @org.springframework.beans.factory.annotation.Value("${APP_ENV:production}")
  private String environment;

  @org.springframework.beans.factory.annotation.Value("${VALIDATION_ALLOW_FIXTURES:no}")
  private String fixtures;

  /**
   * One lock per caller/route bucket. A single global synchronized interceptor made unrelated
   * users queue behind each other during a burst and turned the limiter itself into a bottleneck.
   */
  private final ConcurrentHashMap<String, Window> windows = new ConcurrentHashMap<>();
  private final AtomicLong nextCleanupAt = new AtomicLong();

  private static final class Window {
    private final ArrayDeque<Long> timestamps = new ArrayDeque<>();
    private volatile long lastSeen;

    synchronized boolean allow(long now, long cutoff, int limit) {
      while (!timestamps.isEmpty() && timestamps.getFirst() < cutoff) timestamps.removeFirst();
      lastSeen = now;
      if (timestamps.size() >= limit) return false;
      timestamps.addLast(now);
      return true;
    }
  }

  @Override
  public void addInterceptors(InterceptorRegistry registry) {
    registry.addInterceptor(this).addPathPatterns("/api/**");
  }

  @Override
  public boolean preHandle(
      HttpServletRequest req, HttpServletResponse res, Object handler) {
    if ("validation".equals(environment) && "yes".equals(fixtures)) return true;
    String path = req.getRequestURI();
    int limit = 0;
    long window = 60_000;
    if (path.equals("/api/auth/register")) limit = 5;
    else if (path.equals("/api/auth/login")) limit = 10;
    else if (path.startsWith("/api/map/") || path.startsWith("/api/poi/")) limit = 30;
    else if (req.getMethod().equals("POST") && path.equals("/api/favorites")) limit = 30;
    else if (req.getMethod().equals("POST") && path.equals("/api/trips")) limit = 10;
    else if (req.getMethod().equals("POST") && path.startsWith("/api/assistant/conversations")) limit = 10;
    else if (path.equals("/api/rag/rebuild")) {
      limit = 2;
      window = 3_600_000;
    } else if (req.getMethod().equals("POST")
        && (path.startsWith("/api/trip/") && !path.endsWith("/cancel")
            || path.endsWith("/revise-task")
            || path.endsWith("/revise-day"))) limit = 5;
    if (limit == 0) return true;
    long now = Instant.now().toEpochMilli(), cutoff = now - window;
    cleanup(now);
    String caller =
        req.getUserPrincipal() == null
            ? "ip:" + req.getRemoteAddr()
            : "user:" + req.getUserPrincipal().getName();
    String key = caller + ":" + path.replaceAll("/[0-9a-fA-F-]{8,}/", "/{id}/");
    if (windows.size() >= 10000 && !windows.containsKey(key))
      throw new ApiException(429, "限流容量已满，请稍后重试", "REQUEST_RATE_LIMITED");
    var bucket = windows.computeIfAbsent(key, ignored -> new Window());
    if (!bucket.allow(now, cutoff, limit)) {
      res.setHeader("Retry-After", Long.toString(Math.max(1, window / 1000)));
      throw new ApiException(429, "请求过于频繁，请稍后重试", "REQUEST_RATE_LIMITED");
    }
    return true;
  }

  private void cleanup(long now) {
    long due = nextCleanupAt.get();
    if (now < due || !nextCleanupAt.compareAndSet(due, now + 60_000)) return;
    long stale = now - 3_600_000;
    windows.entrySet().removeIf(entry -> entry.getValue().lastSeen < stale);
  }
}
