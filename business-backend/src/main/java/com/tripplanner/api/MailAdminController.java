package com.tripplanner.api;

import com.tripplanner.security.MailDeliveryService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/admin/mail")
public class MailAdminController {
  private final MailDeliveryService mail;
  public MailAdminController(MailDeliveryService mail) { this.mail = mail; }
  @GetMapping public Object list(@RequestParam(defaultValue="") String status) { return mail.list(status); }
  @PostMapping("/{id}/replay") public Object replay(@PathVariable long id, HttpServletRequest req) { return mail.replay(id, UsersController.uid(req), req.getHeader("X-Request-ID")); }
}
