package com.tripplanner;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.api.CapabilitiesController;
import com.tripplanner.domain.AmapGateway;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

class PhotoContractTest {
  @Test
  void transientPlaceholderMustNeverBeCachedAsRealPhoto() {
    var agent = mock(AgentClient.class);
    var amap = mock(AmapGateway.class);
    when(agent.post(eq("/capabilities/poi-detail"), any())).thenReturn(
        JsonMapper.builder().build().createObjectNode().set("data", JsonMapper.builder().build().createObjectNode()));
    when(amap.imageFromPoi(anyString(), any()))
        .thenReturn(new AmapGateway.Image("<svg/>".getBytes(), "image/svg+xml", true));
    var response = new CapabilitiesController(agent, amap).photoImage("颐和园", "B000A7O1CU", "北京");
    assertEquals("no-store", response.getHeaders().getFirst("Cache-Control"));
    assertEquals("image/svg+xml", response.getHeaders().getFirst("Content-Type"));
    assertArrayEquals("<svg/>".getBytes(), response.getBody());
  }

  @Test
  void realPhotoRetainsBoundedCache() {
    var agent = mock(AgentClient.class);
    var amap = mock(AmapGateway.class);
    when(agent.post(eq("/capabilities/poi-detail"), any())).thenReturn(
        JsonMapper.builder().build().createObjectNode().set("data", JsonMapper.builder().build().createObjectNode()));
    when(amap.imageFromPoi(anyString(), any()))
        .thenReturn(new AmapGateway.Image(new byte[] {(byte) 0xff, (byte) 0xd8}, "image/jpeg", false));
    var response = new CapabilitiesController(agent, amap).photoImage("颐和园", "B000A7O1CU", "北京");
    assertEquals("public, max-age=3600", response.getHeaders().getFirst("Cache-Control"));
    assertEquals("agent-amap-mcp", response.getHeaders().getFirst("X-Trip-Image-Source"));
  }
}
