package com.tripplanner.api;

import com.tripplanner.domain.AccountDataService;
import com.tripplanner.security.BrowserSession;
import jakarta.servlet.http.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.UUID;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api")
public class AccountDataController {
  private final AccountDataService data;
  private final BrowserSession browser;
  public AccountDataController(AccountDataService data,BrowserSession browser){this.data=data;this.browser=browser;}
  public record Erasure(@NotNull @Size(max=128)String password,@Size(max=6)String totp_code,@NotNull String confirmation){}
  @GetMapping("/account/data-jobs") public Object jobs(HttpServletRequest req){return data.jobs(UsersController.uid(req));}
  @PostMapping("/account/exports") @ResponseStatus(HttpStatus.ACCEPTED) public Object export(HttpServletRequest req){return data.requestExport(UsersController.uid(req),req.getHeader("X-Request-ID"));}
  @GetMapping("/account/exports/{id}/download") public Object download(HttpServletRequest req,@PathVariable UUID id){return ResponseEntity.ok().contentType(MediaType.parseMediaType("application/zip"))
      .header("Cache-Control","no-store").header("Content-Disposition","attachment; filename=trip-account-"+id+".zip").body(data.download(UsersController.uid(req),id));}
  @GetMapping("/account/erasure-preview") public Object preview(HttpServletRequest req){return data.preview(UsersController.uid(req));}
  @PostMapping("/account/erasure") @ResponseStatus(HttpStatus.ACCEPTED) public Object erase(HttpServletRequest req,HttpServletResponse res,@Valid @RequestBody Erasure body){
    if(!"DELETE MY ACCOUNT".equals(body.confirmation()))throw new ApiException(422,"请输入DELETE MY ACCOUNT确认注销");
    var result=data.erase(UsersController.uid(req),body.password(),body.totp_code(),req.getHeader("X-Request-ID"));browser.clear(res);return result;
  }
  @GetMapping("/auth/erasure/{id}") public Object erasure(@PathVariable UUID id,@RequestHeader(value="X-Erasure-Receipt",required=false)String receipt,HttpServletResponse res){res.setHeader("Cache-Control","no-store");return data.erasureStatus(id,receipt);}
  @PostMapping("/admin/operations/data-jobs/{id}/replay") public Object replay(HttpServletRequest req,@PathVariable UUID id){return data.replay(UsersController.uid(req),id,req.getHeader("X-Request-ID"));}
  @GetMapping("/admin/operations/data-jobs") public Object adminJobs(){return data.adminJobs();}
}
