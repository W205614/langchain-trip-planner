package com.tripplanner.api;

import com.tripplanner.domain.BusinessAuditService;
import com.tripplanner.domain.BusinessTypes;
import com.tripplanner.domain.TripLedgerService;
import com.tripplanner.domain.ScheduleDependencies;
import com.tripplanner.domain.PlanningInputService;
import com.tripplanner.domain.TripSuggestionService;
import com.tripplanner.domain.UserQuotaService;
import com.tripplanner.persistence.*;
import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.sql.Timestamp;
import java.time.*;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
public class SharesController {
  private final ShareMapper shares; private final HistoryMapper history;
  private final TransactionTemplate tx; private final JsonMapper json;
  private final TripLedgerService ledger; private final BusinessAuditService audit;
  private final ScheduleDependencies schedules;
  private final UserQuotaService userQuotas;
  private final TripSuggestionService suggestions;
  private final SecureRandom random = new SecureRandom();
  public SharesController(ShareMapper shares, HistoryMapper history, TransactionTemplate tx, JsonMapper json,
      TripLedgerService ledger, BusinessAuditService audit, ScheduleDependencies schedules, UserQuotaService userQuotas,
      TripSuggestionService suggestions) {
    this.shares=shares; this.history=history; this.tx=tx; this.json=json; this.ledger=ledger; this.audit=audit;
    this.schedules=schedules;
    this.userQuotas=userQuotas;
    this.suggestions=suggestions;
  }

  @PostMapping("/api/trips/{recordId}/shares")
  public Object create(HttpServletRequest req, @PathVariable long recordId,
      @RequestBody(required=false) ObjectNode body) {
    long uid=UsersController.uid(req); var record=history.owned(uid,recordId);
    if(record==null) throw new ApiException(404,"行程不存在");
    if(!json.readTree(record.get("quality_json").toString()).path("assistant_proposal_status").asText("").isBlank())
      throw new ApiException(409,"助手方案不能直接分享，请先确认原行程");
    int days=body==null?7:body.path("expires_days").asInt(7);
    if(days<1||days>90) throw new ApiException(422,"分享有效期必须为1到90天");
    if (body!=null && body.has("allow_suggestions") && !body.path("allow_suggestions").isBoolean())
      throw new ApiException(422,"建议权限必须为布尔值");
    boolean allowSuggestions=body!=null && body.path("allow_suggestions").asBoolean();
    byte[] raw=new byte[32]; random.nextBytes(raw);
    String token=Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    var snapshot=json.createObjectNode();
    for(String field:List.of("city","start_date","end_date","travel_days","transportation","accommodation","title","source"))
      snapshot.set(field,json.valueToTree(record.get(field)));
    var plan=(ObjectNode)json.readTree(record.get("plan_json").toString());
    if (allowSuggestions) PlanningInputService.stableIds(plan);
    snapshot.set("plan",plan);
    var quality=json.readTree(record.get("quality_json").toString());
    snapshot.put("outcome",quality.path("outcome").asText("unassessed"));
    var publicSnapshot=PublicTripSnapshot.project(json,snapshot);
    if (allowSuggestions) publicSnapshot.set("_suggestion_targets",suggestions.snapshotTargets(plan,publicSnapshot.path("plan")));
    var row=new HashMap<String,Object>(); row.put("record_id",recordId); row.put("owner_id",uid);
    row.put("token_hash",hash(token)); row.put("snapshot_json",json.writeValueAsString(publicSnapshot));
    row.put("allow_suggestions",allowSuggestions);
    row.put("record_version",record.get("version")); row.put("expires_at",Timestamp.from(Instant.now().plus(days,ChronoUnit.DAYS)));
    tx.executeWithoutResult(s->{
      userQuotas.lockActive(uid);
      if (history.owned(uid, recordId) == null) throw new ApiException(404, "行程不存在");
      shares.insert(row);
      audit.success(uid,"share.create","trip_share",row.get("id"),((Number)record.get("version")).intValue(),
          req.getHeader("X-Request-ID"),Map.of("record_id",recordId));
    });
    return Map.of("success",true,"id",row.get("id"),"token",token,"expires_at",row.get("expires_at"));
  }

  @GetMapping("/api/trips/{recordId}/shares")
  public Object list(HttpServletRequest req,@PathVariable long recordId){
    long uid=UsersController.uid(req); if(history.owned(uid,recordId)==null) throw new ApiException(404,"行程不存在");
    return Map.of("success",true,"data",shares.list(uid,recordId));
  }

  @DeleteMapping("/api/trips/{recordId}/shares/{shareId}")
  public Object revoke(HttpServletRequest req,@PathVariable long recordId,@PathVariable long shareId){
    long uid=UsersController.uid(req);
    tx.executeWithoutResult(s->{
      if(shares.revoke(uid,recordId,shareId)==0) throw new ApiException(404,"分享不存在或已撤销");
      audit.success(uid,"share.revoke","trip_share",shareId,null,req.getHeader("X-Request-ID"),Map.of("record_id",recordId));
    });
    return Map.of("success",true,"message","分享已撤销");
  }

  @GetMapping("/api/shared-trips/{token}")
  public Object publicTrip(@PathVariable String token){
    var row=active(token);
    var snapshot=json.readTree(row.get("snapshot_json").toString());
    var result=json.createObjectNode().put("success",true);
    result.set("data",PublicTripSnapshot.project(json,snapshot));
    if (Boolean.TRUE.equals(row.get("allow_suggestions"))) {
      result.put("allow_suggestions",true);
      var targets=result.putArray("suggestion_targets");
      for (var target:snapshot.path("_suggestion_targets")) {
        var summary=targets.addObject();
        for (String field:List.of("day_index","item_index","title","old_start","old_end")) summary.set(field,target.path(field));
      }
    }
    return result;
  }

  @PostMapping("/api/shared-trips/{token}/suggestions")
  public Object suggest(HttpServletRequest req,@PathVariable String token,@RequestBody ObjectNode body) {
    return suggestions.submit(UsersController.uid(req),tokenHash(token),body,req.getHeader("X-Request-ID"));
  }

  @GetMapping("/api/trips/{recordId}/suggestions")
  public Object suggestions(HttpServletRequest req,@PathVariable long recordId,@RequestParam(defaultValue="1") int page) {
    return suggestions.list(UsersController.uid(req),recordId,page);
  }

  @PostMapping("/api/trips/{recordId}/suggestions/{suggestionId}/decision")
  public Object decideSuggestion(HttpServletRequest req,@PathVariable long recordId,
      @PathVariable long suggestionId,@RequestBody ObjectNode body) {
    return suggestions.decide(UsersController.uid(req),recordId,suggestionId,body,req.getHeader("X-Request-ID"));
  }

  @PostMapping("/api/shared-trips/{token}/copy")
  public Object copy(HttpServletRequest req,@PathVariable String token){
    long uid=UsersController.uid(req); var share=active(token);
    var snapshot=PublicTripSnapshot.project(json, json.readTree(share.get("snapshot_json").toString()));
    var plan=snapshot.path("plan");
    var record=new HashMap<String,Object>(); record.put("user_id",uid); record.put("title",snapshot.path("title").asText("")+"（副本）");
    record.put("source",BusinessTypes.TripSource.COPIED.wire());
    for(String field:List.of("city","start_date","end_date","transportation","accommodation")) record.put(field,snapshot.path(field).asText(""));
    record.put("travel_days",snapshot.path("travel_days").asInt(1)); record.put("preferences","[]"); record.put("free_text_input","");
    var quality=json.createObjectNode().put("outcome","draft");
    quality.putArray("data_gaps").add("copied_share_requires_reverification");
    record.put("plan_json",json.writeValueAsString(schedules.invalidateCopy(plan,quality)));
    record.put("quality_json",json.writeValueAsString(quality)); record.put("last_verified_at",null);
    tx.executeWithoutResult(s->{
      history.insert(record);
      long copiedId=((Number)record.get("id")).longValue();
      history.outbox(copiedId,uid,"delete");
      ledger.capture(uid,copiedId,"copied_create",req.getHeader("X-Request-ID"));
    });
    return Map.of("success",true,"id",record.get("id"),"version",1,"message","已复制为待重新核验的独立行程");
  }

  private Map<String,Object> active(String token){
    var row=shares.active(tokenHash(token)); if(row==null) throw new ApiException(404,"分享不存在或已失效"); return row;
  }
  private String tokenHash(String token) {
    if (token==null || !token.matches("[A-Za-z0-9_-]{40,64}")) throw new ApiException(404,"分享不存在或已失效");
    return hash(token);
  }
  private String hash(String value){
    try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
    catch(Exception ex){throw new IllegalStateException(ex);}
  }
}
