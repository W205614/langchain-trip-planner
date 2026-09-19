ALTER TABLE trip_records
  ADD COLUMN departure_city VARCHAR(32) NOT NULL DEFAULT '',
  ADD COLUMN traveler_count INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN room_count INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN budget_total INTEGER;

ALTER TABLE trip_records
  ADD CONSTRAINT ck_trip_record_travelers CHECK(traveler_count BETWEEN 1 AND 20),
  ADD CONSTRAINT ck_trip_record_rooms CHECK(room_count BETWEEN 1 AND 10 AND room_count <= traveler_count),
  ADD CONSTRAINT ck_trip_record_budget CHECK(budget_total IS NULL OR budget_total BETWEEN 100 AND 10000000);

CREATE TABLE community_trip_cards (
  id BIGSERIAL PRIMARY KEY,
  record_id BIGINT,
  owner_id BIGINT NOT NULL,
  record_version INTEGER NOT NULL CHECK(record_version >= 1),
  title VARCHAR(160) NOT NULL,
  city VARCHAR(64) NOT NULL,
  snapshot_json TEXT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','published','rejected')),
  review_note VARCHAR(500) NOT NULL DEFAULT '',
  reviewed_by BIGINT,
  created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
  reviewed_at TIMESTAMP,
  published_at TIMESTAMP,
  CONSTRAINT fk_community_record FOREIGN KEY(record_id) REFERENCES trip_records(id) ON DELETE SET NULL,
  CONSTRAINT fk_community_owner FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_community_reviewer FOREIGN KEY(reviewed_by) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT uq_community_record_version UNIQUE(record_id,record_version)
);
CREATE INDEX community_trip_cards_public ON community_trip_cards(status,published_at DESC,id DESC);
CREATE INDEX community_trip_cards_review ON community_trip_cards(status,created_at,id);
