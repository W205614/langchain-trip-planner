package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface CommunityMapper {
  @Insert(
      """
      INSERT INTO community_trip_cards(record_id,owner_id,record_version,title,city,snapshot_json)
      VALUES(#{record_id},#{owner_id},#{record_version},#{title},#{city},#{snapshot_json})
      """)
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insert(Map<String, Object> row);

  @Select(
      """
      SELECT c.id,c.record_id,c.record_version,c.title,c.city,c.snapshot_json,c.status,
             c.review_note,c.created_at,c.published_at,u.username AS author
      FROM community_trip_cards c JOIN users u ON u.id=c.owner_id
      WHERE c.status='published' ORDER BY c.published_at DESC,c.id DESC
      LIMIT #{limit} OFFSET #{offset}
      """)
  List<Map<String, Object>> published(@Param("offset") int offset, @Param("limit") int limit);

  @Select("SELECT count(*) FROM community_trip_cards WHERE status='published'")
  long publishedCount();

  @Select(
      """
      SELECT c.*,u.username AS author FROM community_trip_cards c
      JOIN users u ON u.id=c.owner_id WHERE c.id=#{id} AND c.status='published'
      """)
  Map<String, Object> publishedById(long id);

  @Select(
      """
      SELECT c.id,c.record_id,c.owner_id,c.record_version,c.title,c.city,c.snapshot_json,c.status,
             c.review_note,c.created_at,c.reviewed_at,u.username AS author
      FROM community_trip_cards c JOIN users u ON u.id=c.owner_id
      WHERE (#{status}='' OR c.status=#{status}) ORDER BY c.created_at,c.id LIMIT #{limit}
      """)
  List<Map<String, Object>> reviewQueue(
      @Param("status") String status, @Param("limit") int limit);

  @Update(
      """
      UPDATE community_trip_cards SET status=#{status},review_note=#{note},reviewed_by=#{reviewer},
      reviewed_at=timezone('UTC',now()),
      published_at=CASE WHEN #{status}='published' THEN timezone('UTC',now()) ELSE NULL END
      WHERE id=#{id} AND status='pending'
      """)
  int review(
      @Param("id") long id,
      @Param("status") String status,
      @Param("note") String note,
      @Param("reviewer") long reviewer);
}
