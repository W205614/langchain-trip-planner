package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface FavoriteMapper {
  @Insert("""
      INSERT INTO favorite_pois(user_id,provider,poi_id,city,name,address,longitude,latitude,snapshot_json)
      VALUES(#{user_id},'amap',#{poi_id},#{city},#{name},#{address},#{longitude},#{latitude},#{snapshot_json})
      ON CONFLICT(user_id,provider,poi_id) DO UPDATE SET city=EXCLUDED.city,name=EXCLUDED.name,
      address=EXCLUDED.address,longitude=EXCLUDED.longitude,latitude=EXCLUDED.latitude,
      snapshot_json=EXCLUDED.snapshot_json,refreshed_at=timezone('UTC',now())
      """)
  int upsert(Map<String, Object> row);

  @Select("SELECT * FROM favorite_pois WHERE user_id=#{uid} AND provider='amap' AND poi_id=#{poi}")
  Map<String, Object> get(@Param("uid") long uid, @Param("poi") String poi);

  @Select("""
      SELECT * FROM favorite_pois WHERE user_id=#{uid} AND (#{city}='' OR city=#{city})
      ORDER BY created_at DESC,id DESC LIMIT #{limit} OFFSET #{offset}
      """)
  List<Map<String, Object>> list(@Param("uid") long uid, @Param("city") String city,
      @Param("offset") int offset, @Param("limit") int limit);

  @Select("SELECT count(*) FROM favorite_pois WHERE user_id=#{uid} AND (#{city}='' OR city=#{city})")
  long count(@Param("uid") long uid, @Param("city") String city);

  @Delete("DELETE FROM favorite_pois WHERE user_id=#{uid} AND provider='amap' AND poi_id=#{poi}")
  int delete(@Param("uid") long uid, @Param("poi") String poi);
}
