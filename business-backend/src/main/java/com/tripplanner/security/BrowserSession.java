package com.tripplanner.security;

import jakarta.servlet.http.*;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseCookie;
import org.springframework.stereotype.Service;

/** Explicit browser mode avoids changing the established non-browser Bearer contract. */
@Service
public class BrowserSession {
  public static final String COOKIE = "trip_session";
  private final long minutes;
  public BrowserSession(@Value("${trip.token-minutes}") long minutes,
      @Value("${SESSION_COOKIE_SECURE:true}") boolean legacySecureFlag) { this.minutes=minutes; }
  public boolean browser(HttpServletRequest request) { return "cookie".equals(request.getHeader("X-Auth-Mode")); }
  public void issue(HttpServletRequest request,HttpServletResponse response,String token) {
    // Only loopback HTTP development may opt out of Secure. Production must terminate TLS.
    boolean local="localhost".equals(request.getServerName()) || "127.0.0.1".equals(request.getServerName()) || "::1".equals(request.getServerName()) || "[::1]".equals(request.getServerName());
    // A legacy false flag must never downgrade a non-loopback browser session.
    boolean protectedTransport=request.isSecure() || !local;
    response.addHeader("Set-Cookie",ResponseCookie.from(COOKIE,token).httpOnly(true).secure(protectedTransport)
        .sameSite("Strict").path("/api").maxAge(minutes*60).build().toString());
    response.addHeader("Set-Cookie",ResponseCookie.from("trip_csrf",csrf(token))
        .httpOnly(false).secure(protectedTransport).sameSite("Strict").path("/").maxAge(minutes*60).build().toString());
    response.setHeader("Cache-Control","no-store");
  }
  public void clear(HttpServletResponse response) {
    for(String name:new String[]{COOKIE,"trip_csrf"}) response.addHeader("Set-Cookie",ResponseCookie.from(name,"")
        .httpOnly(COOKIE.equals(name)).sameSite("Strict").path(COOKIE.equals(name)?"/api":"/").maxAge(0).build().toString());
    response.setHeader("Cache-Control","no-store");
  }
  public static String cookie(HttpServletRequest request,String name) {
    if(request.getCookies()!=null)for(Cookie cookie:request.getCookies())if(name.equals(cookie.getName()))return cookie.getValue();
    return null;
  }
  public static String csrf(String token) {
    try { return Base64.getUrlEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256")
        .digest(("trip-csrf:"+token).getBytes(StandardCharsets.UTF_8))); }
    catch(java.security.NoSuchAlgorithmException ex){throw new IllegalStateException(ex);}
  }
}
