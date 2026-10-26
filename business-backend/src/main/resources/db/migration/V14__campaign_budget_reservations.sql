-- Campaign counters are monotonic pre-send reservations, never billing records.
CREATE TABLE outbound_campaigns (
 id VARCHAR(120) PRIMARY KEY,
 text_limit INTEGER NOT NULL CHECK(text_limit BETWEEN 1 AND 12),
 embedding_limit INTEGER NOT NULL CHECK(embedding_limit BETWEEN 1 AND 40),
 amap_limit INTEGER NOT NULL CHECK(amap_limit BETWEEN 1 AND 120),
 token_limit BIGINT NOT NULL CHECK(token_limit BETWEEN 1 AND 800000),
 text_used BIGINT NOT NULL DEFAULT 0 CHECK(text_used>=0),
 embedding_used BIGINT NOT NULL DEFAULT 0 CHECK(embedding_used>=0),
 amap_used BIGINT NOT NULL DEFAULT 0 CHECK(amap_used>=0),
 reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Changing an environment variable must not silently create a fresh budget.
CREATE TABLE outbound_campaign_binding (
 singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
 campaign_id VARCHAR(120) NOT NULL REFERENCES outbound_campaigns(id),
 bound_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE outbound_campaign_workflows (
 campaign_id VARCHAR(120) NOT NULL REFERENCES outbound_campaigns(id),
 root_task_id VARCHAR(36) NOT NULL REFERENCES trip_tasks(id),
 sample_fingerprint VARCHAR(128) NOT NULL,
 phase VARCHAR(16) NOT NULL CHECK(phase IN ('development','acceptance')),
 reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(campaign_id,root_task_id),
 UNIQUE(campaign_id,sample_fingerprint)
);
CREATE TABLE outbound_campaign_samples (
 campaign_id VARCHAR(120) NOT NULL REFERENCES outbound_campaigns(id),
 sample_id VARCHAR(64) NOT NULL,
 sample_fingerprint VARCHAR(128) NOT NULL,
 phase VARCHAR(16) NOT NULL CHECK(phase IN ('development','acceptance')),
 registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(campaign_id,sample_fingerprint),
 UNIQUE(campaign_id,sample_id)
);
ALTER TABLE outbound_reservations ADD COLUMN reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0);
