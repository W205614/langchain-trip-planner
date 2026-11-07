package com.tripplanner.api;

import com.tripplanner.domain.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.Map;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class PublicOperationsController {
  private final PublicOperationsService operations;
  private final UserQuotaService quotas;
  public PublicOperationsController(PublicOperationsService operations,UserQuotaService quotas){this.operations=operations;this.quotas=quotas;}
  public record State(@NotNull String state,@NotNull @Size(min=1,max=300) String reason){}
  public record Feedback(@NotNull String category,@NotNull @Size(min=1,max=160) String subject,
      @NotNull @Size(min=1,max=3000) String message,Long trip_id,@Size(max=36) String task_id,@Size(max=64) String request_id){}
  public record Response(@NotNull String status,@NotNull @Size(max=3000) String reply){}
  @GetMapping("/admin/operations/users") public Object users(@RequestParam(defaultValue="1")int page,@RequestParam(defaultValue="")String query){return operations.users(page,query);}
  @PutMapping("/admin/operations/users/{uid}/state") public Object state(HttpServletRequest req,@PathVariable long uid,@Valid @RequestBody State body){return operations.state(UsersController.uid(req),uid,body.state(),body.reason(),req.getHeader("X-Request-ID"));}
  @GetMapping("/admin/operations/users/{uid}/quotas") public Object quota(@PathVariable long uid){return quotas.summary(uid);}
  @PutMapping("/admin/operations/users/{uid}/quotas") public Object quota(HttpServletRequest req,@PathVariable long uid,@RequestBody Map<String,Integer> body){return operations.updateQuotas(UsersController.uid(req),uid,body,req.getHeader("X-Request-ID"));}
  @GetMapping("/usage/limits") public Object limits(HttpServletRequest req){return quotas.summary(UsersController.uid(req));}
  @PostMapping("/feedback") public Object feedback(HttpServletRequest req,@Valid @RequestBody Feedback body){return operations.createFeedback(UsersController.uid(req),body.category(),body.subject(),body.message(),body.trip_id(),body.task_id(),body.request_id());}
  @GetMapping("/feedback") public Object feedback(HttpServletRequest req,@RequestParam(defaultValue="1")int page){return operations.feedback(UsersController.uid(req),page,false,"");}
  @GetMapping("/admin/operations/feedback") public Object feedback(@RequestParam(defaultValue="1")int page,@RequestParam(defaultValue="")String status){return operations.feedback(0,page,true,status);}
  @PutMapping("/admin/operations/feedback/{id}") public Object reply(HttpServletRequest req,@PathVariable long id,@RequestHeader("If-Match")int version,@Valid @RequestBody Response body){return operations.respond(UsersController.uid(req),id,version,body.status(),body.reply(),req.getHeader("X-Request-ID"));}
}
