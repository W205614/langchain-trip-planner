CREATE TABLE IF NOT EXISTS deployment_identity (
 id INTEGER PRIMARY KEY CHECK(id=1), dataset_id VARCHAR(64) NOT NULL,
 enrolled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE operational_heartbeats (
 name VARCHAR(64) PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL,
 details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX execution_checkpoints_expiry ON execution_checkpoints(expires_at);
CREATE INDEX rag_sync_jobs_live ON rag_sync_jobs(created_at) WHERE status<>'succeeded';
CREATE INDEX knowledge_ingest_jobs_live ON knowledge_ingest_jobs(created_at) WHERE status<>'succeeded';
CREATE INDEX trip_tasks_terminal_age ON trip_tasks(updated_at) WHERE status IN ('succeeded','needs_attention','failed','cancelled');
