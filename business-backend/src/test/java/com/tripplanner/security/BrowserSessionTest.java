package com.tripplanner.security;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

class BrowserSessionTest {
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
