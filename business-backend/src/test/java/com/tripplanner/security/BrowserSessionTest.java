package com.tripplanner.security;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class BrowserSessionTest {
  @Test void csrfProtectionTracksTheExistingCookieAuthenticationContract() {
    var browser=new BrowserSession(120,true);
    var request=new MockHttpServletRequest("POST","/api/auth/logout");
    assertFalse(browser.requiresCsrf(request));
    request.setCookies(new jakarta.servlet.http.Cookie(BrowserSession.COOKIE,"session-one"));
    assertTrue(browser.requiresCsrf(request));
    var first=browser.loadToken(request);
    assertEquals("X-CSRF-Token",first.getHeaderName());
    assertEquals(BrowserSession.csrf("session-one"),first.getToken());
    request.setCookies(new jakarta.servlet.http.Cookie(BrowserSession.COOKIE,"session-two"));
    assertNotEquals(first.getToken(),browser.loadToken(request).getToken());
    request.addHeader("Authorization","Bearer explicit-client-token");
    assertFalse(browser.requiresCsrf(request));
    request.removeHeader("Authorization");request.setMethod("GET");
    assertFalse(browser.requiresCsrf(request));
    request.setMethod("POST");request.setRequestURI("/api/auth/login");
    assertFalse(browser.requiresCsrf(request));
  }
  @Test void legacyFalseFlagCannotDowngradeRemoteCookiesButLoopbackDevelopmentRemainsUsable(){
    var browser=new BrowserSession(120,false);
    var remote=new MockHttpServletRequest();remote.setServerName("trips.example.com");remote.setSecure(false);
    var response=new MockHttpServletResponse();browser.issue(remote,response,"isolated-unit-session");
    assertEquals(2,response.getHeaders("Set-Cookie").size());
    for(String cookie:response.getHeaders("Set-Cookie"))assertTrue(cookie.contains("Secure"),cookie);
    var loopback=new MockHttpServletRequest();loopback.setServerName("127.0.0.1");
    var localResponse=new MockHttpServletResponse();browser.issue(loopback,localResponse,"isolated-unit-session");
    for(String cookie:localResponse.getHeaders("Set-Cookie"))assertFalse(cookie.contains("Secure"),cookie);
    loopback.setSecure(true);var httpsResponse=new MockHttpServletResponse();browser.issue(loopback,httpsResponse,"isolated-unit-session");
    for(String cookie:httpsResponse.getHeaders("Set-Cookie"))assertTrue(cookie.contains("Secure"),cookie);
  }
}
