package com.tripplanner.domain;

import org.springframework.context.annotation.*;
import org.springframework.scheduling.concurrent.ThreadPoolTaskScheduler;

@Configuration
public class Schedulers {
  @Bean(name = "taskScheduler")
  ThreadPoolTaskScheduler taskScheduler() {
    var s = new ThreadPoolTaskScheduler();
    s.setPoolSize(2);
    s.setThreadNamePrefix("task-scheduler-");
    return s;
  }

  @Bean(name = "outboxScheduler")
  ThreadPoolTaskScheduler outboxScheduler() {
    var s = new ThreadPoolTaskScheduler();
    s.setPoolSize(1);
    s.setThreadNamePrefix("outbox-");
    return s;
  }
}
