package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface ShareMapper {
  @Insert("""
      INSERT INTO trip_shares(record_id,owner_id,token_hash,snapshot_json,record_version,expires_at,allow_suggestions)
      VALUES(#{record_id},#{owner_id},#{token_hash},#{snapshot_json},#{record_version},#{expires_at},COALESCE(#{allow_suggestions,jdbcType=BOOLEAN},FALSE))
      """)
  @Options(useGeneratedKeys=true, keyProperty="id") int insert(Map<String,Object> row);

  @Select("SELECT id,record_id,owner_id,record_version,expires_at,revoked_at,created_at,allow_suggestions FROM trip_shares WHERE owner_id=#{uid} AND record_id=#{record} ORDER BY id DESC")
  List<Map<String,Object>> list(@Param("uid") long uid, @Param("record") long record);

  @Select("SELECT * FROM trip_shares WHERE token_hash=#{hash} AND revoked_at IS NULL AND expires_at>timezone('UTC',now())")
  Map<String,Object> active(@Param("hash") String hash);

  @Select("""
      SELECT id,record_id,owner_id,record_version,snapshot_json FROM trip_shares
      WHERE token_hash=#{hash} AND allow_suggestions AND revoked_at IS NULL
        AND expires_at>timezone('UTC',now()) FOR SHARE
      """)
  Map<String,Object> forSuggestion(@Param("hash") String hash);

  @Update("UPDATE trip_shares SET revoked_at=timezone('UTC',now()), snapshot_json='{}' WHERE id=#{id} AND owner_id=#{uid} AND record_id=#{record} AND revoked_at IS NULL")
  int revoke(@Param("uid") long uid, @Param("record") long record, @Param("id") long id);

  @Update("UPDATE trip_shares SET revoked_at=COALESCE(revoked_at,timezone('UTC',now())), snapshot_json='{}' WHERE owner_id=#{uid} AND record_id=#{record} AND (revoked_at IS NULL OR snapshot_json<>'{}')")
  int revokeAll(@Param("uid") long uid, @Param("record") long record);
}
