-- Refuse to hide legacy corruption. Operators must repair invalid rows before retrying Flyway.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM user_travel_preferences p LEFT JOIN users u ON u.id=p.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM trip_records r LEFT JOIN users u ON u.id=r.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM trip_tasks t LEFT JOIN users u ON u.id=t.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM trip_tasks t LEFT JOIN trip_records r ON r.id=t.record_id WHERE t.record_id IS NOT NULL AND r.id IS NULL)
     OR EXISTS (SELECT 1 FROM rag_sync_jobs j LEFT JOIN users u ON u.id=j.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM favorite_pois f LEFT JOIN users u ON u.id=f.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM trip_shares s LEFT JOIN users u ON u.id=s.owner_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM assistant_conversations c LEFT JOIN users u ON u.id=c.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM assistant_conversations c LEFT JOIN trip_records r ON r.id=c.active_trip_id WHERE c.active_trip_id IS NOT NULL AND r.id IS NULL)
     OR EXISTS (SELECT 1 FROM assistant_messages m LEFT JOIN users u ON u.id=m.user_id WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM assistant_messages m LEFT JOIN assistant_conversations c ON c.id=m.conversation_id WHERE c.id IS NULL)
     OR EXISTS (SELECT 1 FROM knowledge_documents d LEFT JOIN users u ON u.id=d.submitted_by WHERE u.id IS NULL)
     OR EXISTS (SELECT 1 FROM knowledge_documents d LEFT JOIN users u ON u.id=d.reviewed_by WHERE d.reviewed_by IS NOT NULL AND u.id IS NULL)
     OR EXISTS (SELECT 1 FROM knowledge_ingest_jobs j LEFT JOIN knowledge_documents d ON d.id=j.document_id WHERE d.id IS NULL)
  THEN
    RAISE EXCEPTION 'V4 refused: orphan business rows exist; repair them before migration';
  END IF;

  IF EXISTS (SELECT 1 FROM trip_tasks WHERE status NOT IN ('queued','running','succeeded','needs_attention','failed','cancelled') OR percent NOT BETWEEN 0 AND 100)
     OR EXISTS (SELECT 1 FROM rag_sync_jobs WHERE status NOT IN ('pending','running','waiting','retry','failed','succeeded') OR operation NOT IN ('upsert','delete'))
     OR EXISTS (SELECT 1 FROM knowledge_documents WHERE status NOT IN ('pending','rejected','queued','processing','awaiting_review','publishing','published','failed','deleted'))
     OR EXISTS (SELECT 1 FROM knowledge_ingest_jobs WHERE status NOT IN ('pending','running','waiting','retry','failed','succeeded'))
     OR EXISTS (SELECT 1 FROM trip_records WHERE travel_days < 1 OR travel_days > 30 OR version < 1)
  THEN
    RAISE EXCEPTION 'V4 refused: invalid status, range, or version values exist';
  END IF;
END $$;

ALTER TABLE user_travel_preferences
  ADD CONSTRAINT fk_preferences_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE trip_records
  ADD CONSTRAINT fk_trip_record_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  ADD CONSTRAINT ck_trip_record_days CHECK(travel_days BETWEEN 1 AND 30),
  ADD CONSTRAINT ck_trip_record_version CHECK(version >= 1),
  ADD CONSTRAINT ck_trip_record_dates CHECK(
    start_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    AND end_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    AND end_date >= start_date);
ALTER TABLE trip_tasks
  ADD CONSTRAINT fk_trip_task_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_trip_task_record FOREIGN KEY(record_id) REFERENCES trip_records(id) ON DELETE SET NULL,
  ADD CONSTRAINT ck_trip_task_status CHECK(status IN ('queued','running','succeeded','needs_attention','failed','cancelled')),
  ADD CONSTRAINT ck_trip_task_percent CHECK(percent BETWEEN 0 AND 100),
  ADD CONSTRAINT ck_trip_task_deadline CHECK(deadline_at > created_at);
ALTER TABLE rag_sync_jobs
  ADD CONSTRAINT fk_rag_sync_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  ADD CONSTRAINT ck_rag_sync_status CHECK(status IN ('pending','running','waiting','retry','failed','succeeded')),
  ADD CONSTRAINT ck_rag_sync_operation CHECK(operation IN ('upsert','delete')),
  ADD CONSTRAINT ck_rag_sync_attempts CHECK(attempts >= 0);
ALTER TABLE knowledge_documents
  ADD CONSTRAINT fk_knowledge_submitter FOREIGN KEY(submitted_by) REFERENCES users(id) ON DELETE RESTRICT,
  ADD CONSTRAINT fk_knowledge_reviewer FOREIGN KEY(reviewed_by) REFERENCES users(id) ON DELETE SET NULL,
  ADD CONSTRAINT ck_knowledge_status CHECK(status IN ('pending','rejected','queued','processing','awaiting_review','publishing','published','failed','deleted')),
  ADD CONSTRAINT ck_knowledge_version CHECK(version >= 1),
  ADD CONSTRAINT ck_knowledge_source_tier CHECK(source_tier IN ('community','reviewed','official'));
ALTER TABLE knowledge_ingest_jobs
  ADD CONSTRAINT fk_ingest_document FOREIGN KEY(document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  ADD CONSTRAINT ck_ingest_status CHECK(status IN ('pending','running','waiting','retry','failed','succeeded')),
  ADD CONSTRAINT ck_ingest_version CHECK(document_version >= 1),
  ADD CONSTRAINT ck_ingest_attempts CHECK(attempts >= 0);
ALTER TABLE favorite_pois
  ADD CONSTRAINT fk_favorite_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  ADD CONSTRAINT ck_favorite_longitude CHECK(longitude BETWEEN -180 AND 180),
  ADD CONSTRAINT ck_favorite_latitude CHECK(latitude BETWEEN -90 AND 90);
ALTER TABLE trip_shares
  ADD CONSTRAINT fk_share_owner FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE,
  ADD CONSTRAINT ck_share_record_version CHECK(record_version >= 1);
ALTER TABLE assistant_conversations
  ADD CONSTRAINT fk_conversation_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_conversation_trip FOREIGN KEY(active_trip_id) REFERENCES trip_records(id) ON DELETE SET NULL;
ALTER TABLE assistant_messages
  ADD CONSTRAINT fk_message_conversation FOREIGN KEY(conversation_id) REFERENCES assistant_conversations(id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_message_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE;

-- record_id deliberately has no FK: an RAG delete tombstone must outlive its deleted trip record.
CREATE INDEX rag_sync_jobs_ready ON rag_sync_jobs(status,next_retry_at,id);
CREATE INDEX knowledge_ingest_jobs_ready ON knowledge_ingest_jobs(status,next_retry_at,id);
CREATE INDEX trip_tasks_owner_created ON trip_tasks(user_id,created_at DESC,id);

CREATE TABLE trip_record_versions (
 id BIGSERIAL PRIMARY KEY,
 record_id BIGINT NOT NULL,
 user_id BIGINT NOT NULL,
 record_version INTEGER NOT NULL CHECK(record_version >= 1),
 change_type VARCHAR(32) NOT NULL CHECK(change_type IN
   ('migration_baseline','manual_create','agent_create','copied_create','user_edit','reverify','agent_revision','assistant_confirm','draft_apply','restore')),
 request_id VARCHAR(64) NOT NULL DEFAULT '',
 title VARCHAR(160) NOT NULL DEFAULT '',
 source VARCHAR(32) NOT NULL,
 plan_json TEXT NOT NULL,
 quality_json TEXT NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 CONSTRAINT fk_trip_version_record FOREIGN KEY(record_id) REFERENCES trip_records(id) ON DELETE CASCADE,
 CONSTRAINT fk_trip_version_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 CONSTRAINT uq_trip_record_version UNIQUE(record_id,record_version)
);
CREATE INDEX trip_record_versions_owner ON trip_record_versions(user_id,record_id,record_version DESC);

CREATE FUNCTION reject_trip_version_update() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'trip_record_versions are immutable';
END $$;
CREATE TRIGGER trip_record_versions_immutable BEFORE UPDATE ON trip_record_versions
 FOR EACH ROW EXECUTE FUNCTION reject_trip_version_update();

INSERT INTO trip_record_versions(
  record_id,user_id,record_version,change_type,request_id,title,source,plan_json,quality_json,created_at)
SELECT id,user_id,version,'migration_baseline','',title,source,plan_json,quality_json,updated_at
FROM trip_records;

CREATE TABLE business_audit_events (
 id BIGSERIAL PRIMARY KEY,
 occurred_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 request_id VARCHAR(64) NOT NULL DEFAULT '',
 actor_type VARCHAR(16) NOT NULL DEFAULT 'user' CHECK(actor_type IN ('user','service','system')),
 actor_id BIGINT,
 action VARCHAR(64) NOT NULL,
 resource_type VARCHAR(32) NOT NULL,
 resource_id VARCHAR(64) NOT NULL,
 resource_version INTEGER,
 outcome VARCHAR(16) NOT NULL DEFAULT 'success' CHECK(outcome IN ('success','failure','denied')),
 metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
 CONSTRAINT fk_audit_actor FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE SET NULL,
 CONSTRAINT ck_audit_metadata_object CHECK(jsonb_typeof(metadata_json)='object')
);
CREATE INDEX business_audit_resource ON business_audit_events(resource_type,resource_id,occurred_at DESC);
CREATE INDEX business_audit_actor ON business_audit_events(actor_id,occurred_at DESC);
