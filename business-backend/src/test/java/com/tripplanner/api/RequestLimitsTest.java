package com.tripplanner.api;

import static org.junit.jupiter.api.Assertions.*;

import java.security.Principal;
import java.util.concurrent.*;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.util.ReflectionTestUtils;

class RequestLimitsTest {
  private RequestLimits limiter() {
    var limiter = new RequestLimits();
    ReflectionTestUtils.setField(limiter, "environment", "test");
    ReflectionTestUtils.setField(limiter, "fixtures", "no");
    return limiter;
  }

  private MockHttpServletRequest login(String user) {
    var request = new MockHttpServletRequest("POST", "/api/auth/login");
    request.setRemoteAddr("127.0.0.1");
    if (user != null) request.setUserPrincipal((Principal) () -> user);
    return request;
  }

  @Test
  void authenticatedUsersBehindOneAddressHaveIndependentBuckets() {
    var limiter = limiter();
    for (int index = 0; index < 10; index++) {
      assertTrue(limiter.preHandle(login("alice"), new MockHttpServletResponse(), this));
      assertTrue(limiter.preHandle(login("bob"), new MockHttpServletResponse(), this));
    }
    var response = new MockHttpServletResponse();
    var rejected = assertThrows(ApiException.class, () -> limiter.preHandle(login("alice"), response, this));
    assertEquals(429, rejected.status);
    assertEquals("REQUEST_RATE_LIMITED", rejected.code);
    assertEquals("60", response.getHeader("Retry-After"));
  }

  @Test
  void unrelatedBucketsCanProgressConcurrently() throws Exception {
    var limiter = limiter();
    try (var pool = Executors.newFixedThreadPool(16)) {
      var futures = new java.util.ArrayList<Future<Boolean>>();
      for (int index = 0; index < 200; index++) {
        int user = index;
        futures.add(
            pool.submit(
                () ->
                    limiter.preHandle(
                        login("user-" + user), new MockHttpServletResponse(), this)));
      }
      for (var future : futures) assertTrue(future.get(5, TimeUnit.SECONDS));
    }
  }
}
