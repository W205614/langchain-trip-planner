package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.api.CapabilitiesController;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class PhotoContractTest {
  @Test
  void transientPlaceholderMustNeverBeCachedAsRealPhoto() {
    var agent = mock(AgentClient.class);
    var json = JsonMapper.builder().build();
    when(agent.post(eq("/capabilities/photo-image"), any()))
        .thenReturn(
            json.createObjectNode()
                .put("content_type", "image/svg+xml")
                .put("content", "PHN2Zy8+"));
    var response = new CapabilitiesController(agent).photoImage("颐和园", "B000A7O1CU", "北京");
    assertEquals("no-store", response.getHeaders().getFirst("Cache-Control"));
    assertEquals("image/svg+xml", response.getHeaders().getFirst("Content-Type"));
    assertArrayEquals("<svg/>".getBytes(), response.getBody());
  }

  @Test
  void realPhotoRetainsBoundedCache() {
    var agent = mock(AgentClient.class);
    var json = JsonMapper.builder().build();
    when(agent.post(eq("/capabilities/photo-image"), any()))
        .thenReturn(
            json.createObjectNode().put("content_type", "image/jpeg").put("content", "/9j/"));
    var response = new CapabilitiesController(agent).photoImage("颐和园", "B000A7O1CU", "北京");
    assertEquals("public, max-age=3600", response.getHeaders().getFirst("Cache-Control"));
  }
}
