CREATE TABLE outbound_operations (
 id UUID PRIMARY KEY,
 actor_kind VARCHAR(12) NOT NULL CHECK(actor_kind IN ('user','system','anonymous')),
 actor_id BIGINT REFERENCES users(id),
 purpose VARCHAR(32) NOT NULL,
 expires_at TIMESTAMP NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT timezone('UTC',now()),
 CHECK((actor_kind='user')=(actor_id IS NOT NULL))
);
CREATE INDEX outbound_operations_expiry ON outbound_operations(expires_at);
