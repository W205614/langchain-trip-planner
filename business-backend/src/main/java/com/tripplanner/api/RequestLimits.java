package com.tripplanner.api;

import jakarta.servlet.http.*;
import java.time.Instant;
import java.util.*;
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

  private final Map<String, ArrayDeque<Long>> windows = new HashMap<>();

  @Override
  public void addInterceptors(InterceptorRegistry registry) {
    registry.addInterceptor(this).addPathPatterns("/api/**");
  }

  @Override
  public synchronized boolean preHandle(
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
    windows
        .entrySet()
        .removeIf(e -> e.getValue().isEmpty() || e.getValue().getLast() < now - 3_600_000);
    String key = req.getRemoteAddr() + ":" + path.replaceAll("/[0-9a-fA-F-]{8,}/", "/{id}/");
    if (windows.size() >= 10000 && !windows.containsKey(key))
      throw new ApiException(429, "限流容量已满，请稍后重试");
    var queue = windows.computeIfAbsent(key, k -> new ArrayDeque<>());
    while (!queue.isEmpty() && queue.getFirst() < cutoff) queue.removeFirst();
    if (queue.size() >= limit) throw new ApiException(429, "请求过于频繁，请稍后重试");
    queue.addLast(now);
    return true;
  }
}
