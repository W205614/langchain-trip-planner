package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface TripVersionMapper {
  @Insert(
      """
      INSERT INTO trip_record_versions(
        record_id,user_id,record_version,change_type,request_id,title,source,plan_json,quality_json)
      SELECT id,user_id,version,#{changeType},#{requestId},title,source,plan_json,quality_json
      FROM trip_records WHERE id=#{recordId} AND user_id=#{userId}
      """)
  int capture(
      @Param("userId") long userId,
      @Param("recordId") long recordId,
      @Param("changeType") String changeType,
      @Param("requestId") String requestId);

  @Select(
      """
      SELECT record_version AS version,change_type,request_id,title,source,created_at
      FROM trip_record_versions
      WHERE user_id=#{userId} AND record_id=#{recordId}
      ORDER BY record_version DESC
      LIMIT #{limit} OFFSET #{offset}
      """)
  List<Map<String, Object>> list(
      @Param("userId") long userId,
      @Param("recordId") long recordId,
      @Param("offset") int offset,
      @Param("limit") int limit);

  @Select(
      "SELECT count(*) FROM trip_record_versions WHERE user_id=#{userId} AND record_id=#{recordId}")
  long count(@Param("userId") long userId, @Param("recordId") long recordId);

  @Select(
      """
      SELECT record_id,user_id,record_version AS version,change_type,request_id,title,source,
             plan_json,quality_json,created_at
      FROM trip_record_versions
      WHERE user_id=#{userId} AND record_id=#{recordId} AND record_version=#{version}
      """)
  Map<String, Object> owned(
      @Param("userId") long userId,
      @Param("recordId") long recordId,
      @Param("version") int version);
}
