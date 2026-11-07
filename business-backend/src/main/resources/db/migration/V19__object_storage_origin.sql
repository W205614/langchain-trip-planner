ALTER TABLE knowledge_documents ADD COLUMN storage_origin VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE upload_reservations ADD COLUMN storage_origin VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE storage_migrations ADD COLUMN source_origin VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE storage_migrations ADD COLUMN target_origin VARCHAR(64) NOT NULL DEFAULT '';
