import { ObservabilityScopeContext, registerObservabilityScope } from '../../../../api/observabilityScope';
import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../../../api/client';
import { ContractError } from './contract';
export type ReadError = 'unauthorized' | 'denied' | 'not-found' | 'unavailable' | 'failed' | 'malformed' | 'incompatible' | 'throttled';
export interface QueryState<T> {
    data?: T;
    loading: boolean;
    error?: ReadError;
    updatedAt?: number;
}
// Mounted protected sections share revocation, never response data or authority.
const revocations = new Set<(error: 'unauthorized' | 'denied') => void>();
function classify(e: unknown): ReadError {
    if (e instanceof ContractError)
        return e.kind;
    if (e instanceof ApiError) {
        if (e.status === 401)
            return 'unauthorized';
        if (e.status === 403)
            return 'denied';
        if (e.status === 404)
            return 'not-found';
        if (e.status === 429)
            return 'throttled';
        if ([502, 503, 504].includes(e.status))
            return 'unavailable';
    }
    if (e instanceof TypeError) return 'unavailable';
    return 'failed';
}
/** One cancellable, completion-scheduled request per mounted section. No global cache. */
export function useObservabilityQuery<T>(key: string, load: (signal: AbortSignal) => Promise<T>, poll?: (data: T) => boolean, enabled = true, refreshMode: 'retain' | 'replace' = 'retain') {
    const scope = useContext(ObservabilityScopeContext);
    const scopedKey = JSON.stringify([key, scope]);
    const [state, setState] = useState<QueryState<T>>({ loading: true });
    const loader = useRef(load);
    loader.current = load;
    const policy = useRef(poll);
    const previouslyAuthorized = useRef(false);
    policy.current = poll;
    const execute = useRef<() => void>(() => { });
    const refresh = useCallback(() => execute.current(), []);
    useEffect(() => {
        if (!enabled) {
            setState({ loading: false });
            execute.current = () => { };
            return;
        }
        let alive = true, busy = false, stopped = false;
        let controller: AbortController | undefined;
        let pollTimer: ReturnType<typeof setTimeout> | undefined;
        let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
        let lastData: T | undefined;
        let generation = 0;
        setState({ loading: true });
        const cancel = () => { generation++; controller?.abort(); clearTimeout(deadlineTimer); clearTimeout(pollTimer); busy = false; };
        const revoke = (error: 'unauthorized' | 'denied') => {
            cancel(); stopped = true; lastData = undefined;
            if (alive) setState({ loading: false, error });
        };
        revocations.add(revoke);
        const run = async () => {
            if (!alive || busy || document.hidden)
                return;
            clearTimeout(pollTimer);
            busy = true;
            const current = ++generation;
            controller = new AbortController();
            const signal = controller.signal;
            registerObservabilityScope(signal, scope);
            setState(s => ({ ...s, loading: true, error: undefined }));
            try {
                const timeout = new Promise<never>((_, reject) => { deadlineTimer = setTimeout(() => { controller?.abort(); reject(new ApiError('Read deadline exceeded.', 503)); }, 10000); });
                const data = await Promise.race([loader.current(signal), timeout]);
                if (!alive || current !== generation)
                    return;
                lastData = data;
                scope.onRetrieved?.(Date.now());
                previouslyAuthorized.current = true;
                stopped = false;
                setState({ data, loading: false, updatedAt: Date.now() });
            }
            catch (e) {
                if (!alive || current !== generation)
                    return;
                const error = classify(e);
                // Initial role-specific denial is an access probe, not revocation.
                if (error === 'unauthorized' || (error === 'denied' && previouslyAuthorized.current)) {
                    for (const subscriber of revocations) subscriber(error);
                    return;
                }
                stopped = true;
                lastData = undefined;
                // Clear all data on failure, including revocation; never expose old technical fields.
                setState({ loading: false, error });
            }
            finally {
                if (alive && current === generation) {
                    clearTimeout(deadlineTimer);
                    busy = false;
                    if (!stopped && lastData !== undefined && policy.current?.(lastData) && !document.hidden)
                        pollTimer = setTimeout(() => void run(), 15000);
                }
            }
        };
        execute.current = () => {
            if (refreshMode === 'replace') {
                cancel(); lastData = undefined;
                // Access must be verified again before any protected panel mounts.
                setState({ loading: true });
            }
            stopped = false; void run();
        };
        const visibility = () => { if (document.hidden)
            cancel();
        else if (!stopped)
            void run(); };
        document.addEventListener('visibilitychange', visibility);
        void run();
        return () => { alive = false; revocations.delete(revoke); cancel(); execute.current = () => { }; document.removeEventListener('visibilitychange', visibility); };
    }, [scopedKey, enabled, refreshMode]);
    return { ...state, refresh };
}
