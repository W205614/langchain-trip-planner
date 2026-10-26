package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface AssistantMapper {
  @Insert("INSERT INTO assistant_conversations(id,user_id,active_trip_id,title) VALUES(#{id},#{user_id},#{active_trip_id},#{title})") int conversation(Map<String,Object> row);
  @Select("SELECT * FROM assistant_conversations WHERE id=#{id} AND user_id=#{uid}") Map<String,Object> owned(@Param("uid")long uid,@Param("id")String id);
  @Select("SELECT * FROM assistant_conversations WHERE user_id=#{uid} AND active_trip_id=#{trip} ORDER BY updated_at DESC LIMIT 1") Map<String,Object> latestForTrip(@Param("uid")long uid,@Param("trip")long trip);
  @Select("SELECT * FROM assistant_conversations WHERE user_id=#{uid} ORDER BY updated_at DESC LIMIT 50") List<Map<String,Object>> list(@Param("uid")long uid);
  @Insert("INSERT INTO assistant_messages(conversation_id,user_id,role,content,action_type,action_ref,result_json) VALUES(#{conversation_id},#{user_id},#{role},#{content},#{action_type},#{action_ref},COALESCE(#{result_json},'{}'))") int message(Map<String,Object> row);
  @Select("SELECT id,role,content,action_type,action_ref,created_at,result_json FROM assistant_messages WHERE conversation_id=#{id} AND user_id=#{uid} ORDER BY id") List<Map<String,Object>> messages(@Param("uid")long uid,@Param("id")String id);
  @Update("UPDATE assistant_conversations SET updated_at=timezone('UTC',now()),active_trip_id=#{trip} WHERE id=#{id} AND user_id=#{uid}") int touch(@Param("uid")long uid,@Param("id")String id,@Param("trip")Long trip);
  @Update("UPDATE assistant_conversations SET updated_at=timezone('UTC',now()),active_trip_id=NULL WHERE user_id=#{uid} AND active_trip_id=#{trip}") int detachTrip(@Param("uid")long uid,@Param("trip")long trip);

  @Select("SELECT * FROM assistant_clarifications WHERE conversation_id=#{id} AND user_id=#{uid}")
  Map<String,Object> clarification(@Param("uid")long uid,@Param("id")String id);
  @Insert("""
      INSERT INTO assistant_clarifications(conversation_id,user_id,trip_id,original_request,last_input,instruction,operation,
       intent_json,missing_slots,collected_slots,base_version,planning_revision,status,expires_at,revision)
      VALUES(#{conversation_id},#{user_id},#{trip_id},#{original_request},#{last_input},#{instruction},#{operation},#{intent_json},
       #{missing_slots},#{collected_slots},#{base_version},#{planning_revision},#{status},#{expires_at},1)
      ON CONFLICT(conversation_id) DO UPDATE SET trip_id=excluded.trip_id,original_request=excluded.original_request,
       last_input=excluded.last_input,instruction=excluded.instruction,operation=excluded.operation,intent_json=excluded.intent_json,missing_slots=excluded.missing_slots,
       collected_slots=excluded.collected_slots,base_version=excluded.base_version,planning_revision=excluded.planning_revision,
       status=excluded.status,expires_at=excluded.expires_at,revision=assistant_clarifications.revision+1,
       accepted_key=NULL,accepted_task=NULL,updated_at=now()
      WHERE assistant_clarifications.user_id=excluded.user_id AND assistant_clarifications.revision=#{expected_revision}
      """) int saveClarification(Map<String,Object> row);
  @Update("""
      UPDATE assistant_clarifications SET status=#{status},revision=revision+1,updated_at=now()
      WHERE conversation_id=#{id} AND user_id=#{uid} AND revision=#{revision}
      """)
  int clarificationStatus(@Param("uid")long uid,@Param("id")String id,@Param("revision")int revision,@Param("status")String status);
  @Update("""
      UPDATE assistant_clarifications SET status='submitting',accepted_key=#{key},revision=revision+1,updated_at=now()
      WHERE conversation_id=#{id} AND user_id=#{uid} AND revision=#{revision} AND status='ready'
      """)
  int claimClarification(@Param("uid")long uid,@Param("id")String id,@Param("revision")int revision,@Param("key")String key);
  @Update("""
      UPDATE assistant_clarifications SET status='consumed',accepted_key=#{key},accepted_task=#{task},updated_at=now()
      WHERE conversation_id=#{id} AND user_id=#{uid} AND revision=#{revision} AND status='submitting'
      """)
  int clarificationAccepted(@Param("uid")long uid,@Param("id")String id,@Param("revision")int revision,@Param("key")String key,@Param("task")String task);
}
