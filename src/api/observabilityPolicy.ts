import { getObservabilityJson } from './observabilityClient';
import { ApiError } from './client';
import { ContractError } from '../components/shell/settings/observability/contract';
const datasets = ['active_terminal', 'ordinary_summary', 'exceptional_summary', 'application_logs', 'cloud_trace', 'exceptional_archive', 'analytical_metrics', 'financial_history', 'slo_receipts', 'slo_working_buckets'] as const;
const capabilities = ['own_progress', 'operational_metadata', 'technical_diagnostics', 'financial_data', 'effective_configuration', 'governed_summaries'] as const;
const environments = ['local', 'development', 'test', 'staging', 'production'] as const;
function object(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ContractError();
    return value as Record<string, unknown>;
}
function choice<T extends string>(value: unknown, allowed: readonly T[]): T {
    if (typeof value !== 'string' || !allowed.includes(value as T)) throw new ContractError();
    return value as T;
}
function list(value: unknown, max: number): unknown[] {
    if (!Array.isArray(value) || value.length > max) throw new ContractError();
    return value;
}
export function decodePolicies(value: unknown) {
    const p = object(value);
    for (const [key, expected] of Object.entries({ privacy_version: 'privacy-v1', retention_version: 'retention-v1', sampling_version: 'sampling-v1', archive_version: 'archive-v1', production_archive: 'NOT_DEPLOYED', production_retention_scheduling: 'NOT_EXECUTED', healthy_reduction_status: 'PROVISIONAL_DISABLED' }))
        if (p[key] !== expected) throw new ContractError();
    if (p.sdk_capture_percent !== 100 || p.healthy_trace_percent !== 100 || typeof p.financial_retention_months !== 'number' || !Number.isInteger(p.financial_retention_months) || p.financial_retention_months < 24 || p.financial_retention_months > 120) throw new ContractError();
    const retention = list(p.retention, 10).map(value => {
        const r = object(value);
        if (typeof r.horizon !== 'number' || !Number.isInteger(r.horizon) || r.horizon < 1 || r.horizon > 120 || typeof r.dependencies !== 'boolean') throw new ContractError();
        return { dataset: choice(r.dataset, datasets), horizon: r.horizon, unit: choice(r.unit, ['hours', 'days', 'months'] as const), owner: choice(r.owner, ['application', 'provider', 'cloud_logging', 'archive'] as const), semantics: choice(r.semantics, ['minimum_retention_then_eligible', 'provider_controlled', 'primary_horizon_async_lifecycle', 'minimum_analytical_access'] as const), dependencies: r.dependencies };
    });
    if (retention.length !== datasets.length || new Set(retention.map(r => r.dataset)).size !== datasets.length) throw new ContractError();
    return { retention, financialMonths: p.financial_retention_months, versions: ['privacy-v1', 'retention-v1', 'sampling-v1', 'archive-v1'] };
}
export function decodeAccess(value: unknown) {
    const a = object(value);
    if (a.verified !== true) throw new ContractError();
    return { capabilities: list(a.capabilities, 6).map(v => choice(v, capabilities)), environments: list(a.environments, 5).map(v => choice(v, environments)) };
}
async function read<T>(path: string, decode: (v: unknown) => T, signal?: AbortSignal) {
    try { return decode(await getObservabilityJson<unknown>('/api/observability/' + path, signal)); }
    catch (error) { if (error instanceof ApiError) throw new ApiError('Diagnostic request unavailable.', error.status); throw error; }
}
export const getPolicies = (signal?: AbortSignal) => read('policies', decodePolicies, signal);
export const getAccess = (signal?: AbortSignal) => read('access', decodeAccess, signal);

export function decodeRunRetention(value: unknown) {
    const r = object(value);
    if (typeof r.run_id !== 'string' || !/^[0-9a-f-]{36}$/.test(r.run_id) || r.cloud_trace !== 'NOT_LIVE_CONFIRMED' || r.production_binding !== 'NOT_DEPLOYED') throw new ContractError();
    return { run_id: r.run_id, instrumentation: choice(r.instrumentation, ['COMPLETE', 'MISSING', 'UNKNOWN'] as const), cloud_trace: 'NOT_LIVE_CONFIRMED', exceptional_archive: choice(r.exceptional_archive, ['RETAINED', 'ELIGIBLE_FOR_DELETION', 'PENDING_OR_DEGRADED', 'NOT_REQUIRED', 'UNAVAILABLE'] as const), production_binding: 'NOT_DEPLOYED' };
}
export const getRunRetention = (runId: string, signal?: AbortSignal) => read('runs/' + encodeURIComponent(runId) + '/retention', decodeRunRetention, signal);
