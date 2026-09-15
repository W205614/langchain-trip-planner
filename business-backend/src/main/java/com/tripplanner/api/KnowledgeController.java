package com.tripplanner.api;

import com.tripplanner.domain.KnowledgeService;
import com.tripplanner.persistence.KnowledgeMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
public class KnowledgeController {
  private final KnowledgeService service;
  private final KnowledgeMapper documents;
  private final JsonMapper json;

  public KnowledgeController(KnowledgeService service, KnowledgeMapper documents, JsonMapper json) {
    this.service = service;
    this.documents = documents;
    this.json = json;
  }

  @PostMapping("/api/knowledge/submissions")
  @ResponseStatus(HttpStatus.CREATED)
  public Object upload(
      HttpServletRequest req,
      @RequestParam String city,
      @RequestParam String title,
      @RequestParam MultipartFile file) {
    return Map.of(
        "success",
        true,
        "message",
        "资料已提交，管理员审核后会公开到知识库",
        "data",
        service.upload(UsersController.uid(req), city, title, file));
  }

  @GetMapping("/api/knowledge/submissions/mine")
  public Object mine(HttpServletRequest req) {
    return Map.of(
        "success",
        true,
        "data",
        documents.mine(UsersController.uid(req)).stream().map(service::serialize).toList());
  }

  @GetMapping("/api/knowledge/admin/submissions")
  public Object list(@RequestParam(defaultValue = "") String status_filter) {
    return Map.of(
        "success",
        true,
        "data",
        documents.list(status_filter).stream().map(service::serialize).toList());
  }

  @GetMapping("/api/knowledge/admin/submissions/{id}/preview")
  public Object preview(@PathVariable long id) {
    var d = service.get(id);
    if (d.get("status").equals("deleted")) throw new ApiException(404, "资料不存在");
    var result = service.serialize(d);
    result.put("pages", json.readTree(d.get("extracted_pages_json").toString()));
    result.put(
        "legacy_review",
        d.get("status").equals("published") && d.get("extracted_pages_json").equals("[]"));
    return Map.of("data", result);
  }

  @GetMapping("/api/knowledge/admin/submissions/{id}/original")
  public Object original(@PathVariable long id) {
    return file(id, null);
  }

  @GetMapping("/internal/v1/documents/{id}/{version}/original")
  public Object internal(@PathVariable long id, @PathVariable int version) {
    return file(id, version);
  }

  private Object file(long id, Integer version) {
    var d = service.get(id);
    if (d.get("status").equals("deleted")
        || (version != null && ((Number) d.get("version")).intValue() != version))
      throw new ApiException(409, "资料已变更");
    return ResponseEntity.ok()
        .contentType(MediaType.parseMediaType(d.get("media_type").toString()))
        .header("Cache-Control", "no-store")
        .header("X-Content-Type-Options", "nosniff")
        .body(new FileSystemResource(service.file(d)));
  }

  @PostMapping("/api/knowledge/admin/submissions/{id}/{action}")
  public Object review(
      HttpServletRequest req,
      @PathVariable long id,
      @PathVariable String action,
      @RequestBody ObjectNode body) {
    if (!java.util.Set.of("approve", "reject", "publish").contains(action))
      throw new ApiException(404, "未知操作");
    return service.review(id, UsersController.uid(req), action, body);
  }

  @PutMapping("/api/knowledge/admin/submissions/{id}/extraction")
  public Object edit(HttpServletRequest req, @PathVariable long id, @RequestBody ObjectNode body) {
    return service.review(id, UsersController.uid(req), "extraction", body);
  }

  @DeleteMapping("/api/knowledge/admin/submissions/{id}")
  public Object delete(HttpServletRequest req, @PathVariable long id) {
    return service.review(id, UsersController.uid(req), "delete", json.createObjectNode());
  }
}
