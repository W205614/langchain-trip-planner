-- Reservations survive process death; an uncertain filesystem/DB outcome keeps its quota.
ALTER TABLE knowledge_documents ADD COLUMN size_bytes BIGINT NOT NULL DEFAULT 20971520 CHECK(size_bytes>=0);
CREATE TABLE upload_reservations (
 id UUID PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id),
 sha256 VARCHAR(64) NOT NULL, size_bytes BIGINT NOT NULL CHECK(size_bytes>0),
 stored_path VARCHAR(80) NOT NULL UNIQUE, city VARCHAR(64) NOT NULL, title VARCHAR(160) NOT NULL,
 original_filename VARCHAR(255) NOT NULL, media_type VARCHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL CHECK(status IN ('reserved','stored','uncertain','committed','released')),
 document_id BIGINT REFERENCES knowledge_documents(id),
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE INDEX upload_reservations_rate ON upload_reservations(user_id,created_at);
CREATE UNIQUE INDEX upload_reservations_active_hash ON upload_reservations(user_id,sha256)
 WHERE status IN ('reserved','stored','uncertain');
CREATE INDEX knowledge_owner_hash ON knowledge_documents(submitted_by,sha256);
CREATE TABLE account_security (
 user_id BIGINT PRIMARY KEY REFERENCES users(id),
 totp_secret TEXT, pending_totp_secret TEXT, pending_expires_at TIMESTAMP,
 totp_enabled BOOLEAN NOT NULL DEFAULT FALSE, last_totp_step BIGINT NOT NULL DEFAULT -1,
 recovery_hash VARCHAR(64), recovery_created_at TIMESTAMP,
 failed_attempts INTEGER NOT NULL DEFAULT 0, blocked_until TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE TABLE account_login_attempts (
 identity_hash VARCHAR(64) PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
 window_started_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()), blocked_until TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
