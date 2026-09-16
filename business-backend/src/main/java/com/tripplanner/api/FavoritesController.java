package com.tripplanner.api;

import com.tripplanner.domain.AmapGateway;
import com.tripplanner.persistence.FavoriteMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.util.*;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

@RestController
@RequestMapping("/api/favorites")
public class FavoritesController {
  private final FavoriteMapper favorites;
  private final AmapGateway amap;
  private final JsonMapper json;

  public FavoritesController(FavoriteMapper favorites, AmapGateway amap, JsonMapper json) {
    this.favorites = favorites; this.amap = amap; this.json = json;
  }

  @PostMapping
  public Object add(HttpServletRequest req, @RequestBody ObjectNode body) {
    String id = body.path("poi_id").asText("");
    var poi = amap.detail(id);
    var row = new HashMap<String, Object>();
    row.put("user_id", UsersController.uid(req)); row.put("poi_id", id);
    row.put("city", poi.path("city").asText("")); row.put("name", poi.path("name").asText(""));
    row.put("address", poi.path("address").asText(""));
    row.put("longitude", poi.path("location").path("longitude").asDouble());
    row.put("latitude", poi.path("location").path("latitude").asDouble());
    row.put("snapshot_json", json.writeValueAsString(poi));
    favorites.upsert(row);
    return Map.of("success", true, "data", serialize(favorites.get(UsersController.uid(req), id)));
  }

  @GetMapping
  public Object list(HttpServletRequest req, @RequestParam(defaultValue="") String city,
      @RequestParam(defaultValue="1") int page, @RequestParam(defaultValue="20") int page_size) {
    if (page < 1 || page_size < 1 || page_size > 50 || city.length() > 64)
      throw new ApiException(422, "分页或城市参数无效");
    long uid = UsersController.uid(req);
    return Map.of("success", true,
        "data", favorites.list(uid, city.strip(), (page - 1) * page_size, page_size).stream().map(this::serialize).toList(),
        "total", favorites.count(uid, city.strip()), "page", page, "page_size", page_size);
  }

  @DeleteMapping("/{poiId}")
  public Object delete(HttpServletRequest req, @PathVariable String poiId) {
    if (!poiId.matches("[A-Za-z0-9_-]{1,64}")) throw new ApiException(422, "POI ID 无效");
    favorites.delete(UsersController.uid(req), poiId);
    return Map.of("success", true, "message", "已取消收藏");
  }

  private Map<String, Object> serialize(Map<String, Object> row) {
    if (row == null) throw new ApiException(404, "收藏不存在");
    var result = new LinkedHashMap<String, Object>();
    for (String field : List.of("id","provider","poi_id","city","name","address","longitude","latitude","refreshed_at","created_at"))
      result.put(field, row.get(field));
    result.put("poi", json.readTree(row.get("snapshot_json").toString()));
    return result;
  }
}
