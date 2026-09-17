package com.tripplanner.persistence;

import java.util.Map;
import org.apache.ibatis.annotations.*;

@Mapper
public interface AuditMapper {
  @Insert(
      """
      INSERT INTO business_audit_events(
        request_id,actor_type,actor_id,action,resource_type,resource_id,resource_version,outcome,metadata_json)
      VALUES(#{request_id},#{actor_type},#{actor_id},#{action},#{resource_type},#{resource_id},
             #{resource_version},#{outcome},CAST(#{metadata_json} AS jsonb))
      """)
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insert(Map<String, Object> event);
}
