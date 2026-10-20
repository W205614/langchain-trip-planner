package com.tripplanner.domain;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import com.tripplanner.agent.AgentClient;
import com.tripplanner.persistence.*;
import java.sql.Connection;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

@EnabledIfEnvironmentVariable(named="TEST_DATABASE_URL",matches=".+")
class SingleInstanceLockLossTest {
  @Test void terminatingActualAdvisoryLockSessionStopsSchedulerUntilReacquired()throws Exception{
    var source=new DriverManagerDataSource(System.getenv("TEST_DATABASE_URL"),"trip",System.getenv().getOrDefault("TEST_DATABASE_PASSWORD","isolated-test-only"));
    var owner=new SingleInstance(source,true);
    try{
      owner.acquire();assertTrue(owner.owned());
      var lease=(Connection)ReflectionTestUtils.getField(owner,"lease");int pid;
      try(var s=lease.createStatement();var rs=s.executeQuery("SELECT pg_backend_pid()")){assertTrue(rs.next());pid=rs.getInt(1);}
      try(var c=source.getConnection();var s=c.prepareStatement("SELECT pg_terminate_backend(?)")){s.setInt(1,pid);try(var rs=s.executeQuery()){assertTrue(rs.next());assertTrue(rs.getBoolean(1));}}
      assertFalse(owner.owned());
      var jobs=mock(OutboxMapper.class);var agent=mock(AgentClient.class);
      var worker=new OutboxWorker(jobs,mock(HistoryMapper.class),mock(KnowledgeMapper.class),agent,mock(TransactionTemplate.class),JsonMapper.builder().build(),mock(KnowledgeService.class));
      ReflectionTestUtils.setField(worker,"singleInstance",owner);ReflectionTestUtils.setField(worker,"workersEnabled",true);
      worker.tick();verifyNoInteractions(jobs,agent);
      var competitor=new SingleInstance(source,true);competitor.acquire();assertTrue(competitor.owned());competitor.release();
      owner.acquire();assertTrue(owner.owned());
    }finally{owner.release();}
  }
}
