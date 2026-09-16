package com.tripplanner.api;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.domain.AmapGateway;
import java.util.Map;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api")
public class CapabilitiesController {
  private final AgentClient agent;
  private final AmapGateway amap;

  public CapabilitiesController(AgentClient agent, AmapGateway amap) {
    this.agent = agent;
    this.amap = amap;
  }

  private void photoInput(String name, String poiId, String city) {
    if (name.isBlank()
        || name.length() > 100
        || city.length() > 32
        || !poiId.matches("[A-Za-z0-9_-]{0,64}")) throw new ApiException(422, "图片参数无效");
  }

  @GetMapping("/poi/photo")
  public Object photo(@RequestParam String name) {
    photoInput(name, "", "");
    return Map.of("success", true, "data", Map.of("name", name,
        "photo_url", "/api/poi/photo/image?name=" + java.net.URLEncoder.encode(name, java.nio.charset.StandardCharsets.UTF_8)));
  }

  @GetMapping("/poi/photo/image")
  public org.springframework.http.ResponseEntity<byte[]> photoImage(
      @RequestParam String name,
      @RequestParam(defaultValue = "") String poi_id,
      @RequestParam(defaultValue = "") String city) {
    photoInput(name, poi_id, city);
    var result = amap.image(name, poi_id, city);
    String media = result.mediaType();
    return org.springframework.http.ResponseEntity.ok()
        .header("Content-Type", media)
        .header(
            "Cache-Control", media.equals("image/svg+xml") ? "no-store" : "public, max-age=3600")
        .header("X-Content-Type-Options", "nosniff")
        .header("X-Trip-Image-Source", result.placeholder() ? "placeholder" : "amap-verified")
        .body(result.body());
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
    return Map.of("success", true, "message", "查询完成", "data", amap.search(keywords, city, citylimit));
  }

  @GetMapping("/poi/search")
  public Object search(
      @RequestParam String keywords, @RequestParam(defaultValue = "北京") String city) {
    return poi(keywords, city, true);
  }

  @GetMapping("/poi/detail/{id}")
  public Object detail(@PathVariable String id) {
    return Map.of("success", true, "message", "查询完成", "data", amap.detail(id));
  }

  @GetMapping("/map/weather")
  public Object weather(@RequestParam String city) {
    return Map.of("success", true, "message", "查询完成", "data", amap.weather(city));
  }

  @PostMapping("/map/route")
  public Object route(@RequestBody ObjectNode body) {
    return Map.of("success", true, "message", "查询完成", "data", amap.route(body));
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
    return Map.of("status", amap.configured() ? "configured" : "not_configured",
        "service", "java-amap-rest", "connectivity_checked", false);
  }

  @GetMapping("/trip/health")
  public Object tripHealth() {
    return Map.of("status", agent.available() ? "healthy" : "unavailable", "framework", "LangGraph", "agent_name", "旅行规划工作流");
  }

  @GetMapping("/capabilities")
  public Object capabilities() {
    boolean ai = agent.available();
    String rag = ai ? "unknown" : "waiting_for_agent";
    String vision = ai ? "unknown" : "waiting_for_agent";
    if (ai) {
      try {
        var status = agent.post("/capabilities/status", Map.of());
        rag = status.path("rag").asText("unknown");
        vision = status.path("vision").asText("unknown");
      } catch (Exception ignored) {
        ai = false; rag = "waiting_for_agent"; vision = "waiting_for_agent";
      }
    }
    return Map.of(
        "traditional", "available",
        "map", amap.configured() ? "configured" : "not_configured",
        "agent", ai ? "available" : "unavailable",
        "rag", rag,
        "vision", vision);
  }
}
