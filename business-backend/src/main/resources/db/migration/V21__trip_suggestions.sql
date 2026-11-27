ALTER TABLE trip_shares ADD COLUMN allow_suggestions BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE trip_suggestions (
  id BIGSERIAL PRIMARY KEY,
  trip_id BIGINT NOT NULL REFERENCES trip_records(id) ON DELETE CASCADE,
  share_id BIGINT NOT NULL REFERENCES trip_shares(id) ON DELETE CASCADE,
  author_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('shift_time', 'alternative')),
  day_index INTEGER NOT NULL CHECK (day_index >= 0),
  trip_date DATE NOT NULL,
  base_version INTEGER NOT NULL CHECK (base_version >= 1),
  details JSONB NOT NULL,
  reason VARCHAR(500) NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  applied_version INTEGER,
  created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC', now()),
  decided_at TIMESTAMP
);
CREATE INDEX trip_suggestions_trip ON trip_suggestions(trip_id, id DESC);
CREATE INDEX trip_suggestions_author ON trip_suggestions(author_id);
