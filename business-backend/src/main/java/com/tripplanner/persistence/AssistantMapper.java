package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface AssistantMapper {
  @Insert("INSERT INTO assistant_conversations(id,user_id,active_trip_id,title) VALUES(#{id},#{user_id},#{active_trip_id},#{title})") int conversation(Map<String,Object> row);
  @Select("SELECT * FROM assistant_conversations WHERE id=#{id} AND user_id=#{uid}") Map<String,Object> owned(@Param("uid")long uid,@Param("id")String id);
  @Select("SELECT * FROM assistant_conversations WHERE user_id=#{uid} ORDER BY updated_at DESC LIMIT 50") List<Map<String,Object>> list(@Param("uid")long uid);
  @Insert("INSERT INTO assistant_messages(conversation_id,user_id,role,content,action_type,action_ref) VALUES(#{conversation_id},#{user_id},#{role},#{content},#{action_type},#{action_ref})") int message(Map<String,Object> row);
  @Select("SELECT id,role,content,action_type,action_ref,created_at FROM assistant_messages WHERE conversation_id=#{id} AND user_id=#{uid} ORDER BY id") List<Map<String,Object>> messages(@Param("uid")long uid,@Param("id")String id);
  @Update("UPDATE assistant_conversations SET updated_at=timezone('UTC',now()),active_trip_id=#{trip} WHERE id=#{id} AND user_id=#{uid}") int touch(@Param("uid")long uid,@Param("id")String id,@Param("trip")Long trip);
}
