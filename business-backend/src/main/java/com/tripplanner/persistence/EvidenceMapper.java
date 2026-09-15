package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface EvidenceMapper {
  @Select("SELECT * FROM knowledge_documents WHERE id=#{id}")
  Map<String, Object> document(long id);

  @Select("SELECT * FROM trip_records WHERE id=#{id}")
  Map<String, Object> record(long id);

  @Select("SELECT revision FROM evidence_revision WHERE id=1")
  long revision();

  @Select("SELECT * FROM knowledge_documents WHERE status='published' ORDER BY id")
  List<Map<String, Object>> published();

  @Select(
      "SELECT * FROM trip_records WHERE quality_json::jsonb->>'outcome' IS DISTINCT FROM 'draft'"
          + " ORDER BY id")
  List<Map<String, Object>> records();
}
