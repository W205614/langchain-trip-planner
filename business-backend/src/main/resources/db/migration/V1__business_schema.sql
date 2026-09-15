CREATE TABLE users (
 id BIGSERIAL PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,
 hashed_password VARCHAR(128) NOT NULL, is_admin BOOLEAN NOT NULL DEFAULT FALSE,
 token_version INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE TABLE user_travel_preferences (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL UNIQUE,
 preferences TEXT NOT NULL DEFAULT '[]', transportation VARCHAR(32) NOT NULL DEFAULT '公共交通',
 accommodation VARCHAR(32) NOT NULL DEFAULT '经济型酒店',
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE TABLE trip_records (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, city VARCHAR(64) NOT NULL,
 start_date VARCHAR(16) NOT NULL, end_date VARCHAR(16) NOT NULL, travel_days INTEGER NOT NULL DEFAULT 1,
 transportation VARCHAR(32) NOT NULL DEFAULT '', accommodation VARCHAR(32) NOT NULL DEFAULT '',
 preferences TEXT NOT NULL DEFAULT '[]', free_text_input TEXT NOT NULL DEFAULT '',
 plan_json TEXT NOT NULL, quality_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE INDEX trip_records_owner ON trip_records(user_id, created_at DESC);
CREATE TABLE trip_tasks (
 id VARCHAR(36) PRIMARY KEY, user_id BIGINT NOT NULL, idempotency_key VARCHAR(128) NOT NULL,
 fingerprint VARCHAR(64) NOT NULL, request_json TEXT NOT NULL, usage_json TEXT NOT NULL DEFAULT '{}',
 request_id VARCHAR(64) NOT NULL DEFAULT '', execution_id VARCHAR(36),
 status VARCHAR(16) NOT NULL DEFAULT 'queued', stage VARCHAR(64) NOT NULL DEFAULT 'queued',
 percent INTEGER NOT NULL DEFAULT 0, message VARCHAR(255) NOT NULL DEFAULT '等待生成',
 error_code VARCHAR(64) NOT NULL DEFAULT '', record_id BIGINT, deadline_at TIMESTAMP NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()),
 CONSTRAINT uq_trip_task_user_key UNIQUE(user_id, idempotency_key)
);
CREATE INDEX trip_tasks_queue ON trip_tasks(status, created_at);
CREATE TABLE rag_sync_jobs (
 id BIGSERIAL PRIMARY KEY, record_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 operation VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_retry_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), last_error VARCHAR(512) NOT NULL DEFAULT '',
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE TABLE knowledge_documents (
 id BIGSERIAL PRIMARY KEY, submitted_by BIGINT NOT NULL, reviewed_by BIGINT,
 city VARCHAR(64) NOT NULL, title VARCHAR(160) NOT NULL, original_filename VARCHAR(255) NOT NULL,
 stored_path VARCHAR(512) NOT NULL, sha256 VARCHAR(64) NOT NULL, media_type VARCHAR(64) NOT NULL,
 source_tier VARCHAR(16) NOT NULL DEFAULT 'community', status VARCHAR(16) NOT NULL DEFAULT 'pending',
 review_note VARCHAR(512) NOT NULL DEFAULT '', page_count INTEGER NOT NULL DEFAULT 0,
 source_text TEXT NOT NULL DEFAULT '', extracted_pages_json TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE TABLE knowledge_ingest_jobs (
 id BIGSERIAL PRIMARY KEY, document_id BIGINT NOT NULL, document_version INTEGER NOT NULL DEFAULT 1,
 status VARCHAR(16) NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_retry_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), last_error VARCHAR(512) NOT NULL DEFAULT '',
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()), updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
-- Transactional watermark: snapshots cannot be activated after business evidence changes.
CREATE TABLE evidence_revision (id INTEGER PRIMARY KEY CHECK(id = 1), revision BIGINT NOT NULL DEFAULT 0);
INSERT INTO evidence_revision(id) VALUES (1);
CREATE FUNCTION bump_evidence_revision() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 UPDATE evidence_revision SET revision = revision + 1 WHERE id = 1;
 RETURN NULL;
END $$;
CREATE TRIGGER trip_evidence_change AFTER INSERT OR UPDATE OR DELETE ON trip_records
 FOR EACH STATEMENT EXECUTE FUNCTION bump_evidence_revision();
CREATE TRIGGER knowledge_evidence_change AFTER INSERT OR UPDATE OR DELETE ON knowledge_documents
 FOR EACH STATEMENT EXECUTE FUNCTION bump_evidence_revision();
