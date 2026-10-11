import { getObservabilityJson } from './observabilityClient';
import { AGENTS, ContractError } from '../components/shell/settings/observability/contract';

const environments = ['local', 'development', 'test', 'staging', 'production'] as const;
type Environment = typeof environments[number];
type Status = 'RUNNING' | 'COMPLETED' | 'FAILED' | 'TIMEOUT' | 'CANCELLED';
interface Freshness { latest_event_occurred_at: string; latest_event_processed_at: string; projection_lag: number }
export interface HybridRun extends Freshness { run_id: string; turn_id: string | null; trace_id: string | null; status: Status; aggregate_version: number }
export interface HybridAgent extends Freshness { agent: typeof AGENTS[number]; model_attempts_started: number; model_attempts_completed: number; usage_records_captured: number }
export interface HybridActivity {
    schema_version: 1; environment: Environment; as_of: string; runs: HybridRun[]; agents: HybridAgent[];
    debt: { pending: number; leased: number; retrying: number; dead: number; oldest_unpublished_age: number | null; bounded_snapshot_truncated: boolean };
    processing: { processed: number; duplicate: number; conflict: number; invalid: number; retry: number; dead: number; bounded_snapshot_truncated: boolean };
    runs_truncated: boolean; counts_scope: 'returned_page_and_bounded_processing_snapshot'; completed_in_page: number; failed_in_page: number;
}
function object(v: unknown, keys: string[]): Record<string, unknown> {
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw new ContractError();
    const r = v as Record<string, unknown>;
    if (Object.keys(r).length !== keys.length || keys.some(k => !(k in r))) throw new ContractError();
    return r;
}
function number(v: unknown, integer = false): number {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || (integer && !Number.isSafeInteger(v))) throw new ContractError();
    return v;
}
function bool(v: unknown): boolean { if (typeof v !== 'boolean') throw new ContractError(); return v; }
function text(v: unknown, pattern: RegExp): string { if (typeof v !== 'string' || !pattern.test(v)) throw new ContractError(); return v; }
function timestamp(v: unknown): string {
    const t = text(v, /^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/);
    if (!Number.isFinite(Date.parse(t))) throw new ContractError(); return t;
}
const freshKeys = ['latest_event_occurred_at', 'latest_event_processed_at', 'projection_lag'];
function freshness(r: Record<string, unknown>): Freshness {
    return { latest_event_occurred_at: timestamp(r.latest_event_occurred_at), latest_event_processed_at: timestamp(r.latest_event_processed_at), projection_lag: number(r.projection_lag) };
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function decodeHybridActivity(v: unknown): HybridActivity {
    const r = object(v, ['schema_version', 'environment', 'as_of', 'runs', 'agents', 'debt', 'processing', 'runs_truncated', 'counts_scope', 'completed_in_page', 'failed_in_page']);
    if (r.schema_version !== 1 || !environments.includes(r.environment as Environment) || r.counts_scope !== 'returned_page_and_bounded_processing_snapshot' || !Array.isArray(r.runs) || r.runs.length > 100 || !Array.isArray(r.agents) || r.agents.length > AGENTS.length) throw new ContractError();
    const runs = r.runs.map(v => {
        const row = object(v, ['run_id', 'turn_id', 'trace_id', 'status', 'aggregate_version', ...freshKeys]);
        if (!['RUNNING', 'COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED'].includes(row.status as string)) throw new ContractError();
        const aggregate_version = number(row.aggregate_version, true); if (aggregate_version < 1) throw new ContractError();
        const trace = row.trace_id === null ? null : text(row.trace_id, /^[0-9a-f]{32}$/);
        if (trace === '0'.repeat(32)) throw new ContractError();
        return { run_id: text(row.run_id, uuid), turn_id: row.turn_id === null ? null : text(row.turn_id, /^[A-Za-z0-9_.:-]{1,128}$/), trace_id: trace, status: row.status as Status, aggregate_version, ...freshness(row) };
    });
    const agents = r.agents.map(v => {
        const row = object(v, ['agent', 'model_attempts_started', 'model_attempts_completed', 'usage_records_captured', ...freshKeys]);
        if (!AGENTS.includes(row.agent as typeof AGENTS[number])) throw new ContractError();
        return { agent: row.agent as typeof AGENTS[number], model_attempts_started: number(row.model_attempts_started, true), model_attempts_completed: number(row.model_attempts_completed, true), usage_records_captured: number(row.usage_records_captured, true), ...freshness(row) };
    });
    if (new Set(runs.map(x => x.run_id)).size !== runs.length || new Set(agents.map(x => x.agent)).size !== agents.length) throw new ContractError();
    const d = object(r.debt, ['pending', 'leased', 'retrying', 'dead', 'oldest_unpublished_age', 'bounded_snapshot_truncated']);
    const p = object(r.processing, ['processed', 'duplicate', 'conflict', 'invalid', 'retry', 'dead', 'bounded_snapshot_truncated']);
    return { schema_version: 1, environment: r.environment as Environment, as_of: timestamp(r.as_of), runs, agents,
        debt: { pending: number(d.pending, true), leased: number(d.leased, true), retrying: number(d.retrying, true), dead: number(d.dead, true), oldest_unpublished_age: d.oldest_unpublished_age === null ? null : number(d.oldest_unpublished_age), bounded_snapshot_truncated: bool(d.bounded_snapshot_truncated) },
        processing: { processed: number(p.processed, true), duplicate: number(p.duplicate, true), conflict: number(p.conflict, true), invalid: number(p.invalid, true), retry: number(p.retry, true), dead: number(p.dead, true), bounded_snapshot_truncated: bool(p.bounded_snapshot_truncated) },
        runs_truncated: bool(r.runs_truncated), counts_scope: 'returned_page_and_bounded_processing_snapshot', completed_in_page: number(r.completed_in_page, true), failed_in_page: number(r.failed_in_page, true) };
}
export async function getHybridActivity(signal?: AbortSignal): Promise<HybridActivity> {
    return decodeHybridActivity(await getObservabilityJson('/api/observability/hybrid/activity?limit=50', signal));
}
