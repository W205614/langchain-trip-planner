-- Preserve operation on retry: index failures must not re-run vision.
ALTER TABLE knowledge_ingest_jobs
    ADD COLUMN phase VARCHAR(16) NOT NULL DEFAULT 'auto'
    CHECK (phase IN ('auto', 'parse', 'publish', 'delete'));
