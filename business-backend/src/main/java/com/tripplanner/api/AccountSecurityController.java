package com.tripplanner.api;

import com.tripplanner.security.*;
import jakarta.servlet.http.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/auth")
public class AccountSecurityController {
  private final AccountSecurityService security;private final BrowserSession browser;
  public AccountSecurityController(AccountSecurityService security,BrowserSession browser){this.security=security;this.browser=browser;}
  public record Credentials(@NotNull @Size(max=128) String password,@Size(max=6) String totp_code){}
  public record Change(@NotNull @Size(max=128) String current_password,@NotNull @Size(max=128) String new_password,@Size(max=6) String totp_code){}
  public record Recovery(@NotNull @Size(max=64) String username,@NotNull @Size(max=128) String recovery_code,@NotNull @Size(max=128) String new_password){}
  public record Code(@NotNull @Size(max=6) String totp_code){}
  private String requestId(HttpServletRequest req){return req.getHeader("X-Request-ID");}
  @GetMapping("/security") public Object status(HttpServletRequest req,HttpServletResponse res){res.setHeader("Cache-Control","no-store");return security.status(UsersController.uid(req));}
  @PostMapping("/password") public Object password(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Change body){
    var result=security.changePassword(UsersController.uid(req),body.current_password(),body.new_password(),body.totp_code(),requestId(req));browser.clear(res);return result;}
  @PostMapping("/recovery-code") public Object recovery(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Credentials body){
    res.setHeader("Cache-Control","no-store");return security.recoveryCode(UsersController.uid(req),body.password(),body.totp_code(),requestId(req));}
  @PostMapping("/recover") public Object recover(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Recovery body){
    var result=security.recover(body.username(),body.recovery_code(),body.new_password(),requestId(req));browser.clear(res);return result;}
  @PostMapping("/mfa/enroll") public Object enroll(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Credentials body){
    res.setHeader("Cache-Control","no-store");return security.enroll(UsersController.uid(req),body.password(),body.totp_code(),requestId(req));}
  @PostMapping("/mfa/confirm") public Object confirm(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Code body){
    var result=security.confirm(UsersController.uid(req),body.totp_code(),requestId(req));browser.clear(res);return result;}
  @PostMapping("/mfa/disable") public Object disable(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Credentials body){
    var result=security.disable(UsersController.uid(req),body.password(),body.totp_code(),requestId(req));browser.clear(res);return result;}
}
