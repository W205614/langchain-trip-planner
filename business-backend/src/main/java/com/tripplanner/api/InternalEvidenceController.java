package com.tripplanner.api;

import com.tripplanner.persistence.EvidenceMapper;
import java.util.*;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/internal/v1/evidence")
public class InternalEvidenceController {
  private final EvidenceMapper evidence;
  private final JsonMapper json;

  public InternalEvidenceController(EvidenceMapper evidence, JsonMapper json) {
    this.evidence = evidence;
    this.json = json;
  }

  @PostMapping("/visible")
  public Object visible(@RequestBody ObjectNode input) {
    if (!input.path("candidates").isArray() || input.path("candidates").size() > 100)
      throw new ApiException(422, "候选数量无效");
    var allowed = new ArrayList<Integer>();
    int index = 0;
    long uid = input.path("user_id").asLong(0);
    for (var meta : input.path("candidates")) {
      boolean visible = true;
      if (meta.path("source_type").asText("").equals("multimodal")) {
        var row = evidence.document(meta.path("document_id").asLong(0));
        visible =
            row != null
                && "published".equals(row.get("status"))
                && ((Number) row.get("version")).intValue()
                    == meta.path("document_version").asInt(1);
      }
      if (meta.has("record_id")) {
        var row = evidence.record(meta.path("record_id").asLong(0));
        visible =
            visible
                && row != null
                && ((Number) row.get("user_id")).longValue() == uid
                && ((Number) row.get("version")).intValue() == meta.path("record_version").asInt(1)
                && !json.readTree(row.get("quality_json").toString())
                    .path("outcome")
                    .asText("")
                    .equals("draft");
      }
      if (visible) allowed.add(index);
      index++;
    }
    return Map.of("allowed", allowed);
  }

  @PostMapping("/revision")
  public Object revision() {
    return Map.of("revision", evidence.revision());
  }

  @PostMapping("/snapshot")
  @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
  public Object snapshot() {
    long revision = evidence.revision();
    return Map.of(
        "revision", revision, "documents", evidence.published(), "records", evidence.records());
  }
}
