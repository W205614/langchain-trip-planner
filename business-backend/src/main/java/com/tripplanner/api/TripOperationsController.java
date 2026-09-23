package com.tripplanner.api;

import com.tripplanner.domain.TripOperationsService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
public class TripOperationsController {
  private final TripOperationsService operations;

  public TripOperationsController(TripOperationsService operations) { this.operations=operations; }
  private long user(HttpServletRequest req) { return UsersController.uid(req); }
  private String requestId(HttpServletRequest req) { return req.getHeader("X-Request-ID"); }

  @PostMapping("/api/trips/{id}/checks")
  public Object check(HttpServletRequest req,@PathVariable long id,@RequestHeader("If-Match") int version,
      @RequestParam(value="day_index",required=false) Integer dayIndex) {
    return operations.runCheck(user(req),id,version,dayIndex);
  }
  @GetMapping("/api/trips/{id}/checks/latest")
  public Object checks(HttpServletRequest req,@PathVariable long id,
      @RequestParam(value="day_index",required=false) Integer dayIndex) {
    return operations.checks(user(req),id,dayIndex);
  }
  @PostMapping("/api/trips/{id}/risks/{riskId}/acknowledge")
  public Object acknowledge(HttpServletRequest req,@PathVariable long id,@PathVariable long riskId,
      @RequestHeader("If-Match") int version) {
    return operations.acknowledge(user(req),id,riskId,version);
  }

  @GetMapping("/api/trips/{id}/workspace")
  public Object workspace(HttpServletRequest req,@PathVariable long id) { return operations.workspace(user(req),id); }
  @PutMapping("/api/trips/{id}/workspace/day-order")
  public Object reorder(HttpServletRequest req,@PathVariable long id,@RequestHeader("If-Match") int version,
      @RequestBody ObjectNode body) {
    return operations.reorder(user(req),id,version,body,requestId(req));
  }
  @PostMapping("/api/trips/{id}/members")
  public Object invite(HttpServletRequest req,@PathVariable long id,@RequestBody ObjectNode body) {
    return operations.invite(user(req),id,body,requestId(req));
  }
  @GetMapping("/api/trips/{id}/members")
  public Object members(HttpServletRequest req,@PathVariable long id) { return operations.members(user(req),id); }
  @GetMapping("/api/trips/invitations")
  public Object invitations(HttpServletRequest req) { return operations.invitations(user(req)); }
  @PostMapping("/api/trips/{id}/members/accept")
  public Object accept(HttpServletRequest req,@PathVariable long id) {
    return operations.accept(user(req),id,requestId(req));
  }
  @DeleteMapping("/api/trips/{id}/members/{member}")
  public Object remove(HttpServletRequest req,@PathVariable long id,@PathVariable long member) {
    return operations.removeMember(user(req),id,member,requestId(req));
  }
  @DeleteMapping("/api/trips/{id}/members/me")
  public Object leave(HttpServletRequest req,@PathVariable long id) {
    return operations.removeMember(user(req),id,user(req),requestId(req));
  }

  @GetMapping("/api/trips/{id}/commitments")
  public Object commitments(HttpServletRequest req,@PathVariable long id) { return operations.commitments(user(req),id); }
  @PostMapping("/api/trips/{id}/commitments")
  public Object addCommitment(HttpServletRequest req,@PathVariable long id,@RequestBody ObjectNode body,
      @RequestHeader("Idempotency-Key") String key) {
    return operations.addCommitment(user(req),id,body,key,requestId(req));
  }
  @PutMapping("/api/trips/{id}/commitments/{commitment}")
  public Object updateCommitment(HttpServletRequest req,@PathVariable long id,@PathVariable long commitment,
      @RequestHeader("If-Match") int version,@RequestBody ObjectNode body) {
    return operations.updateCommitment(user(req),id,commitment,version,body,requestId(req));
  }
  @GetMapping("/api/trips/{id}/expenses")
  public Object expenses(HttpServletRequest req,@PathVariable long id) { return operations.expenses(user(req),id); }
  @PostMapping("/api/trips/{id}/expenses")
  public Object addExpense(HttpServletRequest req,@PathVariable long id,@RequestBody ObjectNode body,
      @RequestHeader("Idempotency-Key") String key) {
    return operations.addExpense(user(req),id,body,key,requestId(req));
  }
  @PostMapping("/api/trips/{id}/expenses/{expense}/void")
  public Object voidExpense(HttpServletRequest req,@PathVariable long id,@PathVariable long expense,
      @RequestBody ObjectNode body) {
    return operations.voidExpense(user(req),id,expense,body,requestId(req));
  }

  @GetMapping("/api/notifications")
  public Object notifications(HttpServletRequest req) { return operations.notifications(user(req)); }
  @PostMapping("/api/notifications/{id}/read")
  public Object read(HttpServletRequest req,@PathVariable long id) { return operations.readNotification(user(req),id); }
  @GetMapping("/api/usage/summary")
  public Object usage(HttpServletRequest req) { return operations.usage(user(req)); }
  @PutMapping("/api/usage/policy")
  public Object usagePolicy(HttpServletRequest req,@RequestBody ObjectNode body) {
    return operations.usagePolicy(user(req),body,requestId(req));
  }
}
