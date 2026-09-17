package com.tripplanner.api;

import com.tripplanner.persistence.UserMapper;
import com.tripplanner.domain.BusinessAuditService;
import com.tripplanner.security.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.*;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.json.JsonMapper;

@RestController
@RequestMapping("/api")
public class UsersController {
  private final UserMapper users;
  private final TokenService tokens;
  private final JsonMapper json;
  private final BusinessAuditService audit;

  public UsersController(UserMapper users, TokenService tokens, JsonMapper json, BusinessAuditService audit) {
    this.users = users;
    this.tokens = tokens;
    this.json = json;
    this.audit = audit;
  }

  public record Registration(
      @NotNull @Size(min = 3, max = 32) String username,
      @NotNull @Size(min = 6, max = 64) String password) {}

  public record Login(@NotNull String username, @NotNull String password) {}

  public record Preferences(
      @Size(max = 12) List<String> preferences,
      @NotNull @Size(min = 1, max = 32) String transportation,
      @NotNull @Size(min = 1, max = 32) String accommodation) {}

  @SuppressWarnings("unchecked")
  public static Map<String, Object> user(HttpServletRequest req) {
    var user = (Map<String, Object>) req.getAttribute("user");
    if (user == null) throw new ApiException(401, "未登录或登录已过期");
    return user;
  }

  public static long uid(HttpServletRequest req) {
    return ((Number) user(req).get("id")).longValue();
  }

  private Map<String, Object> token(Map<String, Object> user) {
    return Map.of(
        "access_token",
        tokens.issue(user),
        "token_type",
        "bearer",
        "username",
        user.get("username"),
        "is_admin",
        user.get("is_admin"));
  }

  @PostMapping("/auth/register")
  public Object register(@Valid @RequestBody Registration input) {
    var row = new HashMap<String, Object>();
    row.put("username", input.username());
    row.put("hashed_password", Passwords.hash(input.password()));
    try {
      users.insert(row);
    } catch (DuplicateKeyException ex) {
      throw new ApiException(409, "用户名已存在");
    }
    return token(users.byId(((Number) row.get("id")).longValue()));
  }

  @PostMapping("/auth/login")
  public Object login(@Valid @RequestBody Login input) {
    var row = users.byName(input.username());
    if (row == null || !Passwords.verify(input.password(), row.get("hashed_password").toString()))
      throw new ApiException(401, "用户名或密码错误");
    return token(row);
  }

  @PostMapping("/auth/logout")
  @org.springframework.transaction.annotation.Transactional
  public Object logout(HttpServletRequest req) {
    long uid = uid(req);
    users.revoke(uid);
    audit.success(uid, "auth.logout_all", "user", uid, null, req.getHeader("X-Request-ID"));
    return Map.of("success", true, "message", "该账号的所有旧登录凭证已失效");
  }

  @GetMapping("/auth/me")
  public Object me(HttpServletRequest req) {
    var row = new LinkedHashMap<>(user(req));
    row.remove("hashed_password");
    row.remove("token_version");
    return row;
  }

  @GetMapping("/preferences/me")
  public Object preferences(HttpServletRequest req) {
    var row = users.preferences(uid(req));
    if (row == null)
      return Map.of(
          "success",
          true,
          "data",
          Map.of(
              "saved",
              false,
              "preferences",
              List.of(),
              "transportation",
              "公共交通",
              "accommodation",
              "经济型酒店"));
    return Map.of(
        "success",
        true,
        "data",
        Map.of(
            "saved",
            true,
            "preferences",
            json.readTree(row.get("preferences").toString()),
            "transportation",
            row.get("transportation"),
            "accommodation",
            row.get("accommodation"),
            "updated_at",
            row.get("updated_at").toString()));
  }

  @PutMapping("/preferences/me")
  public Object save(
      HttpServletRequest req, @RequestBody tools.jackson.databind.node.ObjectNode body) {
    if (!body.has("transportation")) body.put("transportation", "公共交通");
    if (!body.has("accommodation")) body.put("accommodation", "经济型酒店");
    com.tripplanner.domain.TripRequests.text(body, "transportation", 1, 32);
    com.tripplanner.domain.TripRequests.text(body, "accommodation", 1, 32);
    if (!body.has("preferences")) body.putArray("preferences");
    if (!body.path("preferences").isArray()
        || body.path("preferences").size() > 12
        || body.path("preferences").valueStream().anyMatch(p -> !p.isString()))
      throw new ApiException(422, "偏好参数无效");
    var input =
        new Preferences(
            body.path("preferences").valueStream().map(p -> p.asText("")).toList(),
            body.path("transportation").asText(""),
            body.path("accommodation").asText(""));
    var tags =
        input.preferences() == null
            ? List.<String>of()
            : input.preferences().stream()
                .filter(Objects::nonNull)
                .map(String::strip)
                .filter(s -> !s.isEmpty())
                .distinct()
                .toList();
    users.savePreferences(
        Map.of(
            "user_id",
            uid(req),
            "preferences",
            json.writeValueAsString(tags),
            "transportation",
            input.transportation().strip(),
            "accommodation",
            input.accommodation().strip()));
    return preferences(req);
  }

  @DeleteMapping("/preferences/me")
  public Object delete(HttpServletRequest req) {
    users.deletePreferences(uid(req));
    return Map.of("success", true, "message", "已删除保存的旅行偏好");
  }
}
