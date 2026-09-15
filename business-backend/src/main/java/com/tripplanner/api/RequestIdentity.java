package com.tripplanner.api;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.UUID;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class RequestIdentity extends OncePerRequestFilter {
  @Override
  protected void doFilterInternal(
      HttpServletRequest req, HttpServletResponse res, FilterChain chain)
      throws ServletException, IOException {
    String incoming = req.getHeader("X-Request-ID");
    String id =
        incoming != null && incoming.matches("[A-Za-z0-9_-]{1,64}")
            ? incoming
            : UUID.randomUUID().toString();
    req.setAttribute("request_id", id);
    res.setHeader("X-Request-ID", id);
    chain.doFilter(
        new HttpServletRequestWrapper(req) {
          @Override
          public String getHeader(String name) {
            return "X-Request-ID".equalsIgnoreCase(name) ? id : super.getHeader(name);
          }
        },
        res);
  }
}
