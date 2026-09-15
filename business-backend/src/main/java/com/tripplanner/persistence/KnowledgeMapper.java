package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface KnowledgeMapper {
  @Select("SELECT * FROM knowledge_documents WHERE id=#{id}")
  Map<String, Object> get(long id);

  @Select("SELECT * FROM knowledge_documents WHERE submitted_by=#{uid} ORDER BY id DESC")
  List<Map<String, Object>> mine(long uid);

  @Select(
      "SELECT * FROM knowledge_documents WHERE #{status}='' OR status=#{status} ORDER BY id DESC")
  List<Map<String, Object>> list(String status);

  @Insert(
      """
INSERT INTO knowledge_documents(submitted_by,city,title,original_filename,stored_path,sha256,media_type)
VALUES(#{submitted_by},#{city},#{title},#{original_filename},#{stored_path},#{sha256},#{media_type})
""")
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insert(Map<String, Object> row);

  @Update(
      """
UPDATE knowledge_documents SET status=#{status},version=#{next_version},reviewed_by=#{reviewed_by},
review_note=#{review_note},source_tier=#{source_tier},extracted_pages_json=#{extracted_pages_json},
source_text=#{source_text},page_count=#{page_count},updated_at=timezone('UTC',now()) WHERE id=#{id} AND version=#{version}
""")
  int update(Map<String, Object> row);

  @Insert(
      "INSERT INTO knowledge_ingest_jobs(document_id,document_version,phase)"
          + " VALUES(#{id},#{version},#{phase})")
  int enqueue(@Param("id") long id, @Param("version") int version, @Param("phase") String phase);
}
