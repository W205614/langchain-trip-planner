package com.tripplanner.security;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

class PasswordsTest {
  @Test
  void unicodeTruncationMatchesLegacyByteSemantics() {
    String value = "旅行".repeat(20);
    String hash = Passwords.hash(value);
    assertTrue(hash.startsWith("$2b$"));
    assertTrue(Passwords.verify(value, hash));
    assertTrue(Passwords.verify("旅行".repeat(12) + "ignored", hash));
    assertFalse(Passwords.verify("错误密码", hash));
  }

  @Test
  void malformedHashFailsClosed() {
    assertFalse(Passwords.verify("password", "broken"));
  }
}
