ALTER TABLE trip_records ADD COLUMN title VARCHAR(160) NOT NULL DEFAULT '';
ALTER TABLE trip_records ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'agent'
    CHECK (source IN ('manual','agent','assistant_revision','copied'));
ALTER TABLE trip_records ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now());
ALTER TABLE trip_records ADD COLUMN last_verified_at TIMESTAMP;

CREATE TABLE favorite_pois (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, provider VARCHAR(32) NOT NULL DEFAULT 'amap',
 poi_id VARCHAR(64) NOT NULL, city VARCHAR(64) NOT NULL, name VARCHAR(160) NOT NULL,
 address VARCHAR(255) NOT NULL DEFAULT '', longitude DOUBLE PRECISION NOT NULL,
 latitude DOUBLE PRECISION NOT NULL, snapshot_json TEXT NOT NULL,
 refreshed_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()),
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()),
 CONSTRAINT uq_favorite_poi UNIQUE(user_id, provider, poi_id)
);
CREATE INDEX favorite_pois_owner ON favorite_pois(user_id, created_at DESC);

CREATE TABLE trip_shares (
 id BIGSERIAL PRIMARY KEY, record_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
 token_hash VARCHAR(64) NOT NULL UNIQUE, snapshot_json TEXT NOT NULL, record_version INTEGER NOT NULL,
 expires_at TIMESTAMP NOT NULL, revoked_at TIMESTAMP,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE INDEX trip_shares_owner ON trip_shares(owner_id, created_at DESC);

CREATE TABLE assistant_conversations (
 id VARCHAR(36) PRIMARY KEY, user_id BIGINT NOT NULL, active_trip_id BIGINT,
 title VARCHAR(160) NOT NULL DEFAULT '旅行助手',
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE INDEX assistant_conversations_owner ON assistant_conversations(user_id, updated_at DESC);

CREATE TABLE assistant_messages (
 id BIGSERIAL PRIMARY KEY, conversation_id VARCHAR(36) NOT NULL, user_id BIGINT NOT NULL,
 role VARCHAR(16) NOT NULL CHECK(role IN ('user','assistant','system_event')),
 content TEXT NOT NULL, action_type VARCHAR(32) NOT NULL DEFAULT '', action_ref VARCHAR(64) NOT NULL DEFAULT '',
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now())
);
CREATE INDEX assistant_messages_conversation ON assistant_messages(conversation_id, id);

CREATE OR REPLACE FUNCTION touch_trip_record() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = timezone('UTC', now()); RETURN NEW; END $$;
CREATE TRIGGER trip_records_touch BEFORE UPDATE ON trip_records
 FOR EACH ROW EXECUTE FUNCTION touch_trip_record();
