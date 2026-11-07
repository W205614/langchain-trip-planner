package com.tripplanner.api;

import com.tripplanner.security.AccountAccess;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class PublicAccessConfiguration implements WebMvcConfigurer, HandlerInterceptor {
  private final AccountAccess accounts;
  public PublicAccessConfiguration(AccountAccess accounts) { this.accounts=accounts; }
  @Override public void addInterceptors(InterceptorRegistry registry) {
    registry.addInterceptor(this).addPathPatterns("/api/**");
  }
  @Override public boolean preHandle(HttpServletRequest request,HttpServletResponse response,Object handler) {
    if (!accounts.verificationRequired() || request.getAttribute("user")==null) return true;
    String path=request.getRequestURI(),method=request.getMethod();
    boolean mutation=!java.util.Set.of("GET","HEAD","OPTIONS").contains(method);
    boolean paid=mutation && (path.startsWith("/api/research")
        || path.startsWith("/api/trip/")&&!path.endsWith("/cancel")
        || path.matches("/api/(tasks|history)/[^/]+/(resume|retry|revise-task|revise-day|apply-draft)")
        || path.startsWith("/api/assistant/conversations/")
        || path.equals("/api/knowledge/submissions") || path.startsWith("/api/knowledge/admin/")
        || path.startsWith("/api/community/") || path.equals("/api/trips")
        || path.matches("/api/trips/[0-9]+/shares")
        || path.startsWith("/api/rag/") || path.startsWith("/api/shares"));
    if (paid) accounts.requireVerified(UsersController.uid(request));
    return true;
  }
}
