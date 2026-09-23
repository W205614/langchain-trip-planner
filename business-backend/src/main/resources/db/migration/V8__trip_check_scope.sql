ALTER TABLE trip_checks ADD COLUMN day_index INTEGER CHECK(day_index IS NULL OR day_index >= 0);
CREATE INDEX trip_checks_scope ON trip_checks(trip_id,day_index,checked_at DESC,id DESC);
