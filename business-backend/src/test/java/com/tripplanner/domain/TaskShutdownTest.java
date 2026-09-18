package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

import com.tripplanner.agent.AgentClient;
import com.tripplanner.persistence.*;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.FutureTask;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

class TaskShutdownTest {
  private TaskService service(TaskMapper tasks) {
    var service =
        new TaskService(
            tasks,
            mock(HistoryMapper.class),
            mock(TransactionTemplate.class),
            mock(AgentClient.class),
            JsonMapper.builder().build(),
            mock(TripLedgerService.class),
            mock(BusinessMetrics.class),
            1,
            300,
            32,
            4,
            50,
            500);
    ReflectionTestUtils.setField(service, "workersEnabled", true);
    return service;
  }

  @Test
  void generatedTripTaskDoesNotOwnMapOrPlanningRules() {
    assertFalse(
        java.util.Arrays.stream(TaskService.class.getDeclaredFields())
            .anyMatch(
                field ->
                    field.getType().equals(AmapGateway.class)
                        || field.getType().equals(PlanRules.class)));
  }

  @Test
  @SuppressWarnings("unchecked")
  void restartReasonIsPersistedBeforeWorkerInterruptionAndNoMoreClaims() {
    var tasks = mock(TaskMapper.class);
    var service = service(tasks);
    var active = (Map<String, FutureTask<Void>>) ReflectionTestUtils.getField(service, "active");
    var future = mock(FutureTask.class);
    active.put("fixture", future);
    service.close();
    var order = inOrder(tasks, future);
    order.verify(tasks).recover();
    order.verify(future).cancel(true);
    clearInvocations(tasks);
    service.tick();
    verifyNoInteractions(tasks);
  }

  @Test
  void failedShutdownPersistenceStillStopsPoolAndDisabledRestoreDoesNotWrite() {
    var tasks = mock(TaskMapper.class);
    var service = service(tasks);
    doThrow(new IllegalStateException("fixture database unavailable")).when(tasks).recover();
    assertThrows(IllegalStateException.class, service::close);
    assertTrue(((ExecutorService) ReflectionTestUtils.getField(service, "pool")).isShutdown());
    var disabled = service(tasks);
    ReflectionTestUtils.setField(disabled, "workersEnabled", false);
    clearInvocations(tasks);
    disabled.close();
    verifyNoInteractions(tasks);
  }
}
