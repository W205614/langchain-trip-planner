package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface OutboxMapper {
  @Select("SELECT * FROM rag_sync_jobs WHERE id=#{id} FOR UPDATE")
  Map<String, Object> historyJob(long id);

  @Select("SELECT * FROM knowledge_ingest_jobs WHERE id=#{id} FOR UPDATE")
  Map<String, Object> knowledgeJob(long id);

  @Select(
      "SELECT * FROM rag_sync_jobs WHERE status IN ('pending','retry','running','waiting') AND"
          + " next_retry_at<=timezone('UTC',now()) ORDER BY id LIMIT 1")
  Map<String, Object> nextHistory();

  @Select(
      "SELECT * FROM knowledge_ingest_jobs WHERE status IN ('pending','retry','running','waiting')"
          + " AND next_retry_at<=timezone('UTC',now()) ORDER BY id LIMIT 1")
  Map<String, Object> nextKnowledge();

  @Update(
      "UPDATE rag_sync_jobs SET status=#{status},"
          + " attempts=#{attempts},last_error=#{error},next_retry_at=timezone('UTC',now())+make_interval(secs"
          + " => #{delay}) WHERE id=#{id}")
  int history(
      @Param("id") long id,
      @Param("status") String status,
      @Param("attempts") int attempts,
      @Param("error") String error,
      @Param("delay") int delay);

  @Update(
      "UPDATE knowledge_ingest_jobs SET status=#{status},"
          + " attempts=#{attempts},last_error=#{error},next_retry_at=timezone('UTC',now())+make_interval(secs"
          + " => #{delay}) WHERE id=#{id}")
  int knowledge(
      @Param("id") long id,
      @Param("status") String status,
      @Param("attempts") int attempts,
      @Param("error") String error,
      @Param("delay") int delay);

  @Select(
      "SELECT id,status,attempts,last_error,record_id FROM rag_sync_jobs WHERE status IN"
          + " ('failed','waiting','retry') ORDER BY id LIMIT 100")
  List<Map<String, Object>> historyFailures();

  @Select(
      "SELECT id,status,attempts,last_error,document_id,document_version,phase FROM"
          + " knowledge_ingest_jobs WHERE status IN ('failed','waiting','retry') ORDER BY id LIMIT"
          + " 100")
  List<Map<String, Object>> knowledgeFailures();
}
