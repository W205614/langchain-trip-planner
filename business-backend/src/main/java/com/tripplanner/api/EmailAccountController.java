package com.tripplanner.api;

import com.tripplanner.security.BrowserSession;
import com.tripplanner.security.EmailAccountService;
import jakarta.servlet.http.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import org.springframework.web.bind.annotation.*;

@RestController
public class EmailAccountController {
  private final EmailAccountService service;
  private final BrowserSession browser;
  public EmailAccountController(EmailAccountService service, BrowserSession browser) { this.service = service; this.browser = browser; }
  public record Binding(@NotBlank @Email @Size(max=254) String email, @NotNull @Size(max=128) String password, @Size(max=6) String totp_code) {}
  public record Token(@NotBlank @Size(max=128) String token) {}
  public record ResetRequest(@NotBlank @Email @Size(max=254) String email) {}
  public record Reset(@NotBlank @Size(max=128) String token, @NotNull @Size(max=128) String new_password, @Size(max=6) String totp_code) {}
  public record Preferences(@NotNull Boolean email_enabled) {}
  @GetMapping("/api/auth/options") public Object options() { return service.options(); }
  @GetMapping("/api/account/email") public Object status(HttpServletRequest req) { return service.status(UsersController.uid(req)); }
  @PostMapping("/api/account/email") public Object bind(HttpServletRequest req, @Valid @RequestBody Binding body) {
    return service.bind(UsersController.uid(req), body.email(), body.password(), body.totp_code(), req.getHeader("X-Request-ID"));
  }
  @PostMapping("/api/account/email/resend") public Object resend(HttpServletRequest req) { return service.resend(UsersController.uid(req)); }
  @PostMapping("/api/auth/email/confirm") public Object confirm(HttpServletRequest req, @Valid @RequestBody Token body) {
    return service.confirm(body.token(), req.getHeader("X-Request-ID"));
  }
  @PostMapping("/api/auth/email/request-reset") public Object request(@Valid @RequestBody ResetRequest body) { return service.requestReset(body.email()); }
  @PostMapping("/api/auth/email/reset") public Object reset(HttpServletRequest req, HttpServletResponse res, @Valid @RequestBody Reset body) {
    var result = service.reset(body.token(), body.new_password(), body.totp_code(), req.getHeader("X-Request-ID")); browser.clear(res); return result;
  }
  @GetMapping("/api/account/notification-preferences") public Object preferences(HttpServletRequest req) { return service.preferences(UsersController.uid(req)); }
  @PutMapping("/api/account/notification-preferences") public Object preferences(HttpServletRequest req, @Valid @RequestBody Preferences body) { return service.preferences(UsersController.uid(req), body.email_enabled()); }
}
