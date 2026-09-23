CREATE TABLE trip_checks (
 id BIGSERIAL PRIMARY KEY,
 trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
 trip_version INTEGER NOT NULL CHECK(trip_version >= 1),
 checked_by BIGINT NOT NULL REFERENCES users(id),
 checked_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 expires_at TIMESTAMP NOT NULL,
 status VARCHAR(16) NOT NULL CHECK(status IN ('current','uncertain')),
 facts_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX trip_checks_latest ON trip_checks(trip_id,checked_at DESC,id DESC);

CREATE TABLE trip_risks (
 id BIGSERIAL PRIMARY KEY,
 check_id BIGINT NOT NULL REFERENCES trip_checks(id) ON DELETE CASCADE,
 trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
 day_index INTEGER NOT NULL CHECK(day_index >= 0),
 poi_id VARCHAR(64) NOT NULL DEFAULT '',
 code VARCHAR(40) NOT NULL,
 message VARCHAR(300) NOT NULL,
 source VARCHAR(32) NOT NULL,
 observed_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 acknowledged_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
 acknowledged_at TIMESTAMP
);
CREATE INDEX trip_risks_trip ON trip_risks(trip_id,check_id,day_index);

CREATE TABLE trip_commitments (
 id BIGSERIAL PRIMARY KEY,
 trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
 day_index INTEGER NOT NULL CHECK(day_index >= 0),
 category VARCHAR(16) NOT NULL CHECK(category IN ('ticket','lodging','transport','meal','other')),
 title VARCHAR(160) NOT NULL,
 status VARCHAR(16) NOT NULL CHECK(status IN ('planned','confirmed','cancelled')),
 amount_cents BIGINT CHECK(amount_cents IS NULL OR amount_cents >= 0),
 note VARCHAR(500) NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
 request_key VARCHAR(128),
 fingerprint VARCHAR(64),
 created_by BIGINT NOT NULL REFERENCES users(id),
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE INDEX trip_commitments_trip ON trip_commitments(trip_id,day_index,id);
CREATE UNIQUE INDEX trip_commitments_request ON trip_commitments(trip_id,request_key) WHERE request_key IS NOT NULL;

CREATE TABLE trip_expenses (
 id BIGSERIAL PRIMARY KEY,
 trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
 commitment_id BIGINT REFERENCES trip_commitments(id) ON DELETE SET NULL,
 day_index INTEGER NOT NULL CHECK(day_index >= 0),
 category VARCHAR(16) NOT NULL CHECK(category IN ('ticket','lodging','transport','meal','other')),
 amount_cents BIGINT NOT NULL CHECK(amount_cents > 0),
 note VARCHAR(500) NOT NULL DEFAULT '',
 request_key VARCHAR(128),
 fingerprint VARCHAR(64),
 voided_at TIMESTAMP,
 voided_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
 void_reason VARCHAR(300),
 created_by BIGINT NOT NULL REFERENCES users(id),
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
CREATE INDEX trip_expenses_trip ON trip_expenses(trip_id,day_index,id);
CREATE UNIQUE INDEX trip_expenses_request ON trip_expenses(trip_id,request_key) WHERE request_key IS NOT NULL;

CREATE TABLE trip_members (
 trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 role VARCHAR(8) NOT NULL CHECK(role IN ('viewer','editor')),
 status VARCHAR(8) NOT NULL CHECK(status IN ('pending','accepted')),
 invited_by BIGINT NOT NULL REFERENCES users(id),
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 accepted_at TIMESTAMP,
 PRIMARY KEY(trip_id,user_id)
);
CREATE INDEX trip_members_user ON trip_members(user_id,status,trip_id);

CREATE TABLE trip_notifications (
 id BIGSERIAL PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 trip_id BIGINT REFERENCES trip_records(id) ON DELETE CASCADE,
 event_key VARCHAR(120) NOT NULL,
 kind VARCHAR(24) NOT NULL,
 title VARCHAR(120) NOT NULL,
 message VARCHAR(300) NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 read_at TIMESTAMP,
 UNIQUE(user_id,event_key)
);
CREATE INDEX trip_notifications_user ON trip_notifications(user_id,created_at DESC,id DESC);

CREATE TABLE trip_usage_policies (
 user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 monthly_token_warning_limit BIGINT NOT NULL CHECK(monthly_token_warning_limit BETWEEN 1000 AND 1000000000),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now())
);
