import { createContext } from 'react';
export interface ObservabilityScope { environment?: string; since?: string; until?: string; refresh?: number; capabilities?: readonly string[]; onRetrieved?: (at: number) => void }
export const ObservabilityScopeContext = createContext<ObservabilityScope>({});
const scopes = new WeakMap<AbortSignal, ObservabilityScope>();
export function getObservabilityScope(signal: AbortSignal) { return scopes.get(signal); }
export function registerObservabilityScope(signal: AbortSignal, scope: ObservabilityScope) { scopes.set(signal, scope); }
export function scopedObservabilityPath(path: string, signal?: AbortSignal) {
    const scope = signal && scopes.get(signal);
    if (!scope) return path;
    const [route, query = ''] = path.split('?');
    const params = new URLSearchParams(query);
    if (scope.environment && !route.endsWith('/access')) params.set('environment', scope.environment);
    if (scope.since && scope.until) {
        if (/\/observability\/runs$/.test(route)) { if (!params.has('since')) params.set('since', scope.since); if (!params.has('until')) params.set('until', scope.until); }
        else if (route.endsWith('/runtime-usage')) { if (!params.has('start')) params.set('start', scope.since); if (!params.has('end')) params.set('end', scope.until); }
        else if (/\/(billing-summary|reconciliation|allocation|unit-economics)$/.test(route) && params.get('period_basis') !== 'invoice') {
            if (!params.has('start')) params.set('start', scope.since.slice(0, 10)); if (!params.has('end')) params.set('end', scope.until.slice(0, 10));
        }
    }
    return route + (params.size ? `?${params}` : '');
}
