// M7 v1 server-owned vocabulary; unknown future values are fixed Unknown labels.
export const STAGES = ["request.received", "request.validated", "session.load.started", "session.load.completed", "attachments.started", "attachments.completed", "planning.started", "planning.model.started", "planning.model.completed", "planning.completed", "planning.failed", "thread.resolve.started", "thread.resolve.completed", "pending_interaction.resolve.started", "pending_interaction.resolve.completed", "source_requirements.completed", "context.selection.started", "context.selection.completed", "context.item.selected", "context.item.excluded", "agent.started", "agent.completed", "agent.failed", "agent.team_manager", "agent.technical_authority", "agent.incident_manager", "agent.problem_manager", "agent.automated_operations", "model.request.started", "model.first_token", "model.request.completed", "model.request.failed", "model.request.timeout", "tool.started", "tool.completed", "tool.failed", "tool.timeout", "knowledge.search.started", "knowledge.search.completed", "teams.list_chats.started", "teams.list_chats.completed", "teams.read_messages.started", "teams.read_messages.completed", "database.query.started", "database.query.completed", "storage.operation.started", "storage.operation.completed", "knowledge.selection.completed", "procedure_action.resolved", "command_authority.completed", "approval.requested", "approval.completed", "authority.selected", "provenance.completed", "command_egress.completed", "synthesis.started", "synthesis.completed", "persistence.started", "persistence.completed", "sse.started", "sse.completed", "turn.completed", "turn.failed", "turn.timeout", "turn.cancelled", "run.stalled", "heartbeat"] as const;
export const AGENTS = ["team_manager", "technical_authority_engineer", "incident_manager", "problem_manager", "automated_operations_engineer", "system", "km_image_interpreter", "knowledge_retrieval", "knowledge_embedding"] as const;
export const ERRORS = ["PLANNING_TIMEOUT", "PLANNING_INVALID", "PLANNING_FAILED", "MODEL_TIMEOUT", "MODEL_RATE_LIMIT", "MODEL_AUTH_ERROR", "MODEL_PROVIDER_ERROR", "MODEL_INVALID_RESPONSE", "GRAPH_TIMEOUT", "GRAPH_AUTH_ERROR", "GRAPH_RATE_LIMIT", "GRAPH_NOT_FOUND", "GRAPH_PROVIDER_ERROR", "KNOWLEDGE_TIMEOUT", "KNOWLEDGE_NO_SOURCE", "KNOWLEDGE_PROVIDER_ERROR", "DATABASE_TIMEOUT", "DATABASE_CONNECTION_ERROR", "DATABASE_QUERY_ERROR", "DATABASE_PERSISTENCE_ERROR", "STORAGE_TIMEOUT", "STORAGE_ERROR", "SPECIALIST_FAILED", "TOOL_TIMEOUT", "TOOL_ERROR", "SOURCE_GAP", "COMMAND_AUTHORITY_REJECTED", "APPROVAL_REJECTED", "SSE_DISCONNECTED", "SSE_COMPLETION_MISMATCH", "TRACE_EXPORT_FAILED", "METRIC_EXPORT_FAILED", "COST_LEDGER_PERSIST_FAILED", "TELEMETRY_DROPPED", "TURN_STALLED", "TURN_TIMEOUT", "TURN_CANCELLED"] as const;
export const TOOLS = ["incident_manager", "technical_authority_engineer", "problem_manager", "automated_operations_engineer", "record_case_analysis", "record_conversation_target", "record_source_requirements", "teams_list_chats", "teams_get_messages", "get_resolved_chat_messages", "teams_get_hosted_content", "teams_get_all_hosted_content", "teams_get_members", "get_current_time_context", "teams_propose_create_chat", "teams_propose_send_message", "teams_create_chat", "teams_send_message", "knowledge_search", "knowledge_select_evidence", "procedure_action_catalog", "set_model_response", "other"] as const;
export const SETTINGS = ["projection_enabled", "projection_write_spacing_seconds", "projection_checkpoint_seconds", "projection_stale_seconds", "projection_clock_grace_seconds", "projection_capacity", "projection_attempt_seconds", "projection_terminal_attempts", "projection_retry_seconds", "projection_shutdown_seconds", "projection_terminal_hours", "projection_success_days", "projection_exceptional_days", "trace_sample_rate", "heartbeat_seconds", "watchdog_stall_seconds", "observability_enabled", "otel_enabled", "finops_enabled", "exporter_mode", "collector_endpoint_configured", "service_name", "environment", "telemetry_schema_version", "model_timeout_seconds", "graph_timeout_seconds", "knowledge_timeout_seconds", "database_timeout_seconds", "storage_timeout_seconds", "turn_timeout_seconds", "cleanup_timeout_seconds", "session_load_timeout_seconds", "session_lock_timeout_seconds", "planning_timeout_seconds", "orchestration_timeout_seconds", "agent_timeout_seconds", "tool_timeout_seconds", "gateway_timeout_seconds", "database_acquire_timeout_seconds", "database_query_timeout_seconds", "secret_timeout_seconds", "persistence_timeout_seconds", "sse_delivery_timeout_seconds", "queue_wait_timeout_seconds", "retry_minimum_seconds", "watchdog_check_seconds", "blocking_workers", "blocking_pending", "business_queue_capacity", "business_queue_bytes"] as const;
export const STATUSES = ['PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'STALLED'] as const;
export const DEPENDENCIES = ['session_db', 'case_db', 'attachment_db', 'knowledge_db', 'chat_attachments', 'knowledge_artifacts', 'power_automate_gateway', 'secret_manager', 'knowledge', 'other'] as const;
export const OPERATIONS = ['teams.listChats', 'teams.getMessages', 'teams.getMembers', 'teams.getHostedContent', 'teams.createChat', 'teams.sendMessage', 'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'DDL', 'other', 'CONNECT', 'CHECKOUT', 'COMMIT', 'ROLLBACK', 'metadata', 'applicability', 'sparse', 'dense', 'fusion', 'upload', 'download', 'delete', 'exists', 'access_secret_version'] as const;
import type { RunView, RunPage, TimelinePage, HealthView, EffectiveConfiguration, TechnicalView, DiagnosticEvent, DependencyView } from '../../../../api/observabilityTypes';
export class ContractError extends Error {
    constructor(public kind: 'malformed' | 'incompatible' = 'malformed') { super('Diagnostic response is unavailable.'); }
}
type ObjectValue = Record<string, unknown>;
function object(v: unknown): ObjectValue { if (!v || typeof v !== 'object' || Array.isArray(v))
    throw new ContractError(); return v as ObjectValue; }
function text(v: unknown, pattern = /^[A-Za-z0-9_.-]{1,64}$/): string { if (typeof v !== 'string' || !pattern.test(v))
    throw new ContractError(); return v; }
function number(v: unknown, integer = false): number { if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > Number.MAX_SAFE_INTEGER || (integer && !Number.isInteger(v)))
    throw new ContractError(); return v; }
function bool(v: unknown): boolean { if (typeof v !== 'boolean')
    throw new ContractError(); return v; }
function timestamp(v: unknown): string { const s = text(v, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)$/); if (!Number.isFinite(Date.parse(s)))
    throw new ContractError(); return s; }
function optional<T>(v: unknown, decode: (v: unknown) => T): T | undefined { return v == null ? undefined : decode(v); }
function choice<const T extends readonly string[]>(v: unknown, allowed: T): T[number] | 'Unknown' { if (typeof v !== 'string' || v.length > 256)
    throw new ContractError(); return allowed.includes(v) ? v as T[number] : 'Unknown'; }
function list(v: unknown, max: number): unknown[] { if (!Array.isArray(v) || v.length > max)
    throw new ContractError(); return v; }
const uuid = (v: unknown) => text(v, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
const cursor = (v: unknown) => text(v, /^[A-Za-z0-9_=-]{1,512}$/);
export const isRunId = (v: string) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
function schema(v: unknown): 1 { if (v !== 1)
    throw new ContractError('incompatible'); return 1; }
function technical(v: unknown): TechnicalView {
    const r = object(v);
    return { telemetry_schema_version: schema(r.telemetry_schema_version), producer_instance_id: uuid(r.producer_instance_id), state_version: number(r.state_version, true),
        trace_id: optional(r.trace_id, v => text(v, /^[0-9a-f]{32}$/)), root_span_id: optional(r.root_span_id, v => text(v, /^[0-9a-f]{16}$/)), current_span_id: optional(r.current_span_id, v => text(v, /^[0-9a-f]{16}$/)),
        turn_id: optional(r.turn_id, v => text(v, /^[A-Za-z0-9_.:-]{1,128}$/)), turn_id_origin: optional(r.turn_id_origin, v => choice(v, ['adk', 'execution'] as const)),
        service_version: optional(r.service_version, text), git_sha: optional(r.git_sha, v => text(v, /^[0-9a-fA-F]{7,64}$/)), release_id: optional(r.release_id, text), revision: optional(r.revision, text), region: optional(r.region, text), config_version: optional(r.config_version, v => text(v, /^[0-9a-f]{16}$/)) };
}
function dependency(v: unknown): DependencyView { const r = object(v); return { dependency: choice(r.dependency, DEPENDENCIES), operation: choice(r.operation, OPERATIONS), agent: optional(r.agent, v => choice(v, AGENTS)), tool: optional(r.tool, v => choice(v, TOOLS)), started_at: timestamp(r.started_at) }; }
export function decodeRun(v: unknown): RunView {
    const r = object(v);
    // Optional versions supplied by future API envelopes are checked, never silently ignored.
    if (r.telemetry_schema_version !== undefined)
        schema(r.telemetry_schema_version);
    return { run_id: uuid(r.run_id), environment: text(r.environment), status: choice(r.status, STATUSES), classification: choice(r.classification, ['ACTIVE', 'STALLED', 'STALE', 'TERMINAL'] as const),
        outcome_unknown: bool(r.outcome_unknown), source: r.source === 'durable' ? 'durable' : (() => { throw new ContractError(); })(), classification_as_of: timestamp(r.classification_as_of),
        started_at: timestamp(r.started_at), terminal_at: optional(r.terminal_at, timestamp), current_stage: choice(r.current_stage, STAGES), current_agent: optional(r.current_agent, v => choice(v, AGENTS)), current_tool: optional(r.current_tool, v => choice(v, TOOLS)),
        dependencies: list(r.dependencies ?? [], 128).map(dependency), elapsed_ms: number(r.elapsed_ms), last_progress_at: timestamp(r.last_progress_at), heartbeat_at: timestamp(r.heartbeat_at), progress_age_ms: number(r.progress_age_ms),
        work_deadline_at: optional(r.work_deadline_at, timestamp), total_deadline_at: optional(r.total_deadline_at, timestamp), error_code: optional(r.error_code, v => choice(v, ERRORS)),
        cleanup_status: choice(r.cleanup_status, ['pending', 'running', 'completed', 'failed', 'timeout']), delivery_status: choice(r.delivery_status, ['pending', 'relayed', 'disconnected']), timeline_truncated_count: number(r.timeline_truncated_count, true), technical: optional(r.technical, technical) };
}
export function decodeRuns(v: unknown): RunPage { const r = object(v); if (r.telemetry_schema_version !== undefined)
    schema(r.telemetry_schema_version); return { items: list(r.items, 100).map(decodeRun), next_cursor: optional(r.next_cursor, cursor), as_of: timestamp(r.as_of) }; }
function event(v: unknown): DiagnosticEvent { const r = object(v); return { event_seq: number(r.event_seq, true), state_version: number(r.state_version, true), event_type: choice(r.event_type, ['started', 'stage', 'agent', 'tool', 'dependency', 'stalled', 'resumed', 'terminal', 'cleanup', 'delivery', 'identity', 'deadline']), timestamp: timestamp(r.timestamp), elapsed_ms: number(r.elapsed_ms), stage: choice(r.stage, STAGES), status: choice(r.status, STATUSES), agent: optional(r.agent, v => choice(v, AGENTS)), tool: optional(r.tool, v => choice(v, TOOLS)), dependency: optional(r.dependency, v => choice(v, DEPENDENCIES)), error_code: optional(r.error_code, v => choice(v, ERRORS)) }; }
export function decodeTimeline(v: unknown): TimelinePage { const r = object(v); if (r.telemetry_schema_version !== undefined)
    schema(r.telemetry_schema_version); const items = list(r.items, 100).map(event); if (items.some((e, i) => i > 0 && e.event_seq <= items[i - 1].event_seq))
    throw new ContractError(); return { items, next_cursor: optional(r.next_cursor, cursor), truncated_count: number(r.truncated_count, true) }; }
export function decodeHealth(v: unknown): HealthView {
    const r = object(v);
    if (r.telemetry_schema_version !== undefined)
        schema(r.telemetry_schema_version);
    if (r.writer_scope !== 'process_local' || r.durable_scope !== 'authorized_environments')
        throw new ContractError();
    return { persistence: choice(r.persistence, ['disabled', 'healthy', 'degraded', 'unavailable']), writer_scope: 'process_local', durable_scope: 'authorized_environments', as_of: timestamp(r.as_of), pending: number(r.pending, true), admitted: number(r.admitted, true), terminal_pending: number(r.terminal_pending, true), stale_records: optional(r.stale_records, v => number(v, true)), exporter_state: choice(r.exporter_state, ['disabled', 'healthy', 'degraded', 'unknown']), collector_configured: bool(r.collector_configured), last_success: optional(r.last_success, timestamp), accepted: number(r.accepted ?? 0, true), coalesced: number(r.coalesced ?? 0, true), attempts: number(r.attempts ?? 0, true), persisted: number(r.persisted ?? 0, true), failed: number(r.failed ?? 0, true), retries: number(r.retries ?? 0, true), rejected: number(r.rejected ?? 0, true), terminal_unpersisted: number(r.terminal_unpersisted ?? 0, true), shutdown_incomplete: number(r.shutdown_incomplete ?? 0, true), invalid: number(r.invalid ?? 0, true) };
}
export function decodeConfig(v: unknown): EffectiveConfiguration[] {
    const result: EffectiveConfiguration[] = [];
    const seen = new Set<string>();
    for (const item of list(v, 100)) {
        const r = object(item);
        const setting = choice(r.setting, SETTINGS);
        if (setting === 'Unknown')
            continue;
        if (seen.has(setting) || r.read_only !== true)
            throw new ContractError();
        seen.add(setting);
        let value: boolean | number | string;
        if (['projection_enabled', 'observability_enabled', 'otel_enabled', 'finops_enabled', 'collector_endpoint_configured'].includes(setting))
            value = bool(r.value);
        else if (['environment', 'service_name'].includes(setting))
            value = text(r.value);
        else if (setting === 'exporter_mode')
            value = choice(r.value, ['otlp', 'local', 'none', 'disabled']);
        else
            value = number(r.value);
        if (setting === 'telemetry_schema_version')
            schema(value);
        result.push({ setting, value, owner: choice(r.owner, ['application_settings', 'environment_policy', 'terraform', 'durable_database', 'runtime_ui']), read_only: true, config_version: text(r.config_version, /^[A-Za-z0-9_.-]{1,64}$/) });
    }
    return result;
}
