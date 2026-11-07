package com.tripplanner.security;

import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.UserMapper;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/** Account state is checked again at business writes, not only at token verification. */
@Service
public class AccountAccess {
  private final UserMapper users;
  private final boolean verificationRequired;

  public AccountAccess(UserMapper users,
      @Value("${PUBLIC_REGISTRATION_MODE:legacy}") String mode) {
    if (!Set.of("legacy", "email_verified").contains(mode))
      throw new IllegalArgumentException("PUBLIC_REGISTRATION_MODE must be legacy or email_verified");
    this.users = users;
    this.verificationRequired = mode.equals("email_verified");
  }

  public boolean verificationRequired() { return verificationRequired; }

  public Map<String, Object> requireActive(long userId) {
    var user = users.byId(userId);
    if (user == null || !"active".equals(user.get("account_state")))
      throw new ApiException(403, "账号当前不可用", "ACCOUNT_INACTIVE");
    return user;
  }

  public void requireVerified(long userId) {
    var user = requireActive(userId);
    if (verificationRequired && user.get("email_verified_at") == null)
      throw new ApiException(403, "请先验证邮箱，再使用此功能", "EMAIL_VERIFICATION_REQUIRED");
  }

  public static Map<String, Object> publicUser(Map<String, Object> user) {
    var result = new LinkedHashMap<String, Object>();
    for (String field : Set.of("id", "username", "is_admin", "created_at", "email", "email_verified_at", "account_state"))
      result.put(field, user.get(field));
    result.put("email_verified", user.get("email_verified_at") != null);
    return result;
  }
}
