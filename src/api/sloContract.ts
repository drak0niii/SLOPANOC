import { ContractError } from '../components/shell/settings/observability/contract';
import { SLO_STATES } from './sloTypes';
import type { SLOPage, SLOView } from './sloTypes';
const ids = ['availability','terminal_completion','latency_general','latency_teams','latency_troubleshooting','latency_complex','model_ttft','trace_completeness','sse_delivery','model_reliability','dependency_gateway','dependency_database','dependency_storage','dependency_knowledge','dependency_graph','cost_ledger_completeness'];
function object(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) throw new ContractError(); return v as Record<string, unknown>; }
function numeric(v: unknown, signed = false): number { if (typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > Number.MAX_SAFE_INTEGER || (!signed && v < 0)) throw new ContractError(); return v; }
function count(v: unknown): number { const n = numeric(v); if (!Number.isInteger(n)) throw new ContractError(); return n; }
function text(v: unknown, pattern: RegExp): string { if (typeof v !== 'string' || !pattern.test(v)) throw new ContractError(); return v; }
function date(v: unknown): string { const s = text(v, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)$/); if (!Number.isFinite(Date.parse(s))) throw new ContractError(); return s; }
function choice<T extends string>(v: unknown, choices: readonly T[]): T { if (!choices.includes(v as T)) throw new ContractError(); return v as T; }
function nullable(v: unknown, decoder: (v: unknown) => number): number | null { return v == null ? null : decoder(v); }
function fraction(v: unknown): number { const n = numeric(v); if (n > 1) throw new ContractError(); return n; }
function decode(v: unknown): SLOView {
    const r = object(v); if (r.schema_version !== 1) throw new ContractError('incompatible');
    const item: SLOView = { schema_version: 1, slo_id: choice(r.slo_id, ids), name: text(r.name, /^[A-Za-z0-9 /-]{1,128}$/),
        objective: fraction(r.objective), objective_status: choice(r.objective_status, ['PROVISIONAL','ARCHITECTURAL']),
        window_seconds: count(r.window_seconds), window_start: date(r.window_start), window_end: date(r.window_end),
        threshold_seconds: nullable(r.threshold_seconds, numeric), eligible: count(r.eligible), good: count(r.good), bad: count(r.bad), unknown: count(r.unknown), excluded: count(r.excluded),
        current_value: nullable(r.current_value, fraction), remaining_fraction: nullable(r.remaining_fraction, v => numeric(v, true)), coverage: nullable(r.coverage, fraction),
        burn_tiers: Array.isArray(r.burn_tiers) && r.burn_tiers.length <= 3 ? r.burn_tiers.map(v => choice(v, ['fast','sustained','slow'])) : (() => { throw new ContractError(); })(),
        state: choice(r.state, SLO_STATES), evaluated_at: date(r.evaluated_at), source_last_updated: r.source_last_updated == null ? null : date(r.source_last_updated),
        freshness_seconds: count(r.freshness_seconds), source_status: choice(r.source_status, ['AVAILABLE','UNAVAILABLE','STALE','DATA_SOURCE_AVAILABLE_IN_M10']) };
    if (item.good + item.bad !== item.eligible || item.window_seconds !== 2419200 || item.objective <= 0) throw new ContractError();
    if (item.state === 'DEFINED_NOT_EVALUATED' && (item.current_value !== null || item.remaining_fraction !== null)) throw new ContractError();
    return item;
}
export function decodeSLOPage(v: unknown): SLOPage {
    const r = object(v); if (r.schema_version !== 1) throw new ContractError('incompatible');
    if (!Array.isArray(r.items) || r.items.length > 32) throw new ContractError();
    const items = r.items.map(decode); if (new Set(items.map(i => i.slo_id)).size !== items.length) throw new ContractError();
    return { schema_version: 1, environment: choice(r.environment, ['local','development','staging','production']), evaluated_at: date(r.evaluated_at), items };
}
