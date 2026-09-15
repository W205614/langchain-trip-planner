package com.tripplanner.security;

import at.favre.lib.crypto.bcrypt.BCrypt;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Matches the legacy Python bcrypt behavior, including byte truncation. */
public final class Passwords {
  private Passwords() {}

  private static byte[] bytes(String password) {
    byte[] value = password.getBytes(StandardCharsets.UTF_8);
    return Arrays.copyOf(value, Math.min(value.length, 72));
  }

  public static String hash(String password) {
    return new String(
        BCrypt.with(BCrypt.Version.VERSION_2B).hash(12, bytes(password)), StandardCharsets.UTF_8);
  }

  public static boolean verify(String password, String hash) {
    try {
      return BCrypt.verifyer()
          .verify(bytes(password), hash.getBytes(StandardCharsets.UTF_8))
          .verified;
    } catch (IllegalArgumentException ex) {
      return false;
    }
  }
}
