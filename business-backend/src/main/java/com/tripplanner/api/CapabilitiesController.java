package com.tripplanner.api;

import com.tripplanner.agent.AgentClient;
import java.util.Map;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api")
public class CapabilitiesController {
  private final AgentClient agent;

  public CapabilitiesController(AgentClient agent) {
    this.agent = agent;
  }

  private void photoInput(String name, String poiId, String city) {
    if (name.isBlank()
        || name.length() > 100
        || city.length() > 32
        || !poiId.matches("[A-Za-z0-9]{0,64}")) throw new ApiException(422, "图片参数无效");
  }

  @GetMapping("/poi/photo")
  public Object photo(@RequestParam String name) {
    photoInput(name, "", "");
    return agent.post("/capabilities/photo", Map.of("name", name));
  }

  @GetMapping("/poi/photo/image")
  public org.springframework.http.ResponseEntity<byte[]> photoImage(
      @RequestParam String name,
      @RequestParam(defaultValue = "") String poi_id,
      @RequestParam(defaultValue = "") String city) {
    photoInput(name, poi_id, city);
    var result =
        agent.post(
            "/capabilities/photo-image", Map.of("name", name, "poi_id", poi_id, "city", city));
    String media = result.path("content_type").asText("");
    if (!java.util.Set.of("image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml")
        .contains(media)) throw new ApiException(503, "图片格式不可用");
    return org.springframework.http.ResponseEntity.ok()
        .header("Content-Type", media)
        .header(
            "Cache-Control", media.equals("image/svg+xml") ? "no-store" : "public, max-age=3600")
        .header("X-Content-Type-Options", "nosniff")
        .body(java.util.Base64.getDecoder().decode(result.path("content").asText("")));
  }

  @GetMapping("/trip/eval-policy")
  public Object evalPolicy() {
    return agent.post("/capabilities/eval-policy", Map.of());
  }

  @GetMapping("/map/poi")
  public Object poi(
      @RequestParam String keywords,
      @RequestParam String city,
      @RequestParam(defaultValue = "true") boolean citylimit) {
    return agent.post(
        "/capabilities/poi", Map.of("keywords", keywords, "city", city, "citylimit", citylimit));
  }

  @GetMapping("/poi/search")
  public Object search(
      @RequestParam String keywords, @RequestParam(defaultValue = "北京") String city) {
    return poi(keywords, city, true);
  }

  @GetMapping("/poi/detail/{id}")
  public Object detail(@PathVariable String id) {
    return agent.post("/capabilities/detail", Map.of("poi_id", id));
  }

  @GetMapping("/map/weather")
  public Object weather(@RequestParam String city) {
    return agent.post("/capabilities/weather", Map.of("city", city));
  }

  @PostMapping("/map/route")
  public Object route(@RequestBody ObjectNode body) {
    return agent.post("/capabilities/route", body);
  }

  @PostMapping("/research")
  public Object research(@RequestBody ObjectNode body) {
    com.tripplanner.domain.TripRequests.text(body, "city", 1, 64);
    com.tripplanner.domain.TripRequests.text(body, "query", 2, 300);
    return agent.post("/capabilities/research", body);
  }

  @GetMapping("/rag/status")
  public Object rag() {
    return agent.post("/capabilities/rag-status", Map.of());
  }

  @GetMapping("/map/health")
  public Object mapHealth() {
    return agent.post("/capabilities/map-health", Map.of());
  }

  @GetMapping("/trip/health")
  public Object tripHealth() {
    return Map.of("status", "healthy", "framework", "LangGraph", "agent_name", "旅行规划工作流");
  }
}
