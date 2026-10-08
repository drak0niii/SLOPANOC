import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../../../api/client';
import { ContractError } from './contract';
export type ReadError = 'unauthorized' | 'denied' | 'not-found' | 'unavailable' | 'failed' | 'malformed' | 'incompatible' | 'throttled';
export interface QueryState<T> {
    data?: T;
    loading: boolean;
    error?: ReadError;
    updatedAt?: number;
}
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
        if (e.status === 503)
            return 'unavailable';
    }
    return 'failed';
}
/** One cancellable, completion-scheduled request per mounted section. No global cache. */
export function useObservabilityQuery<T>(key: string, load: (signal: AbortSignal) => Promise<T>, poll?: (data: T) => boolean, enabled = true) {
    const [state, setState] = useState<QueryState<T>>({ loading: true });
    const loader = useRef(load);
    loader.current = load;
    const policy = useRef(poll);
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
        const run = async () => {
            if (!alive || busy || document.hidden)
                return;
            clearTimeout(pollTimer);
            busy = true;
            const current = ++generation;
            controller = new AbortController();
            const signal = controller.signal;
            setState(s => ({ ...s, loading: true, error: undefined }));
            try {
                const timeout = new Promise<never>((_, reject) => { deadlineTimer = setTimeout(() => { controller?.abort(); reject(new Error('Request timed out.')); }, 10000); });
                const data = await Promise.race([loader.current(signal), timeout]);
                if (!alive || current !== generation)
                    return;
                lastData = data;
                stopped = false;
                setState({ data, loading: false, updatedAt: Date.now() });
            }
            catch (e) {
                if (!alive || current !== generation)
                    return;
                const error = classify(e);
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
        execute.current = () => { stopped = false; void run(); };
        const visibility = () => { if (document.hidden)
            cancel();
        else if (!stopped)
            void run(); };
        document.addEventListener('visibilitychange', visibility);
        void run();
        return () => { alive = false; cancel(); execute.current = () => { }; document.removeEventListener('visibilitychange', visibility); };
    }, [key, enabled]);
    return { ...state, refresh };
}
