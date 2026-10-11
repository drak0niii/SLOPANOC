import { ApiError, getJsonAtBase } from './client';
import { scopedObservabilityPath, getObservabilityScope } from './observabilityScope';

export function getObservabilityApiBaseUrl(): string {
    const raw = import.meta.env.VITE_OBSERVABILITY_API_BASE_URL;
    // DEV's dedicated Vite proxy preserves first-party browser credentials.
    // Production still requires an explicit deployment binding.
    if (!raw?.trim() && import.meta.env.DEV) return '';
    if (!raw?.trim()) throw new Error('Observability service is not configured.');
    const base = raw.trim().replace(/\/+$/, '');
    const url = new URL(base);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash ||
        (import.meta.env.PROD && url.protocol !== 'https:')) throw new Error('Observability service configuration is invalid.');
    return base;
}
/** Identical GETs in a query bundle share one in-flight read, never persisted. */
const reads = new WeakMap<AbortSignal, Map<string, Promise<unknown>>>();
interface SharedRead { controller: AbortController; promise: Promise<unknown>; users: Set<symbol> }
// In-flight only; entries are removed at completion or when the last caller cancels.
const sharedReads = new Map<string, SharedRead>();
function sharedRead<T>(base: string, path: string, signal: AbortSignal): Promise<T> {
    if (signal.aborted) return Promise.reject(new DOMException('Read cancelled.', 'AbortError'));
    const key = base + path;
    let entry = sharedReads.get(key);
    if (!entry) {
        if (sharedReads.size >= 64) return Promise.reject(new Error('Read capacity unavailable.'));
        const controller = new AbortController();
        const promise = getJsonAtBase<unknown>(base, path, controller.signal);
        entry = { controller, promise, users: new Set() }; sharedReads.set(key, entry);
        const owner = entry;
        void promise.finally(() => { if (sharedReads.get(key) === owner) sharedReads.delete(key); }).catch(() => {});
    }
    const owner = entry, user = Symbol(); owner.users.add(user);
    return new Promise<T>((resolve, reject) => {
        let complete = false;
        const cleanup = () => {
            signal.removeEventListener('abort', abort); owner.users.delete(user);
            if (!owner.users.size && sharedReads.get(key) === owner) { sharedReads.delete(key); owner.controller.abort(); }
        };
        const abort = () => { if (!complete) { complete = true; cleanup(); reject(new DOMException('Read cancelled.', 'AbortError')); } };
        signal.addEventListener('abort', abort, { once: true });
        owner.promise.then(value => { if (!complete) { complete = true; cleanup(); resolve(value as T); } }, error => { if (!complete) { complete = true; cleanup(); reject(error); } });
    });
}
export function getObservabilityJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    if (!path.startsWith('/api/observability/') || path.includes('sse-receipts')) throw new Error('Invalid read path.');
    const scope = signal && getObservabilityScope(signal);
    if (scope?.capabilities && !path.split('?')[0].endsWith('/access')) {
        const route = path.split('?')[0];
        const required = route.includes('/finops/') ? 'financial_data' : route.includes('/archive/') ? 'technical_diagnostics' : /\/(config|policies)$/.test(route) ? 'effective_configuration' : 'operational_metadata';
        if (!scope.capabilities.includes(required)) return Promise.reject(new ApiError('Access is not permitted.', 403));
    }
    const scoped = scopedObservabilityPath(path, signal);
    const base = getObservabilityApiBaseUrl();
    if (!signal) return getJsonAtBase<T>(base, scoped);
    if (getObservabilityScope(signal)?.since) return sharedRead<T>(base, scoped, signal);
    let pending = reads.get(signal);
    if (!pending) { pending = new Map(); reads.set(signal, pending); }
    const existing = pending.get(scoped);
    if (existing) return existing as Promise<T>;
    const request = getJsonAtBase<T>(base, scoped, signal);
    pending.set(scoped, request);
    void request.finally(() => pending?.delete(scoped)).catch(() => {});
    return request;
}
