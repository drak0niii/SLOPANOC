import { getObservabilityJson } from './observabilityClient';
import { ContractError } from '../components/shell/settings/observability/contract';

export interface AuditEntry {
    event_id: string; event_type: string; occurred_at: string; processed_at: string;
    consistency_class: 'A' | 'B' | 'C' | 'D' | null;
    outcome: string | null;
    source_record_id: string;
}
export interface RunAuditPage {
    schema_version: 1; run_id: string; environment: string; as_of: string;
    actions: { execution_id: string; state: string; outcome: string; updated_at: string }[];
    events: AuditEntry[]; next_cursor: string | null; completeness: 'PARTIAL';
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const id = /^[A-Za-z0-9_.:-]{1,128}$/;
const types = new Set(['governance.progression.recorded', 'turn.bound', 'run.accepted', 'run.terminal',
    'model.attempt.started', 'model.attempt.completed', 'model.usage.captured', 'accounting.debt.created', 'accounting.debt.resolved',
    'session.created', 'session.accessed', 'agent.execution.started', 'agent.execution.completed', 'agent.execution.failed',
    'retrieval.started', 'retrieval.completed', 'retrieval.failed', 'evidence.selection.recorded', 'tool.execution.started',
    'tool.execution.completed', 'tool.execution.failed', 'approval.requested', 'approval.approved', 'approval.rejected',
    'approval.expired', 'approval.consumed', 'authorization.decided', 'action.authorized', 'action.dispatch_attempted',
    'action.acknowledged', 'action.completed', 'action.failed', 'action.outcome_unknown']);
function object(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ContractError();
    return value as Record<string, unknown>;
}
function text(value: unknown, pattern: RegExp): string {
    if (typeof value !== 'string' || !pattern.test(value)) throw new ContractError(); return value;
}
function time(value: unknown): string {
    const result = text(value, /^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/);
    if (!Number.isFinite(Date.parse(result))) throw new ContractError(); return result;
}
export function decodeRunAuditPage(value: unknown): RunAuditPage {
    const page = object(value);
    const keys = ['schema_version', 'run_id', 'environment', 'as_of', 'events', 'actions', 'next_cursor', 'completeness', 'consistency_notice'];
    if (Object.keys(page).length !== keys.length || keys.some(k => !(k in page)) || page.schema_version !== 1 || page.completeness !== 'PARTIAL' || !['local', 'development', 'test', 'staging', 'production'].includes(page.environment as string) || !Array.isArray(page.events) || page.events.length > 100) throw new ContractError();
    const run_id = text(page.run_id, uuid);
    if (!Array.isArray(page.actions) || page.actions.length > 100) throw new ContractError();
    const actions = page.actions.map(value => {
        const action = object(value);
        if (Object.keys(action).length !== 4 || Object.keys(action).some(k => !['execution_id','state','outcome','updated_at'].includes(k))) throw new ContractError();
        const state = text(action.state, /^(AUTHORIZED|DISPATCHING|ACKNOWLEDGED|COMPLETED|FAILED|UNKNOWN)$/);
        const outcome = text(action.outcome, /^(AUTHORIZED|ACKNOWLEDGED|COMPLETED|FAILED|UNKNOWN)$/);
        if (outcome !== (state === 'DISPATCHING' ? 'UNKNOWN' : state)) throw new ContractError();
        return { execution_id: text(action.execution_id, uuid), state, outcome, updated_at: time(action.updated_at) };
    });
    const events = page.events.map(value => {
        const row = object(value), event = object(row.event), payload = object(event.payload_safe);
        if (Object.keys(row).length !== 2 || event.schema_version !== 1 || event.event_class !== 'AUDIT' || event.run_id !== run_id || event.environment !== page.environment || !types.has(event.event_type as string)) throw new ContractError();
        const envelopeKeys = ['event_id','schema_version','environment','timestamp','run_id','turn_id','trace_id','event_class','event_type','producer','payload_safe','aggregate_type','aggregate_id','aggregate_version'];
        const payloadKeys = ['source_record_id','source_record_version','capture','status','agent','execution','session_id','model_attempt_id','agent_execution_id','tool_execution_id','approval_id','action_id'];
        if (Object.keys(event).some(k => !envelopeKeys.includes(k)) || Object.keys(payload).some(k => !payloadKeys.includes(k))) throw new ContractError();
        const fact = payload.execution == null ? null : object(payload.execution);
        if (fact) {
            const factKeys = ['execution_domain','consistency_class','agent_execution_id','parent_agent_execution_id','retrieval_id','authorization_id','command_execution_id','attempt_id','actor_type','actor_reference','safe_target_reference','provenance_reference','source_revision','tool_name','retrieval_type','source_namespace','candidate_count','selected_count','duration_ms','outcome','failure_code','decision','reason_code','policy_version','supersedes_event_id'];
            if (!['A', 'B', 'C', 'D'].includes(fact.consistency_class as string) || Object.keys(fact).some(k => !factKeys.includes(k))) throw new ContractError();
        }
        // Explicitly expose only safe timeline summaries; no arbitrary event/body rendering.
        return { event_id: text(event.event_id, uuid), event_type: event.event_type as string,
            occurred_at: time(event.timestamp), processed_at: time(row.processed_at),
            source_record_id: text(payload.source_record_id, id),
            consistency_class: fact ? fact.consistency_class as AuditEntry['consistency_class'] : null,
            outcome: fact ? text(fact.outcome, /^(STARTED|COMPLETED|FAILED|TIMEOUT|CANCELLED|AUTHORIZED|DENIED|DISPATCHING|ACKNOWLEDGED|UNKNOWN|PENDING|APPROVED|REJECTED|EXPIRED|CONSUMED|RECORDED|ACCESSED|CREATED)$/) : null };
    });
    if (new Set(events.map(e => e.event_id)).size !== events.length) throw new ContractError();
    for (let i = 1; i < events.length; i++) {
        const previous = events[i - 1], current = events[i];
        if (Date.parse(current.occurred_at) < Date.parse(previous.occurred_at) ||
            (Date.parse(current.occurred_at) === Date.parse(previous.occurred_at) && current.event_id <= previous.event_id)) throw new ContractError();
    }
    return { schema_version: 1, run_id, environment: page.environment as string, as_of: time(page.as_of), events, actions,
        next_cursor: page.next_cursor === null ? null : text(page.next_cursor, /^[A-Za-z0-9_=-]{1,512}$/), completeness: 'PARTIAL' };
}
export async function getRunAudit(runId: string, signal?: AbortSignal): Promise<RunAuditPage> {
    text(runId, uuid);
    return decodeRunAuditPage(await getObservabilityJson(`/api/observability/runs/${runId}/audit?limit=50`, signal));
}
