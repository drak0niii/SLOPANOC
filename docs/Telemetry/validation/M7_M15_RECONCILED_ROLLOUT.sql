BEGIN;

-- Running upgrade 3e59584b1012 -> 7c4e9a2d1f05

CREATE TABLE slopanoc_case_troubleshooting_progressions (
    case_id VARCHAR NOT NULL, 
    version INTEGER NOT NULL, 
    schema_version TEXT NOT NULL, 
    progression JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_by_session_id TEXT, 
    PRIMARY KEY (case_id), 
    FOREIGN KEY(case_id) REFERENCES slopanoc_cases (case_id), 
    CONSTRAINT ck_slopanoc_case_troubleshooting_progressions_version_positive CHECK (version >= 1)
);

INSERT INTO alembic_version (version_num) VALUES ('7c4e9a2d1f05') RETURNING alembic_version.version_num;

-- Running upgrade 7c4e9a2d1f05 -> 9f71c2a64e08

CREATE TABLE observability_run_event (
    run_id VARCHAR(36) NOT NULL, 
    event_seq BIGINT NOT NULL, 
    state_version BIGINT NOT NULL, 
    event_type VARCHAR(16) NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    elapsed_ms FLOAT NOT NULL, 
    stage VARCHAR(64) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    agent VARCHAR(64), 
    tool VARCHAR(64), 
    dependency VARCHAR(64), 
    error_code VARCHAR(64), 
    PRIMARY KEY (run_id, event_seq), 
    CHECK (event_seq >= 0 AND state_version >= 0 AND elapsed_ms >= 0), 
    CHECK (event_type IN ('started','stage','agent','tool','dependency','stalled','resumed','terminal','cleanup','delivery','identity','deadline')), 
    CHECK (stage IN ('request.received','request.validated','session.load.started','session.load.completed','attachments.started','attachments.completed','planning.started','planning.model.started','planning.model.completed','planning.completed','planning.failed','thread.resolve.started','thread.resolve.completed','pending_interaction.resolve.started','pending_interaction.resolve.completed','source_requirements.completed','context.selection.started','context.selection.completed','context.item.selected','context.item.excluded','agent.started','agent.completed','agent.failed','agent.team_manager','agent.technical_authority','agent.incident_manager','agent.problem_manager','agent.automated_operations','model.request.started','model.first_token','model.request.completed','model.request.failed','model.request.timeout','tool.started','tool.completed','tool.failed','tool.timeout','knowledge.search.started','knowledge.search.completed','teams.list_chats.started','teams.list_chats.completed','teams.read_messages.started','teams.read_messages.completed','database.query.started','database.query.completed','storage.operation.started','storage.operation.completed','knowledge.selection.completed','procedure_action.resolved','command_authority.completed','approval.requested','approval.completed','authority.selected','provenance.completed','command_egress.completed','synthesis.started','synthesis.completed','persistence.started','persistence.completed','sse.started','sse.completed','turn.completed','turn.failed','turn.timeout','turn.cancelled','run.stalled','heartbeat')), 
    CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED','STALLED'))
);

CREATE TABLE observability_run_status (
    current_span_id VARCHAR(16), 
    current_agent VARCHAR(64), 
    current_tool VARCHAR(64), 
    dependencies JSON NOT NULL, 
    stage_started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_progress_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    work_deadline_at TIMESTAMP WITH TIME ZONE, 
    total_deadline_at TIMESTAMP WITH TIME ZONE, 
    stalled BOOLEAN NOT NULL, 
    stalled_at TIMESTAMP WITH TIME ZONE, 
    run_id VARCHAR(36) NOT NULL, 
    producer_instance_id VARCHAR(36) NOT NULL, 
    state_version BIGINT NOT NULL, 
    session_detached BOOLEAN DEFAULT false NOT NULL, 
    session_id VARCHAR(128), 
    turn_id VARCHAR(128), 
    turn_id_origin VARCHAR(16), 
    trace_id VARCHAR(32), 
    root_span_id VARCHAR(16), 
    environment VARCHAR(64) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    current_stage VARCHAR(64) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    terminal_at TIMESTAMP WITH TIME ZONE, 
    elapsed_ms FLOAT NOT NULL, 
    error_code VARCHAR(64), 
    error_stage VARCHAR(64), 
    cleanup_status VARCHAR(16) NOT NULL, 
    delivery_status VARCHAR(16) NOT NULL, 
    service_version VARCHAR(64), 
    git_sha VARCHAR(64), 
    release_id VARCHAR(64), 
    revision VARCHAR(64), 
    region VARCHAR(64), 
    config_version VARCHAR(16), 
    telemetry_schema_version INTEGER NOT NULL, 
    projection_schema_version INTEGER NOT NULL, 
    retention_class VARCHAR(16) NOT NULL, 
    ever_stalled BOOLEAN NOT NULL, 
    timeline_truncated_count BIGINT NOT NULL, 
    expiry_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (run_id), 
    CHECK ((status IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED') AND terminal_at IS NOT NULL) OR (status IN ('PENDING','RUNNING','STALLED') AND terminal_at IS NULL)), 
    CHECK (cleanup_status IN ('pending','running','completed','failed','timeout')), 
    CHECK (current_stage IN ('request.received','request.validated','session.load.started','session.load.completed','attachments.started','attachments.completed','planning.started','planning.model.started','planning.model.completed','planning.completed','planning.failed','thread.resolve.started','thread.resolve.completed','pending_interaction.resolve.started','pending_interaction.resolve.completed','source_requirements.completed','context.selection.started','context.selection.completed','context.item.selected','context.item.excluded','agent.started','agent.completed','agent.failed','agent.team_manager','agent.technical_authority','agent.incident_manager','agent.problem_manager','agent.automated_operations','model.request.started','model.first_token','model.request.completed','model.request.failed','model.request.timeout','tool.started','tool.completed','tool.failed','tool.timeout','knowledge.search.started','knowledge.search.completed','teams.list_chats.started','teams.list_chats.completed','teams.read_messages.started','teams.read_messages.completed','database.query.started','database.query.completed','storage.operation.started','storage.operation.completed','knowledge.selection.completed','procedure_action.resolved','command_authority.completed','approval.requested','approval.completed','authority.selected','provenance.completed','command_egress.completed','synthesis.started','synthesis.completed','persistence.started','persistence.completed','sse.started','sse.completed','turn.completed','turn.failed','turn.timeout','turn.cancelled','run.stalled','heartbeat')), 
    CHECK (delivery_status IN ('pending','relayed','disconnected')), 
    CHECK (retention_class IN ('normal','exceptional')), 
    CHECK (state_version >= 0 AND elapsed_ms >= 0 AND timeline_truncated_count >= 0), 
    CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED','STALLED')), 
    CHECK (status NOT IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED') OR (current_agent IS NULL AND current_tool IS NULL)), 
    CHECK (telemetry_schema_version = 1 AND projection_schema_version = 1)
);

CREATE INDEX ix_observability_active ON observability_run_status (environment, status, heartbeat_at, run_id);

CREATE INDEX ix_observability_session ON observability_run_status (session_id, started_at, run_id);

CREATE INDEX ix_observability_status_expiry ON observability_run_status (expiry_at, run_id);

CREATE TABLE observability_turn (
    run_id VARCHAR(36) NOT NULL, 
    producer_instance_id VARCHAR(36) NOT NULL, 
    state_version BIGINT NOT NULL, 
    session_detached BOOLEAN DEFAULT false NOT NULL, 
    session_id VARCHAR(128), 
    turn_id VARCHAR(128), 
    turn_id_origin VARCHAR(16), 
    trace_id VARCHAR(32), 
    root_span_id VARCHAR(16), 
    environment VARCHAR(64) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    current_stage VARCHAR(64) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    terminal_at TIMESTAMP WITH TIME ZONE, 
    elapsed_ms FLOAT NOT NULL, 
    error_code VARCHAR(64), 
    error_stage VARCHAR(64), 
    cleanup_status VARCHAR(16) NOT NULL, 
    delivery_status VARCHAR(16) NOT NULL, 
    service_version VARCHAR(64), 
    git_sha VARCHAR(64), 
    release_id VARCHAR(64), 
    revision VARCHAR(64), 
    region VARCHAR(64), 
    config_version VARCHAR(16), 
    telemetry_schema_version INTEGER NOT NULL, 
    projection_schema_version INTEGER NOT NULL, 
    retention_class VARCHAR(16) NOT NULL, 
    ever_stalled BOOLEAN NOT NULL, 
    timeline_truncated_count BIGINT NOT NULL, 
    expiry_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (run_id), 
    CHECK ((status IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED') AND terminal_at IS NOT NULL) OR (status IN ('PENDING','RUNNING','STALLED') AND terminal_at IS NULL)), 
    CHECK (cleanup_status IN ('pending','running','completed','failed','timeout')), 
    CHECK (current_stage IN ('request.received','request.validated','session.load.started','session.load.completed','attachments.started','attachments.completed','planning.started','planning.model.started','planning.model.completed','planning.completed','planning.failed','thread.resolve.started','thread.resolve.completed','pending_interaction.resolve.started','pending_interaction.resolve.completed','source_requirements.completed','context.selection.started','context.selection.completed','context.item.selected','context.item.excluded','agent.started','agent.completed','agent.failed','agent.team_manager','agent.technical_authority','agent.incident_manager','agent.problem_manager','agent.automated_operations','model.request.started','model.first_token','model.request.completed','model.request.failed','model.request.timeout','tool.started','tool.completed','tool.failed','tool.timeout','knowledge.search.started','knowledge.search.completed','teams.list_chats.started','teams.list_chats.completed','teams.read_messages.started','teams.read_messages.completed','database.query.started','database.query.completed','storage.operation.started','storage.operation.completed','knowledge.selection.completed','procedure_action.resolved','command_authority.completed','approval.requested','approval.completed','authority.selected','provenance.completed','command_egress.completed','synthesis.started','synthesis.completed','persistence.started','persistence.completed','sse.started','sse.completed','turn.completed','turn.failed','turn.timeout','turn.cancelled','run.stalled','heartbeat')), 
    CHECK (delivery_status IN ('pending','relayed','disconnected')), 
    CHECK (retention_class IN ('normal','exceptional')), 
    CHECK (state_version >= 0 AND elapsed_ms >= 0 AND timeline_truncated_count >= 0), 
    CHECK (status IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED')), 
    CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED','STALLED')), 
    CHECK (telemetry_schema_version = 1 AND projection_schema_version = 1)
);

CREATE INDEX ix_observability_recent ON observability_turn (environment, started_at, run_id);

CREATE INDEX ix_observability_summary_expiry ON observability_turn (expiry_at, run_id);

CREATE INDEX ix_observability_summary_session ON observability_turn (session_id, started_at, run_id);

CREATE INDEX ix_observability_terminal ON observability_turn (environment, terminal_at, run_id);

UPDATE alembic_version SET version_num='9f71c2a64e08' WHERE alembic_version.version_num = '7c4e9a2d1f05';

-- Running upgrade 9f71c2a64e08 -> b37e90a14c62

CREATE TABLE observability_sre_receipt (
    run_id VARCHAR(36) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    payload JSON NOT NULL, 
    contribution JSON NOT NULL, 
    settled BOOLEAN NOT NULL, 
    PRIMARY KEY (run_id)
);

CREATE INDEX ix_sre_due ON observability_sre_receipt (settled, due_at);

CREATE TABLE observability_sre_bucket (
    environment VARCHAR(16) NOT NULL, 
    slo_id VARCHAR(64) NOT NULL, 
    at TIMESTAMP WITH TIME ZONE NOT NULL, 
    good BIGINT NOT NULL, 
    bad BIGINT NOT NULL, 
    unknown BIGINT NOT NULL, 
    excluded BIGINT NOT NULL, 
    PRIMARY KEY (environment, slo_id, at)
);

CREATE TABLE observability_sre_source (
    environment VARCHAR(16) NOT NULL, 
    coverage_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    capture_complete BOOLEAN NOT NULL, 
    PRIMARY KEY (environment)
);

UPDATE alembic_version SET version_num='b37e90a14c62' WHERE alembic_version.version_num = '9f71c2a64e08';

-- Running upgrade b37e90a14c62 -> c10a8f6e2d41

CREATE TABLE finops_accounting_source (
    environment VARCHAR(16) NOT NULL, 
    coverage_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_observation TIMESTAMP WITH TIME ZONE, 
    last_persistence TIMESTAMP WITH TIME ZONE, 
    admission_failures BIGINT NOT NULL, 
    PRIMARY KEY (environment)
);

CREATE TABLE finops_runtime_attempt (
    environment VARCHAR(16) NOT NULL, 
    attempt_id VARCHAR(36) NOT NULL, 
    identity JSON NOT NULL, 
    state VARCHAR(32) NOT NULL, 
    admitted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    provider_finished_at TIMESTAMP WITH TIME ZONE, 
    deadline_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    captured_at TIMESTAMP WITH TIME ZONE, 
    gap_reason VARCHAR(32), 
    PRIMARY KEY (environment, attempt_id), 
    CHECK (environment IN ('local','development','staging','production')), 
    CHECK (state IN ('ADMITTED','STARTED','PROVIDER_FINISHED','USAGE_CAPTURED','NOT_STARTED','FINAL_CAPTURE_PENDING','FINAL_CAPTURE_FAILED','OUTCOME_UNKNOWN','CONFLICT'))
);

CREATE INDEX ix_finops_attempt_debt ON finops_runtime_attempt (environment, state, deadline_at);

CREATE TABLE finops_usage_bucket (
    environment VARCHAR(16) NOT NULL, 
    at TIMESTAMP WITH TIME ZONE NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    model VARCHAR(128) NOT NULL, 
    agent VARCHAR(64) NOT NULL, 
    workload VARCHAR(32) NOT NULL, 
    operation VARCHAR(64) NOT NULL, 
    operation_type VARCHAR(16) NOT NULL, 
    good BIGINT NOT NULL, 
    bad BIGINT NOT NULL, 
    quantity_known BIGINT NOT NULL, 
    quantity_partial BIGINT NOT NULL, 
    input_tokens BIGINT NOT NULL, 
    input_tokens_known BIGINT NOT NULL, 
    output_tokens BIGINT NOT NULL, 
    output_tokens_known BIGINT NOT NULL, 
    candidate_tokens BIGINT NOT NULL, 
    candidate_tokens_known BIGINT NOT NULL, 
    cached_tokens BIGINT NOT NULL, 
    cached_tokens_known BIGINT NOT NULL, 
    total_tokens BIGINT NOT NULL, 
    total_tokens_known BIGINT NOT NULL, 
    thought_tokens BIGINT NOT NULL, 
    thought_tokens_known BIGINT NOT NULL, 
    tool_input_tokens BIGINT NOT NULL, 
    tool_input_tokens_known BIGINT NOT NULL, 
    billable_characters BIGINT NOT NULL, 
    billable_characters_known BIGINT NOT NULL, 
    PRIMARY KEY (environment, at, provider, model, agent, workload, operation, operation_type), 
    CHECK (good >= 0 AND bad >= 0 AND quantity_known >= 0 AND quantity_partial >= 0)
);

CREATE INDEX ix_finops_bucket_time ON finops_usage_bucket (environment, at);

CREATE TABLE ai_usage_ledger (
    event_id VARCHAR(36) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    attempt_id VARCHAR(36) NOT NULL, 
    payload_hash VARCHAR(64) NOT NULL, 
    record_kind VARCHAR(16) NOT NULL, 
    original_event_id VARCHAR(36), 
    correction_reason VARCHAR(32), 
    correction_process VARCHAR(32), 
    metadata_safe JSON NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    input_tokens BIGINT, 
    output_tokens BIGINT, 
    candidate_tokens BIGINT, 
    cached_tokens BIGINT, 
    total_tokens BIGINT, 
    thought_tokens BIGINT, 
    tool_input_tokens BIGINT, 
    billable_characters BIGINT, 
    PRIMARY KEY (event_id), 
    CHECK (record_kind IN ('BASE','ADJUSTMENT')), 
    CHECK (environment IN ('local','development','staging','production')), 
    CHECK (record_kind = 'ADJUSTMENT' OR input_tokens IS NULL OR input_tokens >= 0), 
    CHECK ((record_kind = 'BASE' AND original_event_id IS NULL) OR (record_kind = 'ADJUSTMENT' AND original_event_id IS NOT NULL)), 
    CHECK (record_kind = 'ADJUSTMENT' OR output_tokens IS NULL OR output_tokens >= 0), 
    CHECK (record_kind = 'ADJUSTMENT' OR candidate_tokens IS NULL OR candidate_tokens >= 0), 
    CHECK (record_kind = 'ADJUSTMENT' OR cached_tokens IS NULL OR cached_tokens >= 0), 
    CHECK (record_kind = 'ADJUSTMENT' OR total_tokens IS NULL OR total_tokens >= 0), 
    CHECK (record_kind = 'ADJUSTMENT' OR tool_input_tokens IS NULL OR tool_input_tokens >= 0), 
    CHECK (record_kind = 'ADJUSTMENT' OR thought_tokens IS NULL OR thought_tokens >= 0), 
    CHECK (record_kind = 'ADJUSTMENT' OR billable_characters IS NULL OR billable_characters >= 0), 
    FOREIGN KEY(environment, attempt_id) REFERENCES finops_runtime_attempt (environment, attempt_id), 
    FOREIGN KEY(original_event_id) REFERENCES ai_usage_ledger (event_id)
);

CREATE INDEX ix_finops_ledger_time ON ai_usage_ledger (environment, observed_at, event_id);

CREATE UNIQUE INDEX ux_finops_base_attempt ON ai_usage_ledger (environment, attempt_id) WHERE record_kind = 'BASE';

CREATE TABLE finops_usage_inbox (
    event_id VARCHAR(36) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    attempt_id VARCHAR(36) NOT NULL, 
    payload JSON NOT NULL, 
    payload_hash VARCHAR(64) NOT NULL, 
    delivered BOOLEAN NOT NULL, 
    attempts BIGINT NOT NULL, 
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    delivered_at TIMESTAMP WITH TIME ZONE, 
    exhausted BOOLEAN NOT NULL, 
    PRIMARY KEY (event_id), 
    CHECK (attempts >= 0), 
    FOREIGN KEY(environment, attempt_id) REFERENCES finops_runtime_attempt (environment, attempt_id)
);

CREATE INDEX ix_finops_inbox_pending ON finops_usage_inbox (delivered, exhausted, next_attempt_at);

UPDATE alembic_version SET version_num='c10a8f6e2d41' WHERE alembic_version.version_num = 'b37e90a14c62';

-- Running upgrade c10a8f6e2d41 -> d11b4a8c9e32

CREATE TABLE finops_billing_publication (
    generation VARCHAR(64) NOT NULL, 
    state_key VARCHAR(64) NOT NULL, 
    fingerprint VARCHAR(64) NOT NULL, 
    schema_version BIGINT NOT NULL, 
    mapping_version VARCHAR(32) NOT NULL, 
    window_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    window_end TIMESTAMP WITH TIME ZONE NOT NULL, 
    extracted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    export_time TIMESTAMP WITH TIME ZONE, 
    price_from TIMESTAMP WITH TIME ZONE, 
    price_to TIMESTAMP WITH TIME ZONE, 
    row_count BIGINT NOT NULL, 
    late_rows BIGINT NOT NULL, 
    correction_rows BIGINT NOT NULL, 
    unmapped_rows BIGINT NOT NULL, 
    evidence_rows BIGINT NOT NULL, 
    controls JSON NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    PRIMARY KEY (generation), 
    CHECK (status = 'PUBLISHED'), 
    CHECK (row_count >= 0 AND late_rows >= 0 AND correction_rows >= 0 AND unmapped_rows >= 0), 
    CHECK (schema_version = 1)
);

CREATE INDEX ix_billing_publication_history ON finops_billing_publication (state_key, published_at);

CREATE TABLE finops_billing_ingest_state (
    key VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    partition VARCHAR(10) NOT NULL, 
    mode VARCHAR(16) NOT NULL, 
    revision BIGINT NOT NULL, 
    generation VARCHAR(64), 
    last_success TIMESTAMP WITH TIME ZONE, 
    last_attempt TIMESTAMP WITH TIME ZONE, 
    error VARCHAR(32), 
    PRIMARY KEY (key), 
    CHECK (environment IN ('local','development','staging','production')), 
    CHECK (revision >= 0), 
    CHECK (mode IN ('TEST_FIXTURE','BIGQUERY')), 
    CHECK (source IN ('DETAILED_BILLING','PRICING_EXPORT','FOCUS')), 
    FOREIGN KEY(generation) REFERENCES finops_billing_publication (generation)
);

CREATE UNIQUE INDEX ix_billing_state_scope ON finops_billing_ingest_state (environment, source, scope, partition);

CREATE TABLE finops_billing_projection_cache (
    generation VARCHAR(64) NOT NULL, 
    summaries JSON NOT NULL, 
    prices JSON NOT NULL, 
    PRIMARY KEY (generation), 
    FOREIGN KEY(generation) REFERENCES finops_billing_publication (generation)
);

UPDATE alembic_version SET version_num='d11b4a8c9e32' WHERE alembic_version.version_num = 'c10a8f6e2d41';

-- Running upgrade d11b4a8c9e32 -> e12c6d8a4b50

CREATE TABLE finops_reconciliation_input (
    identity VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    period VARCHAR(10) NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    source_generation VARCHAR(64), 
    body JSON NOT NULL, 
    PRIMARY KEY (identity), 
    CHECK (environment IN ('local','development','staging','production')), 
    CHECK (kind IN ('BILLING','RUNTIME','PRICING'))
);

CREATE INDEX ix_reconciliation_inputs ON finops_reconciliation_input (environment, scope, period, kind, source_generation);

CREATE TABLE finops_reconciliation_registry (
    identity VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    version VARCHAR(32) NOT NULL, 
    ownership_version VARCHAR(32) NOT NULL, 
    body JSON NOT NULL, 
    PRIMARY KEY (identity), 
    CHECK (environment IN ('local','development','staging','production'))
);

CREATE UNIQUE INDEX ux_reconciliation_registry_version ON finops_reconciliation_registry (environment, version, ownership_version);

CREATE TABLE finops_reconciliation_generation (
    identity VARCHAR(64) NOT NULL, 
    registry VARCHAR(64) NOT NULL, 
    runtime_input VARCHAR(64) NOT NULL, 
    billing_input VARCHAR(64) NOT NULL, 
    pricing_input VARCHAR(64) NOT NULL, 
    body JSON NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (identity), 
    FOREIGN KEY(registry) REFERENCES finops_reconciliation_registry (identity), 
    FOREIGN KEY(billing_input) REFERENCES finops_reconciliation_input (identity), 
    FOREIGN KEY(pricing_input) REFERENCES finops_reconciliation_input (identity), 
    FOREIGN KEY(runtime_input) REFERENCES finops_reconciliation_input (identity)
);

CREATE TABLE finops_reconciliation_state (
    key VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    period VARCHAR(10) NOT NULL, 
    revision BIGINT NOT NULL, 
    generation VARCHAR(64), 
    last_attempt TIMESTAMP WITH TIME ZONE, 
    last_success TIMESTAMP WITH TIME ZONE, 
    error VARCHAR(32), 
    PRIMARY KEY (key), 
    CHECK (environment IN ('local','development','staging','production')), 
    CHECK (revision >= 0), 
    FOREIGN KEY(generation) REFERENCES finops_reconciliation_generation (identity)
);

CREATE UNIQUE INDEX ux_reconciliation_period ON finops_reconciliation_state (environment, scope, period);

UPDATE alembic_version SET version_num='e12c6d8a4b50' WHERE alembic_version.version_num = 'd11b4a8c9e32';

-- Running upgrade e12c6d8a4b50 -> f13a7c9d2e61

CREATE TABLE finops_unit_definition_version (
    identity VARCHAR(64) NOT NULL, 
    version VARCHAR(32) NOT NULL, 
    body JSON NOT NULL, 
    PRIMARY KEY (identity), 
    UNIQUE (version)
);

CREATE TABLE finops_unit_denominator_snapshot (
    identity VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    period VARCHAR(10) NOT NULL, 
    source VARCHAR(8) NOT NULL, 
    body JSON NOT NULL, 
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (identity), 
    CHECK (source IN ('M9','M10'))
);

CREATE INDEX ix_unit_snapshot_period ON finops_unit_denominator_snapshot (environment, period, source);

CREATE TABLE finops_unit_driver_version (
    identity VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    version VARCHAR(32) NOT NULL, 
    body JSON NOT NULL, 
    PRIMARY KEY (identity)
);

CREATE UNIQUE INDEX ux_unit_driver_version ON finops_unit_driver_version (environment, scope, version);

CREATE TABLE finops_unit_economics_generation (
    identity VARCHAR(64) NOT NULL, 
    definitions VARCHAR(64) NOT NULL, 
    drivers VARCHAR(64) NOT NULL, 
    reconciliation VARCHAR(64) NOT NULL, 
    turn_snapshot VARCHAR(64), 
    model_snapshot VARCHAR(64), 
    body JSON NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (identity), 
    FOREIGN KEY(definitions) REFERENCES finops_unit_definition_version (identity), 
    FOREIGN KEY(drivers) REFERENCES finops_unit_driver_version (identity), 
    FOREIGN KEY(model_snapshot) REFERENCES finops_unit_denominator_snapshot (identity), 
    FOREIGN KEY(turn_snapshot) REFERENCES finops_unit_denominator_snapshot (identity)
);

CREATE TABLE finops_unit_economics_publication (
    key VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    period VARCHAR(10) NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    revision BIGINT NOT NULL, 
    snapshot VARCHAR(64), 
    generation VARCHAR(64), 
    last_attempt TIMESTAMP WITH TIME ZONE, 
    last_success TIMESTAMP WITH TIME ZONE, 
    error VARCHAR(32), 
    PRIMARY KEY (key), 
    CHECK (kind IN ('M9','M10','ECONOMICS')), 
    CHECK (revision >= 0), 
    FOREIGN KEY(generation) REFERENCES finops_unit_economics_generation (identity), 
    FOREIGN KEY(snapshot) REFERENCES finops_unit_denominator_snapshot (identity)
);

CREATE UNIQUE INDEX ux_unit_publication ON finops_unit_economics_publication (environment, scope, period, kind);

UPDATE alembic_version SET version_num='f13a7c9d2e61' WHERE alembic_version.version_num = 'e12c6d8a4b50';

-- Running upgrade f13a7c9d2e61 -> a14d2e6f8c70

CREATE TABLE finops_budget_definition_version (
    identity VARCHAR(64) NOT NULL, 
    version VARCHAR(32) NOT NULL, 
    body JSON NOT NULL, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (identity), 
    UNIQUE (version)
);

CREATE TABLE finops_control_input (
    identity VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    month VARCHAR(7) NOT NULL, 
    cutoff TIMESTAMP WITH TIME ZONE NOT NULL, 
    body JSON NOT NULL, 
    PRIMARY KEY (identity)
);

CREATE INDEX ix_control_input_asof ON finops_control_input (environment, scope, month, cutoff);

CREATE TABLE finops_forecast_backtest_generation (
    identity VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    forecast VARCHAR(64) NOT NULL, 
    truth VARCHAR(64) NOT NULL, 
    scoring_version VARCHAR(32) NOT NULL, 
    known_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    body JSON NOT NULL, 
    PRIMARY KEY (identity)
);

CREATE INDEX ix_control_backtest_asof ON finops_forecast_backtest_generation (environment, scope, known_at);

CREATE TABLE finops_control_generation (
    identity VARCHAR(64) NOT NULL, 
    definitions VARCHAR(64) NOT NULL, 
    inputs VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    month VARCHAR(7) NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    issue TIMESTAMP WITH TIME ZONE NOT NULL, 
    body JSON NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (identity), 
    CHECK (kind IN ('BUDGET','FORECAST','ANOMALY')), 
    FOREIGN KEY(definitions) REFERENCES finops_budget_definition_version (identity), 
    FOREIGN KEY(inputs) REFERENCES finops_control_input (identity)
);

CREATE INDEX ix_control_history ON finops_control_generation (environment, scope, month, kind, issue);

CREATE TABLE finops_anomaly_transition (
    identity VARCHAR(64) NOT NULL, 
    anomaly VARCHAR(64) NOT NULL, 
    generation VARCHAR(64) NOT NULL, 
    previous VARCHAR(64), 
    state VARCHAR(16) NOT NULL, 
    at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (identity), 
    CHECK (state IN ('OBSERVED','ACTIVE','RESOLVED')), 
    FOREIGN KEY(generation) REFERENCES finops_control_generation (identity)
);

CREATE INDEX ix_control_anomaly_history ON finops_anomaly_transition (anomaly, at);

CREATE TABLE finops_control_publication (
    key VARCHAR(64) NOT NULL, 
    environment VARCHAR(16) NOT NULL, 
    scope VARCHAR(64) NOT NULL, 
    month VARCHAR(7) NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    selection VARCHAR(128) NOT NULL, 
    revision BIGINT NOT NULL, 
    generation VARCHAR(64), 
    last_attempt TIMESTAMP WITH TIME ZONE, 
    last_success TIMESTAMP WITH TIME ZONE, 
    error VARCHAR(32), 
    PRIMARY KEY (key), 
    CHECK (environment IN ('local','development','staging','production')), 
    CHECK (kind IN ('BUDGET','FORECAST','ANOMALY')), 
    CHECK (revision >= 0), 
    FOREIGN KEY(generation) REFERENCES finops_control_generation (identity)
);

CREATE UNIQUE INDEX ux_control_publication ON finops_control_publication (environment, scope, month, kind, selection);

UPDATE alembic_version SET version_num='a14d2e6f8c70' WHERE alembic_version.version_num = 'f13a7c9d2e61';

-- Running upgrade a14d2e6f8c70 -> b15e3f7a9c81

CREATE TABLE observability_retention_checkpoint (
    environment VARCHAR(16) NOT NULL, 
    dataset VARCHAR(64) NOT NULL, 
    policy_version VARCHAR(32) NOT NULL, 
    cutoff TIMESTAMP WITH TIME ZONE NOT NULL, 
    cursor VARCHAR(1024), 
    generation BIGINT NOT NULL, 
    complete BOOLEAN NOT NULL, 
    PRIMARY KEY (environment, dataset), 
    CHECK (generation >= 0)
);

CREATE TABLE observability_archive_receipt (
    environment VARCHAR(16) NOT NULL, 
    trace_id VARCHAR(32) NOT NULL, 
    trace_class VARCHAR(16) NOT NULL, 
    expected_spans INTEGER NOT NULL, 
    population_digest VARCHAR(64), 
    terminal_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    population_closed BOOLEAN NOT NULL, 
    policy_version VARCHAR(32) NOT NULL, 
    confirmed_digest VARCHAR(64), 
    confirmed_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (environment, trace_id), 
    CHECK (expected_spans >= 0 AND expected_spans <= 10000)
);

CREATE INDEX ix_archive_receipt_environment_terminal ON observability_archive_receipt (environment, terminal_at);

CREATE INDEX ix_m15_run_retention ON observability_run_status (environment, expiry_at, run_id);

CREATE INDEX ix_m15_summary_retention ON observability_turn (environment, expiry_at, run_id);

UPDATE alembic_version SET version_num='b15e3f7a9c81' WHERE alembic_version.version_num = 'a14d2e6f8c70';

-- Running upgrade d3f8b1c6a942, b15e3f7a9c81 -> c16f4a8b2d90

DELETE FROM alembic_version WHERE alembic_version.version_num = 'd3f8b1c6a942';

UPDATE alembic_version SET version_num='c16f4a8b2d90' WHERE alembic_version.version_num = 'b15e3f7a9c81';

COMMIT;

