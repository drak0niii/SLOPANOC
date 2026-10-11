CREATE TABLE observability_agent_projection (
    environment VARCHAR(16) NOT NULL, 
    agent VARCHAR(64) NOT NULL, 
    model_attempts_started BIGINT NOT NULL, 
    model_attempts_completed BIGINT NOT NULL, 
    usage_records_captured BIGINT NOT NULL, 
    latest_event_occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    latest_event_processed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    projection_lag FLOAT NOT NULL, 
    PRIMARY KEY (environment, agent), 
    CHECK (model_attempts_started >= 0 AND model_attempts_completed >= 0 AND usage_records_captured >= 0 AND projection_lag >= 0)
);

CREATE TABLE observability_critical_audit (
    event_id VARCHAR(36) NOT NULL, 
    event_schema_version BIGINT NOT NULL, 
    event_class VARCHAR(16) NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    aggregate_type VARCHAR(32) NOT NULL, 
    aggregate_id VARCHAR(128) NOT NULL, 
    aggregate_version BIGINT NOT NULL, 
    run_id VARCHAR(36), 
    turn_id VARCHAR(128), 
    trace_id VARCHAR(32), 
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    producer VARCHAR(32) NOT NULL, 
    safe_payload JSON NOT NULL, 
    payload_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (event_id), 
    CHECK (event_schema_version = 1 AND event_class = 'AUDIT' AND aggregate_version >= 1)
);

CREATE INDEX ix_hybrid_audit_aggregate ON observability_critical_audit (environment, aggregate_type, aggregate_id, aggregate_version);

CREATE TABLE observability_projection_failure (
    environment VARCHAR(16) NOT NULL, 
    failure_key VARCHAR(64) NOT NULL, 
    failure_code VARCHAR(32) NOT NULL, 
    processing_state VARCHAR(16) NOT NULL, 
    attempts BIGINT NOT NULL, 
    last_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (environment, failure_key), 
    CHECK (failure_code IN ('INVALID','CONFLICT','PROCESSING_FAILED') AND processing_state IN ('RETRY','DEAD','RESOLVED') AND attempts >= 1)
);

CREATE TABLE observability_projection_receipt (
    environment VARCHAR(16) NOT NULL, 
    event_id VARCHAR(36) NOT NULL, 
    payload_hash VARCHAR(64) NOT NULL, 
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    processed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    duplicate_count BIGINT NOT NULL, 
    PRIMARY KEY (environment, event_id), 
    CHECK (duplicate_count >= 0)
);

CREATE INDEX ix_hybrid_receipt_recent ON observability_projection_receipt (environment, processed_at);

CREATE TABLE observability_run_projection (
    environment VARCHAR(16) NOT NULL, 
    run_id VARCHAR(36) NOT NULL, 
    aggregate_version BIGINT NOT NULL, 
    turn_id VARCHAR(128), 
    trace_id VARCHAR(32), 
    status VARCHAR(16) NOT NULL, 
    latest_event_occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    latest_event_processed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    projection_lag FLOAT NOT NULL, 
    PRIMARY KEY (environment, run_id), 
    CHECK (aggregate_version >= 1 AND projection_lag >= 0), 
    CHECK (status IN ('RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED'))
);

CREATE INDEX ix_hybrid_run_recent ON observability_run_projection (environment, latest_event_occurred_at, run_id);

CREATE TABLE observability_audit_outbox (
    event_id VARCHAR(36) NOT NULL, 
    publication_state VARCHAR(16) NOT NULL, 
    attempt_count BIGINT NOT NULL, 
    available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    lease_owner VARCHAR(36), 
    lease_expires_at TIMESTAMP WITH TIME ZONE, 
    published_at TIMESTAMP WITH TIME ZONE, 
    last_failure_code VARCHAR(32), 
    event_schema_version BIGINT NOT NULL, 
    event_class VARCHAR(16) NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    aggregate_type VARCHAR(32) NOT NULL, 
    aggregate_id VARCHAR(128) NOT NULL, 
    aggregate_version BIGINT NOT NULL, 
    run_id VARCHAR(36), 
    turn_id VARCHAR(128), 
    trace_id VARCHAR(32), 
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    producer VARCHAR(32) NOT NULL, 
    safe_payload JSON NOT NULL, 
    payload_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (event_id), 
    CHECK ((publication_state = 'PUBLISHED' AND published_at IS NOT NULL) OR (publication_state <> 'PUBLISHED' AND published_at IS NULL)), 
    CHECK (publication_state IN ('PENDING','LEASED','RETRYING','DEAD','PUBLISHED')), 
    CHECK ((publication_state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR (publication_state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)), 
    CHECK (event_schema_version = 1 AND event_class = 'AUDIT' AND aggregate_version >= 1 AND attempt_count >= 0), 
    FOREIGN KEY(event_id) REFERENCES observability_critical_audit (event_id)
);

CREATE INDEX ix_hybrid_outbox_available ON observability_audit_outbox (environment, publication_state, available_at, event_id);

CREATE INDEX ix_hybrid_outbox_oldest ON observability_audit_outbox (environment, created_at, publication_state);

