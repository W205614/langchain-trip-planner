CREATE TABLE user_operation_quotas (
 user_id BIGINT PRIMARY KEY REFERENCES users(id),
 daily_tasks INTEGER CHECK(daily_tasks BETWEEN 0 AND 100000),
 active_tasks INTEGER CHECK(active_tasks BETWEEN 0 AND 1000),
 daily_text INTEGER CHECK(daily_text BETWEEN 0 AND 1000000),
 daily_embedding INTEGER CHECK(daily_embedding BETWEEN 0 AND 1000000),
 daily_amap INTEGER CHECK(daily_amap BETWEEN 0 AND 1000000),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE TABLE outbound_user_usage (
 usage_date DATE NOT NULL, user_id BIGINT NOT NULL REFERENCES users(id),
 kind VARCHAR(16) NOT NULL CHECK(kind IN ('text','embedding','amap')),
 reserved_calls BIGINT NOT NULL DEFAULT 0 CHECK(reserved_calls>=0),
 reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
 PRIMARY KEY(usage_date,user_id,kind)
);
ALTER TABLE outbound_reservations ADD COLUMN user_id BIGINT REFERENCES users(id),
 ADD COLUMN operation_id UUID;
CREATE INDEX outbound_reservations_user ON outbound_reservations(user_id,reserved_at);

CREATE TABLE user_feedback (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id),
 category VARCHAR(24) NOT NULL CHECK(category IN ('planning','account','knowledge','other')),
 subject VARCHAR(160) NOT NULL, message VARCHAR(3000) NOT NULL,
 trip_id BIGINT REFERENCES trip_records(id) ON DELETE SET NULL,
 task_id VARCHAR(36) REFERENCES trip_tasks(id) ON DELETE SET NULL,
 request_id VARCHAR(64) NOT NULL DEFAULT '',
 status VARCHAR(16) NOT NULL DEFAULT 'open' CHECK(status IN ('open','processing','closed')),
 reply VARCHAR(3000) NOT NULL DEFAULT '', replied_by BIGINT REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE INDEX user_feedback_queue ON user_feedback(status,created_at,id);
CREATE INDEX user_feedback_owner ON user_feedback(user_id,created_at DESC,id DESC);

CREATE TABLE account_data_jobs (
 id UUID PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id),
 kind VARCHAR(12) NOT NULL CHECK(kind IN ('export','erasure')),
 status VARCHAR(24) NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_cleanup','succeeded','failed','expired')),
 receipt_hash VARCHAR(64), stage VARCHAR(48) NOT NULL DEFAULT 'queued',
 file_name VARCHAR(80), error_code VARCHAR(64) NOT NULL DEFAULT '',
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 expires_at TIMESTAMP
);
CREATE UNIQUE INDEX account_one_live_job ON account_data_jobs(user_id,kind)
 WHERE status IN ('queued','running','waiting_cleanup');
CREATE INDEX account_jobs_ready ON account_data_jobs(status,created_at,id);
CREATE TABLE account_erasure_targets (
 job_id UUID NOT NULL REFERENCES account_data_jobs(id) ON DELETE CASCADE,
 kind VARCHAR(16) NOT NULL CHECK(kind IN ('history','document')),
 target_id BIGINT NOT NULL, version INTEGER,
 PRIMARY KEY(job_id,kind,target_id)
);
