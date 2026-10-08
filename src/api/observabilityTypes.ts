import type { AGENTS, ERRORS, STAGES, TOOLS, DEPENDENCIES, OPERATIONS } from '../components/shell/settings/observability/contract';
export type Status = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'TIMEOUT' | 'CANCELLED' | 'STALLED' | 'Unknown';
export type Stage = typeof STAGES[number] | 'Unknown';
export type Agent = typeof AGENTS[number] | 'Unknown';
export type Tool = typeof TOOLS[number] | 'Unknown';
export type Dependency = typeof DEPENDENCIES[number] | 'Unknown';
export type Operation = typeof OPERATIONS[number] | 'Unknown';
export type EventType = 'started' | 'stage' | 'agent' | 'tool' | 'dependency' | 'stalled' | 'resumed' | 'terminal' | 'cleanup' | 'delivery' | 'identity' | 'deadline' | 'Unknown';
export type HealthState = 'disabled' | 'healthy' | 'degraded' | 'unavailable' | 'unknown' | 'Unknown';
export type ErrorCode = typeof ERRORS[number] | 'Unknown';
export type Classification = 'ACTIVE' | 'STALLED' | 'STALE' | 'TERMINAL' | 'Unknown';
export interface DependencyView {
    dependency: Dependency;
    operation: Operation;
    agent?: Agent;
    tool?: Tool;
    started_at: string;
}
export interface TechnicalView {
    trace_id?: string;
    root_span_id?: string;
    current_span_id?: string;
    turn_id?: string;
    turn_id_origin?: 'adk' | 'execution' | 'Unknown';
    producer_instance_id: string;
    state_version: number;
    service_version?: string;
    git_sha?: string;
    release_id?: string;
    revision?: string;
    region?: string;
    config_version?: string;
    telemetry_schema_version: 1;
}
export interface RunView {
    run_id: string;
    environment: string;
    status: Status;
    classification: Classification;
    outcome_unknown: boolean;
    source: 'durable';
    classification_as_of: string;
    started_at: string;
    terminal_at?: string;
    current_stage: Stage;
    current_agent?: Agent;
    current_tool?: Tool;
    dependencies: DependencyView[];
    elapsed_ms: number;
    last_progress_at: string;
    heartbeat_at: string;
    progress_age_ms: number;
    work_deadline_at?: string;
    total_deadline_at?: string;
    error_code?: ErrorCode;
    cleanup_status: 'pending' | 'running' | 'completed' | 'failed' | 'timeout' | 'Unknown';
    delivery_status: 'pending' | 'relayed' | 'disconnected' | 'Unknown';
    timeline_truncated_count: number;
    technical?: TechnicalView;
}
export interface RunPage {
    items: RunView[];
    next_cursor?: string;
    as_of: string;
}
export interface DiagnosticEvent {
    event_seq: number;
    state_version: number;
    event_type: EventType;
    timestamp: string;
    elapsed_ms: number;
    stage: Stage;
    status: Status;
    agent?: Agent;
    tool?: Tool;
    dependency?: Dependency;
    error_code?: ErrorCode;
}
export interface TimelinePage {
    items: DiagnosticEvent[];
    next_cursor?: string;
    truncated_count: number;
}
export interface HealthView {
    persistence: HealthState;
    writer_scope: 'process_local';
    durable_scope: 'authorized_environments';
    as_of: string;
    pending: number;
    admitted: number;
    terminal_pending: number;
    stale_records?: number;
    exporter_state: HealthState;
    collector_configured: boolean;
    last_success?: string;
    accepted: number;
    coalesced: number;
    attempts: number;
    persisted: number;
    failed: number;
    retries: number;
    rejected: number;
    terminal_unpersisted: number;
    shutdown_incomplete: number;
    invalid: number;
}
export interface EffectiveConfiguration {
    setting: string;
    value: boolean | number | string;
    owner: string;
    read_only: true;
    config_version: string;
}
export interface RunQuery {
    limit?: number;
    cursor?: string;
    status?: Exclude<Status, 'Unknown'>;
    stage?: Exclude<Stage, 'Unknown'>;
    environment?: string;
    since?: string;
    until?: string;
}
export type PageQuery = Pick<RunQuery, 'limit' | 'cursor'>;
