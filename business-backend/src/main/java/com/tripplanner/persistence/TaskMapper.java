package com.tripplanner.persistence;

import java.util.*;
import org.apache.ibatis.annotations.*;

@Mapper
public interface TaskMapper {
  @Select("SELECT * FROM trip_tasks WHERE id=#{id} AND user_id=#{uid}")
  Map<String, Object> owned(@Param("uid") long uid, @Param("id") String id);

  @Select("SELECT * FROM trip_tasks WHERE user_id=#{uid} AND idempotency_key=#{key}")
  Map<String, Object> byKey(@Param("uid") long uid, @Param("key") String key);

  @Select("SELECT * FROM trip_tasks WHERE id=#{id}")
  Map<String, Object> get(String id);

  @Select("SELECT count(*) FROM trip_tasks WHERE status IN ('queued','running')")
  long active();

  @Select("SELECT count(*) FROM trip_tasks WHERE user_id=#{uid} AND status IN ('queued','running')")
  long activeUser(long uid);

  @Select(
      "SELECT count(*) FROM trip_tasks WHERE created_at >= date_trunc('day',timezone('UTC',now()))")
  long daily();

  @Select(
      "SELECT count(*) FROM trip_tasks WHERE user_id=#{uid} AND created_at >="
          + " date_trunc('day',timezone('UTC',now()))")
  long dailyUser(long uid);

  @Select("SELECT count(*) FROM trip_tasks WHERE user_id=#{uid}")
  long count(long uid);

  @Select(
      "SELECT * FROM trip_tasks WHERE user_id=#{uid} ORDER BY created_at DESC,id LIMIT #{limit}"
          + " OFFSET #{offset}")
  List<Map<String, Object>> list(
      @Param("uid") long uid, @Param("offset") int offset, @Param("limit") int limit);

  @Insert(
      """
INSERT INTO trip_tasks(id,user_id,idempotency_key,fingerprint,request_json,request_id,deadline_at)
VALUES(#{id},#{user_id},#{idempotency_key},#{fingerprint},#{request_json},#{request_id},#{deadline_at})
""")
  int insert(Map<String, Object> task);

  @Select(
      value =
          """
UPDATE trip_tasks SET status='running',stage='starting',message='正在规划',execution_id=#{execution}
WHERE id=(SELECT id FROM trip_tasks WHERE status='queued' AND deadline_at>timezone('UTC',now())
          ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING *
""",
      affectData = true)
  Map<String, Object> claim(String execution);

  @Update(
      """
UPDATE trip_tasks SET status='failed',error_code='PROCESS_INTERRUPTED',message='服务重启，生成已中断，请重新提交'
WHERE status='running'
""")
  int recover();

  @Update(
      """
      UPDATE trip_tasks SET status='failed',error_code='TASK_TIMEOUT',message='规划超过时间预算'
      WHERE status IN ('queued','running') AND deadline_at<=timezone('UTC',now())
      """)
  int expire();

  @Update(
      """
      UPDATE trip_tasks SET status='cancelled',stage='cancelled',error_code='USER_CANCELLED',
      message='任务已取消，已发出的上游调用可能仍产生费用'
      WHERE id=#{id} AND user_id=#{uid} AND status IN ('queued','running')
      """)
  int cancel(@Param("uid") long uid, @Param("id") String id);

  @Update(
      """
UPDATE trip_tasks SET stage=#{stage},percent=#{percent},message=#{message},usage_json=#{usage_json}
WHERE id=#{id} AND execution_id=#{execution_id} AND status='running' AND deadline_at>timezone('UTC',now())
""")
  int progress(Map<String, Object> task);

  @Update(
      """
      UPDATE trip_tasks SET status='failed',error_code=#{code},message=#{message}
      WHERE id=#{id} AND execution_id=#{execution} AND status='running'
      """)
  int fail(
      @Param("id") String id,
      @Param("execution") String execution,
      @Param("code") String code,
      @Param("message") String message);

  @Update(
      """
UPDATE trip_tasks SET status=#{status},stage='complete',percent=100,message=#{message}
WHERE id=#{id} AND execution_id=#{execution_id} AND status='running' AND deadline_at>timezone('UTC',now())
""")
  int finish(Map<String, Object> task);

  @Update("UPDATE trip_tasks SET record_id=#{record} WHERE id=#{id}")
  int attach(@Param("id") String id, @Param("record") long record);
}
