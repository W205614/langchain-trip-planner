ALTER TABLE trip_records ADD COLUMN planning_input JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE trip_records ADD COLUMN planning_revision INTEGER NOT NULL DEFAULT 1 CHECK(planning_revision > 0);
ALTER TABLE trip_commitments ADD COLUMN details_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE trip_commitments ADD COLUMN activity_id VARCHAR(160) NOT NULL DEFAULT '';
ALTER TABLE trip_commitments ADD COLUMN poi_id VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE trip_commitments ADD COLUMN location_json JSONB;
ALTER TABLE trip_commitments ADD COLUMN start_at TIMESTAMPTZ;
ALTER TABLE trip_commitments ADD COLUMN end_at TIMESTAMPTZ;
ALTER TABLE trip_commitments ADD CONSTRAINT commitment_time_order CHECK(end_at IS NULL OR start_at IS NULL OR end_at > start_at);
ALTER TABLE trip_tasks ADD COLUMN resumed_from VARCHAR(36) REFERENCES trip_tasks(id) ON DELETE SET NULL;
CREATE TABLE execution_checkpoints (
 task_id VARCHAR(36) NOT NULL REFERENCES trip_tasks(id) ON DELETE CASCADE,
 execution_id VARCHAR(36) NOT NULL, stage VARCHAR(64) NOT NULL,
 input_hash VARCHAR(64) NOT NULL, payload JSONB NOT NULL, compatibility JSONB NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(task_id,stage)
);
CREATE TABLE outbound_budget_counters (
 scope VARCHAR(180) NOT NULL, kind VARCHAR(16) NOT NULL CHECK(kind IN ('text','embedding','amap')),
 used BIGINT NOT NULL DEFAULT 0 CHECK(used>=0), PRIMARY KEY(scope,kind)
);
CREATE TABLE outbound_reservations (
 id BIGSERIAL PRIMARY KEY, execution_id VARCHAR(36), kind VARCHAR(16) NOT NULL,
 run_id VARCHAR(120) NOT NULL DEFAULT '', request_key VARCHAR(160) NOT NULL,
 reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(), outcome VARCHAR(24) NOT NULL DEFAULT 'unknown',
 UNIQUE(run_id,request_key)
);
-- Reservation/commitment changes invalidate proposals even when plan JSON is unchanged.
CREATE FUNCTION bump_trip_planning_revision() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 UPDATE trip_records SET planning_revision=planning_revision+1 WHERE id=COALESCE(NEW.trip_id,OLD.trip_id);
 RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER commitment_planning_revision AFTER INSERT OR UPDATE OR DELETE ON trip_commitments
 FOR EACH ROW EXECUTE FUNCTION bump_trip_planning_revision();
