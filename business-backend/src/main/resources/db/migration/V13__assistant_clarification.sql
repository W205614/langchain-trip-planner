-- Durable, user- and trip-bound slot collection. This state never authorizes
-- applying a plan: the existing proposal review lifecycle remains mandatory.
CREATE TABLE assistant_clarifications (
 conversation_id VARCHAR(36) PRIMARY KEY REFERENCES assistant_conversations(id) ON DELETE CASCADE,
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
 original_request VARCHAR(500) NOT NULL,
 last_input VARCHAR(500) NOT NULL,
 instruction VARCHAR(1000) NOT NULL,
 operation VARCHAR(32) NOT NULL,
 intent_json TEXT NOT NULL,
 missing_slots TEXT NOT NULL DEFAULT '[]',
 collected_slots TEXT NOT NULL DEFAULT '{}',
 base_version INTEGER NOT NULL,
 planning_revision INTEGER NOT NULL,
 status VARCHAR(24) NOT NULL CHECK(status IN ('pending','ready','submitting','consumed','cancelled')),
 accepted_key VARCHAR(128), accepted_task VARCHAR(36),
 expires_at TIMESTAMPTZ NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX assistant_clarifications_expiry_idx ON assistant_clarifications(expires_at);
ALTER TABLE assistant_messages ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}';
