package com.tripplanner.persistence;

import java.util.Map;
import org.apache.ibatis.annotations.*;

@Mapper
public interface UserMapper {
  @Update("UPDATE users SET is_admin=true WHERE username=#{username} AND is_admin=false")
  int bootstrapAdmin(String username);

  @Select("SELECT * FROM users WHERE id=#{id}")
  Map<String, Object> byId(long id);

  @Select("SELECT * FROM users WHERE username=#{username}")
  Map<String, Object> byName(String username);

  @Insert("INSERT INTO users(username, hashed_password) VALUES(#{username}, #{hashed_password})")
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insert(Map<String, Object> user);

  @Update("UPDATE users SET token_version=token_version+1 WHERE id=#{id}")
  int revoke(long id);

  @Select("SELECT * FROM user_travel_preferences WHERE user_id=#{id}")
  Map<String, Object> preferences(long id);

  @Insert(
      """
      INSERT INTO user_travel_preferences(user_id, preferences, transportation, accommodation)
      VALUES(#{user_id}, #{preferences}, #{transportation}, #{accommodation})
      ON CONFLICT(user_id) DO UPDATE SET preferences=EXCLUDED.preferences,
      transportation=EXCLUDED.transportation, accommodation=EXCLUDED.accommodation,
      updated_at=timezone('UTC', now())
      """)
  int savePreferences(Map<String, Object> values);

  @Delete("DELETE FROM user_travel_preferences WHERE user_id=#{id}")
  int deletePreferences(long id);
}
