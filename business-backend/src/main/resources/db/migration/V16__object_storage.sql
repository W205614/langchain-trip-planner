ALTER TABLE knowledge_documents
 ADD COLUMN storage_backend VARCHAR(8) NOT NULL DEFAULT 'local' CHECK(storage_backend IN ('local','s3')),
 ADD COLUMN storage_bucket VARCHAR(255) NOT NULL DEFAULT '',
 ADD COLUMN storage_key VARCHAR(1024) NOT NULL DEFAULT '';
UPDATE knowledge_documents SET storage_key=regexp_replace(replace(stored_path,chr(92),'/'),'^.*/','');
ALTER TABLE knowledge_documents ADD COLUMN storage_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE upload_reservations
 ADD COLUMN storage_backend VARCHAR(8) NOT NULL DEFAULT 'local' CHECK(storage_backend IN ('local','s3')),
 ADD COLUMN storage_bucket VARCHAR(255) NOT NULL DEFAULT '',
 ADD COLUMN storage_key VARCHAR(1024) NOT NULL DEFAULT '';
UPDATE upload_reservations SET storage_key=stored_path;
ALTER TABLE upload_reservations ADD COLUMN write_started_at TIMESTAMP;
CREATE TABLE storage_migrations (
 document_id BIGINT NOT NULL REFERENCES knowledge_documents(id),
 target_backend VARCHAR(8) NOT NULL CHECK(target_backend IN ('local','s3')),
 target_bucket VARCHAR(255) NOT NULL DEFAULT '', target_key VARCHAR(1024) NOT NULL,
 source_backend VARCHAR(8) NOT NULL, source_bucket VARCHAR(255) NOT NULL, source_key VARCHAR(1024) NOT NULL,
 source_version INTEGER NOT NULL, sha256 VARCHAR(64) NOT NULL, size_bytes BIGINT NOT NULL,
 status VARCHAR(16) NOT NULL CHECK(status IN ('copying','verified','switched','conflict')),
 updated_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 PRIMARY KEY(document_id,target_backend,target_bucket,target_key)
);
