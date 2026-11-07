ALTER TABLE users ADD COLUMN account_state VARCHAR(16) NOT NULL DEFAULT 'active'
 CHECK(account_state IN ('active','disabled','deleting','deleted'));
ALTER TABLE users ADD COLUMN email VARCHAR(254);
ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP;
CREATE UNIQUE INDEX users_email_unique ON users(lower(email)) WHERE email IS NOT NULL AND account_state <> 'deleted';

CREATE TABLE account_email_tokens (
 token_hash VARCHAR(64) PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(id),
 purpose VARCHAR(16) NOT NULL CHECK(purpose IN ('verify','reset')),
 target_email VARCHAR(254) NOT NULL,
 expires_at TIMESTAMP NOT NULL, consumed_at TIMESTAMP,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE INDEX account_email_token_owner ON account_email_tokens(user_id,purpose,created_at DESC);

CREATE TABLE mail_deliveries (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
 event_key VARCHAR(160) NOT NULL UNIQUE, kind VARCHAR(32) NOT NULL,
 encrypted_payload TEXT,
 status VARCHAR(16) NOT NULL DEFAULT 'pending'
   CHECK(status IN ('pending','sending','retry','accepted','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
 next_attempt_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 expires_at TIMESTAMP NOT NULL, lease_until TIMESTAMP,
 last_error VARCHAR(80) NOT NULL DEFAULT '',
 accepted_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE INDEX mail_delivery_queue ON mail_deliveries(status,next_attempt_at,id);
CREATE TABLE notification_preferences (
 user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 email_enabled BOOLEAN NOT NULL DEFAULT FALSE,
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
