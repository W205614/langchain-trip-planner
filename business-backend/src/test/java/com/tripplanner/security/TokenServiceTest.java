package com.tripplanner.security;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.UserMapper;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;

class TokenServiceTest {
  final String secret="isolated-token-unit-key-01234567890123456789";
  @Test void revocationStoreFailureIs503WhileBadSignaturesAndExpiredTokensRemain401(){
    var users=mock(UserMapper.class);var tokens=new TokenService(secret,120,users);
    var user=Map.<String,Object>of("id",1L,"token_version",0);String signed=tokens.issue(user);
    when(users.byId(1L)).thenThrow(new DataAccessResourceFailureException("isolated database outage"));
    var outage=assertThrows(ApiException.class,()->tokens.verify(signed));assertEquals(503,outage.status);assertEquals("AUTH_STORE_UNAVAILABLE",outage.code);
    var forged=new TokenService("different-isolated-secret-01234567890123456789",120,users).issue(user);
    assertEquals(401,assertThrows(ApiException.class,()->tokens.verify(forged)).status);
    String expired=new TokenService(secret,-1,users).issue(user);
    assertEquals(401,assertThrows(ApiException.class,()->tokens.verify(expired)).status);
    assertEquals(401,assertThrows(ApiException.class,()->tokens.verify("malformed")).status);
    doReturn(user).when(users).byId(1L);
    assertSame(user,tokens.verify(signed));
  }
}
