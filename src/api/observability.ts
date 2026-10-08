import { ApiError, getJson } from './client';
import type { PageQuery, RunQuery } from './observabilityTypes';
import { ContractError, decodeConfig, decodeHealth, decodeRun, decodeRuns, decodeTimeline, isRunId } from '../components/shell/settings/observability/contract';
const base = '/api/observability';
function query(q: RunQuery = {}): string {
    if (q.limit !== undefined && (!Number.isInteger(q.limit) || q.limit < 1 || q.limit > 100))
        throw new ContractError();
    const params = new URLSearchParams();
    for (const key of ['limit', 'cursor', 'status', 'stage', 'environment', 'since', 'until'] as const)
        if (q[key] !== undefined)
            params.set(key, String(q[key]));
    return params.size ? `?${params}` : '';
}
async function read<T>(path: string, decode: (v: unknown) => T, signal?: AbortSignal): Promise<T> {
    try {
        return decode(await getJson<unknown>(base + path, signal));
    }
    catch (e) {
        if (e instanceof ApiError)
            throw new ApiError('Diagnostic request unavailable.', e.status);
        if (e instanceof SyntaxError)
            throw new ContractError();
        throw e;
    }
}
function id(value: string) { if (!isRunId(value))
    throw new ContractError(); return encodeURIComponent(value); }
export const getActiveRuns = (q: RunQuery = {}, signal?: AbortSignal) => read('/active' + query(q), decodeRuns, signal);
export const getRecentRuns = (q: RunQuery = {}, signal?: AbortSignal) => read('/runs' + query(q), decodeRuns, signal);
export const getRun = (runId: string, signal?: AbortSignal) => read(`/runs/${id(runId)}`, decodeRun, signal);
export const getRunTimeline = (runId: string, q: PageQuery = {}, signal?: AbortSignal) => read(`/runs/${id(runId)}/timeline` + query(q), decodeTimeline, signal);
export const getSessionRuns = (sessionId: string, q: PageQuery = {}, signal?: AbortSignal) => { if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(sessionId))
    throw new ContractError(); return read(`/sessions/${encodeURIComponent(sessionId)}/runs` + query(q), decodeRuns, signal); };
export const getObservabilityHealth = (signal?: AbortSignal) => read('/health', decodeHealth, signal);
export const getObservabilityConfig = (signal?: AbortSignal) => read('/config', decodeConfig, signal);

import { decodeSLOPage } from './sloContract';
export const getSLOs = (signal?: AbortSignal) => read('/slos', decodeSLOPage, signal);
