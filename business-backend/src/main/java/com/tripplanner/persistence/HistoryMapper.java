package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface HistoryMapper {
  @Select("SELECT * FROM trip_records WHERE id=#{id} AND user_id=#{uid}")
  Map<String, Object> owned(@Param("uid") long uid, @Param("id") long id);

  @Insert(
      """
INSERT INTO trip_records(user_id,departure_city,city,start_date,end_date,travel_days,transportation,accommodation,
traveler_count,room_count,budget_total,preferences,free_text_input,plan_json,quality_json,title,source,last_verified_at) VALUES(#{user_id},COALESCE(#{departure_city},''),#{city},#{start_date},#{end_date},
#{travel_days},#{transportation},#{accommodation},COALESCE(#{traveler_count},1),COALESCE(#{room_count},1),#{budget_total},#{preferences},#{free_text_input},#{plan_json},#{quality_json},
COALESCE(#{title},''),COALESCE(#{source},'agent'),#{last_verified_at})
""")
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insert(Map<String, Object> record);

  @Update(
      """
      UPDATE trip_records SET plan_json=#{plan_json},quality_json=#{quality_json},version=version+1
      WHERE id=#{id} AND user_id=#{user_id} AND version=#{version}
      """)
  int update(Map<String, Object> record);

  @Update("UPDATE trip_records SET last_verified_at=timezone('UTC',now()) WHERE id=#{id} AND user_id=#{uid}")
  int markVerified(@Param("uid") long uid, @Param("id") long id);

  @Insert(
      "INSERT INTO rag_sync_jobs(record_id,user_id,operation) VALUES(#{id},#{uid},#{operation})")
  int outbox(@Param("id") long id, @Param("uid") long uid, @Param("operation") String operation);

  @Delete("DELETE FROM trip_records WHERE id=#{id} AND user_id=#{uid}")
  int delete(@Param("uid") long uid, @Param("id") long id);

  @Select(
      "SELECT * FROM trip_records WHERE user_id=#{uid} AND (#{city}='' OR position(#{city} in"
          + " city)>0) ORDER BY created_at DESC,id DESC LIMIT #{limit} OFFSET #{offset}")
  List<Map<String, Object>> list(
      @Param("uid") long uid,
      @Param("city") String city,
      @Param("offset") int offset,
      @Param("limit") int limit);

  @Select(
      "SELECT count(*) FROM trip_records WHERE user_id=#{uid} AND (#{city}='' OR position(#{city}"
          + " in city)>0)")
  long count(@Param("uid") long uid, @Param("city") String city);
}
