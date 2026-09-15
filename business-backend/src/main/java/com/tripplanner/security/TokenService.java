package com.tripplanner.security;

import com.nimbusds.jose.*;
import com.nimbusds.jose.crypto.*;
import com.nimbusds.jwt.*;
import com.tripplanner.api.ApiException;
import com.tripplanner.persistence.UserMapper;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class TokenService {
  private final byte[] key;
  private final long minutes;
  private final UserMapper users;

  public TokenService(
      @Value("${trip.jwt-secret}") String secret,
      @Value("${trip.token-minutes}") long minutes,
      UserMapper users) {
    key = secret.getBytes(StandardCharsets.UTF_8);
    if (key.length < 32)
      throw new IllegalArgumentException("JWT_SECRET_KEY requires at least 32 UTF-8 bytes");
    this.minutes = minutes;
    this.users = users;
  }

  public String issue(Map<String, Object> user) {
    try {
      var claims =
          new JWTClaimsSet.Builder()
              .subject(user.get("id").toString())
              .expirationTime(Date.from(Instant.now().plusSeconds(minutes * 60)))
              .claim("ver", ((Number) user.get("token_version")).intValue())
              .build();
      var jwt = new SignedJWT(new JWSHeader(JWSAlgorithm.HS256), claims);
      jwt.sign(new MACSigner(key));
      return jwt.serialize();
    } catch (JOSEException ex) {
      throw new IllegalStateException("JWT signing failed", ex);
    }
  }

  public Map<String, Object> verify(String token) {
    try {
      var jwt = SignedJWT.parse(token);
      if (!JWSAlgorithm.HS256.equals(jwt.getHeader().getAlgorithm())
          || !jwt.verify(new MACVerifier(key))) throw new IllegalArgumentException();
      var claims = jwt.getJWTClaimsSet();
      if (claims.getExpirationTime() == null || !claims.getExpirationTime().after(new Date()))
        throw new IllegalArgumentException();
      var user = users.byId(Long.parseLong(claims.getSubject()));
      Number ver = (Number) claims.getClaim("ver");
      if (user == null
          || ((Number) user.get("token_version")).intValue() != (ver == null ? 0 : ver.intValue()))
        throw new IllegalArgumentException();
      return user;
    } catch (Exception ex) {
      throw new ApiException(401, "未登录或登录已过期");
    }
  }
}
