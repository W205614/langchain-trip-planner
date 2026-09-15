package com.tripplanner.security;

import com.tripplanner.api.ApiException;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.*;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.filter.OncePerRequestFilter;

@Configuration
public class SecurityConfiguration {
  @Bean
  org.springframework.security.core.userdetails.UserDetailsService jwtOnlyUsers() {
    return username -> {
      throw new org.springframework.security.core.userdetails.UsernameNotFoundException(
          "JWT authentication only");
    };
  }

  @Bean
  org.springframework.web.cors.CorsConfigurationSource cors(
      @Value("${CORS_ORIGINS:http://localhost:5173,http://127.0.0.1:5173}") String origins) {
    var config = new org.springframework.web.cors.CorsConfiguration();
    config.setAllowedOrigins(
        java.util.Arrays.stream(origins.split(","))
            .map(String::strip)
            .filter(s -> !s.isEmpty())
            .toList());
    config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
    config.setAllowedHeaders(
        List.of(
            "Authorization",
            "Content-Type",
            "If-Match",
            "Idempotency-Key",
            "X-Request-ID",
            "Last-Event-ID"));
    config.setExposedHeaders(List.of("X-Request-ID"));
    var source = new org.springframework.web.cors.UrlBasedCorsConfigurationSource();
    source.registerCorsConfiguration("/api/**", config);
    return source;
  }

  @Bean
  SecurityFilterChain security(
      HttpSecurity http, TokenService tokens, @Value("${trip.internal-key}") String internalKey)
      throws Exception {
    if (internalKey.getBytes(StandardCharsets.UTF_8).length < 32)
      throw new IllegalArgumentException("INTERNAL_SERVICE_KEY requires at least 32 bytes");
    var filter =
        new OncePerRequestFilter() {
          @Override
          protected void doFilterInternal(
              HttpServletRequest req, HttpServletResponse res, FilterChain chain)
              throws ServletException, IOException {
            try {
              if (req.getRequestURI().startsWith("/internal/")) {
                String supplied = req.getHeader("X-Service-Key");
                if (supplied == null
                    || !MessageDigest.isEqual(
                        internalKey.getBytes(StandardCharsets.UTF_8),
                        supplied.getBytes(StandardCharsets.UTF_8)))
                  throw new ApiException(401, "内部服务鉴权失败");
                SecurityContextHolder.getContext()
                    .setAuthentication(
                        new UsernamePasswordAuthenticationToken(
                            "agent", null, List.of(new SimpleGrantedAuthority("ROLE_SERVICE"))));
              } else {
                String bearer = req.getHeader("Authorization");
                if (bearer != null && bearer.startsWith("Bearer ")) {
                  var user = tokens.verify(bearer.substring(7));
                  req.setAttribute("user", user);
                  SecurityContextHolder.getContext()
                      .setAuthentication(
                          new UsernamePasswordAuthenticationToken(
                              user.get("id"),
                              null,
                              List.of(
                                  new SimpleGrantedAuthority(
                                      Boolean.TRUE.equals(user.get("is_admin"))
                                          ? "ROLE_ADMIN"
                                          : "ROLE_USER"))));
                }
              }
              chain.doFilter(req, res);
            } catch (ApiException ex) {
              res.setStatus(ex.status);
              res.setContentType("application/json;charset=UTF-8");
              res.getWriter().write("{\"success\":false,\"detail\":\"未登录或登录已过期\"}");
            }
          }
        };
    return http.cors(org.springframework.security.config.Customizer.withDefaults())
        .csrf(c -> c.disable())
        .sessionManagement(c -> c.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .formLogin(c -> c.disable())
        .httpBasic(c -> c.disable())
        .authorizeHttpRequests(
            c ->
                c.dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR)
                    .permitAll()
                    .requestMatchers(
                        "/health",
                        "/healthz",
                        "/readyz",
                        "/metrics",
                        "/api/auth/login",
                        "/api/auth/register",
                        "/api/validation/fixture")
                    .permitAll()
                    .requestMatchers(
                        "/api/map/**", "/api/poi/**", "/api/rag/status", "/api/trip/health")
                    .permitAll()
                    .requestMatchers("/internal/**")
                    .hasRole("SERVICE")
                    .requestMatchers(
                        "/api/knowledge/admin/**", "/api/rag/**", "/api/trip/eval-policy")
                    .hasRole("ADMIN")
                    .anyRequest()
                    .authenticated())
        .exceptionHandling(
            c ->
                c.authenticationEntryPoint(
                    (req, res, e) -> {
                      res.setStatus(401);
                      res.setContentType("application/json");
                      res.getWriter().write("{\"detail\":\"Unauthorized\"}");
                    }))
        .addFilterBefore(filter, UsernamePasswordAuthenticationFilter.class)
        .build();
  }
}
