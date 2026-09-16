package com.tripplanner.api;

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

@RestController
public class SharesController {
  private final ShareMapper shares; private final HistoryMapper history;
  private final TransactionTemplate tx; private final JsonMapper json;
  private final SecureRandom random = new SecureRandom();
  public SharesController(ShareMapper shares, HistoryMapper history, TransactionTemplate tx, JsonMapper json) {
    this.shares=shares; this.history=history; this.tx=tx; this.json=json;
  }

  @PostMapping("/api/trips/{recordId}/shares")
  public Object create(HttpServletRequest req, @PathVariable long recordId,
      @RequestBody(required=false) tools.jackson.databind.node.ObjectNode body) {
    long uid=UsersController.uid(req); var record=history.owned(uid,recordId);
    if(record==null) throw new ApiException(404,"行程不存在");
    int days=body==null?7:body.path("expires_days").asInt(7);
    if(days<1||days>90) throw new ApiException(422,"分享有效期必须为1到90天");
    byte[] raw=new byte[32]; random.nextBytes(raw);
    String token=Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    var snapshot=json.createObjectNode();
    for(String field:List.of("city","start_date","end_date","travel_days","transportation","accommodation","title","source"))
      snapshot.set(field,json.valueToTree(record.get(field)));
    snapshot.set("plan",json.readTree(record.get("plan_json").toString()));
    var quality=json.readTree(record.get("quality_json").toString());
    snapshot.put("outcome",quality.path("outcome").asText("unassessed"));
    var row=new HashMap<String,Object>(); row.put("record_id",recordId); row.put("owner_id",uid);
    row.put("token_hash",hash(token)); row.put("snapshot_json",json.writeValueAsString(snapshot));
    row.put("record_version",record.get("version")); row.put("expires_at",Timestamp.from(Instant.now().plus(days,ChronoUnit.DAYS)));
    shares.insert(row);
    return Map.of("success",true,"id",row.get("id"),"token",token,"expires_at",row.get("expires_at"));
  }

  @GetMapping("/api/trips/{recordId}/shares")
  public Object list(HttpServletRequest req,@PathVariable long recordId){
    long uid=UsersController.uid(req); if(history.owned(uid,recordId)==null) throw new ApiException(404,"行程不存在");
    return Map.of("success",true,"data",shares.list(uid,recordId));
  }

  @DeleteMapping("/api/trips/{recordId}/shares/{shareId}")
  public Object revoke(HttpServletRequest req,@PathVariable long recordId,@PathVariable long shareId){
    if(shares.revoke(UsersController.uid(req),recordId,shareId)==0) throw new ApiException(404,"分享不存在或已撤销");
    return Map.of("success",true,"message","分享已撤销");
  }

  @GetMapping("/api/shared-trips/{token}")
  public Object publicTrip(@PathVariable String token){
    var row=active(token); return Map.of("success",true,"data",json.readTree(row.get("snapshot_json").toString()));
  }

  @PostMapping("/api/shared-trips/{token}/copy")
  public Object copy(HttpServletRequest req,@PathVariable String token){
    long uid=UsersController.uid(req); var share=active(token);
    var snapshot=json.readTree(share.get("snapshot_json").toString()); var plan=snapshot.path("plan");
    var record=new HashMap<String,Object>(); record.put("user_id",uid); record.put("title",snapshot.path("title").asText("")+"（副本）");
    record.put("source","copied");
    for(String field:List.of("city","start_date","end_date","transportation","accommodation")) record.put(field,snapshot.path(field).asText(""));
    record.put("travel_days",snapshot.path("travel_days").asInt(1)); record.put("preferences","[]"); record.put("free_text_input","");
    record.put("plan_json",json.writeValueAsString(plan));
    var quality=json.createObjectNode().put("outcome",snapshot.path("outcome").asText("draft"));
    quality.putArray("data_gaps").add("copied_share_requires_reverification");
    record.put("quality_json",json.writeValueAsString(quality)); record.put("last_verified_at",null);
    tx.executeWithoutResult(s->{history.insert(record); history.outbox(((Number)record.get("id")).longValue(),uid,"delete");});
    return Map.of("success",true,"id",record.get("id"),"version",1,"message","已复制为待重新核验的独立行程");
  }

  private Map<String,Object> active(String token){
    if(token==null||!token.matches("[A-Za-z0-9_-]{40,64}")) throw new ApiException(404,"分享不存在或已失效");
    var row=shares.active(hash(token)); if(row==null) throw new ApiException(404,"分享不存在或已失效"); return row;
  }
  private String hash(String value){
    try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
    catch(Exception ex){throw new IllegalStateException(ex);}
  }
}
