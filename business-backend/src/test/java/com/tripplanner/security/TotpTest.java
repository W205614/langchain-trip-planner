package com.tripplanner.security;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;

class TotpTest {
  @Test void matchesRfc6238Sha1VectorAndRejectsReplayMalformedAndExpiredCodes(){
    String secret=Totp.base32("12345678901234567890".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
    assertEquals("287082",Totp.code(secret,59/30));
    assertEquals(1,Totp.verify(secret,"287082",59,-1));
    assertEquals(-1,Totp.verify(secret,"287082",59,1));
    assertEquals(-1,Totp.verify(secret,"287082",300,-1));
    assertEquals(-1,Totp.verify(secret,"12345x",59,-1));
  }
  @Test void encryptedSeedRoundTripsAndTamperingAndWrongKeyFailClosed(){
    String key=java.util.Base64.getEncoder().encodeToString(new byte[32]);String value=Totp.encrypt("TESTSEED",key);
    assertNotEquals("TESTSEED",value);assertEquals("TESTSEED",Totp.decrypt(value,key));
    byte[] different=new byte[32];different[0]=1;
    assertThrows(com.tripplanner.api.ApiException.class,()->Totp.decrypt(value,java.util.Base64.getEncoder().encodeToString(different)));
    assertThrows(com.tripplanner.api.ApiException.class,()->Totp.encrypt("TESTSEED",""));
  }
  @Test void newPasswordsDoNotSilentlyTruncateUnicodeOrAcceptCommonValues(){
    assertThrows(com.tripplanner.api.ApiException.class,()->AccountSecurityService.validatePassword("旅行".repeat(20),"user"));
    assertThrows(com.tripplanner.api.ApiException.class,()->AccountSecurityService.validatePassword("password123","user"));
    assertDoesNotThrow(()->AccountSecurityService.validatePassword("密码-abcdef","user"));
  }
}
