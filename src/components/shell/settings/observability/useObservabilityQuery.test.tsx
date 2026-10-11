import { it, expect, vi, afterEach } from "vitest";
import { renderHook, act, cleanup } from '@testing-library/react';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ApiError } from '../../../../api/client';
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
async function flush() { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }
it('15 second completion cadence, no overlap, cancellation and inactive cleanup', async () => {
    vi.useFakeTimers();
    let resolve!: (v: number) => void;
    let signal: AbortSignal | undefined;
    const load = vi.fn((s: AbortSignal) => { signal = s; return new Promise<number>(r => { resolve = r; }); });
    const h = renderHook(() => useObservabilityQuery('active', load, () => true));
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    act(() => h.result.current.refresh());
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => { resolve(1); });
    await act(async () => { vi.advanceTimersByTime(14999); });
    expect(load).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(load).toHaveBeenCalledTimes(2);
    h.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => vi.advanceTimersByTime(30000));
    expect(load).toHaveBeenCalledTimes(2);
});
it.each([401, 403, 429])('revocation/throttling %i clears data and stops polling', async (status) => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValueOnce({ technical: 'authorized' }).mockRejectedValue(new ApiError('secret', status));
    const h = renderHook(() => useObservabilityQuery('active', load, () => true));
    await flush();
    expect(h.result.current.data).toBeDefined();
    await act(async () => vi.advanceTimersByTime(15000));
    expect(h.result.current.data).toBeUndefined();
    expect(h.result.current.error).toBeDefined();
    await act(async () => vi.advanceTimersByTime(60000));
    expect(load).toHaveBeenCalledTimes(2);
});
it('pauses hidden document, resumes when visible and ignores obsolete response', async () => {
    vi.useFakeTimers();
    let hidden = false;
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    let resolve!: (v: number) => void;
    const load = vi.fn().mockImplementationOnce(() => new Promise<number>(r => { resolve = r; })).mockResolvedValue(2);
    const h = renderHook(() => useObservabilityQuery('active', load, () => true));
    await flush();
    hidden = true;
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(async () => { resolve(1); vi.advanceTimersByTime(30000); });
    expect(load).toHaveBeenCalledTimes(1);
    expect(h.result.current.data).toBeUndefined();
    hidden = false;
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await flush();
    expect(h.result.current.data).toBe(2);
});
it('finite loading timeout even for a transport that ignores AbortSignal', async () => { vi.useFakeTimers(); const load = vi.fn(() => new Promise<number>(() => { })); const h = renderHook(() => useObservabilityQuery('slow', load)); await act(async () => vi.advanceTimersByTime(10000)); expect(h.result.current.loading).toBe(false); expect(h.result.current.error).toBe('unavailable'); });
it('terminal or stale policy suppresses automated refresh', async () => { vi.useFakeTimers(); const load = vi.fn().mockResolvedValue({ classification: 'STALE' }); renderHook(() => useObservabilityQuery('detail', load, () => false)); await flush(); await act(async () => vi.advanceTimersByTime(60000)); expect(load).toHaveBeenCalledTimes(1); });
it('changed key cancels old request and old result cannot replace new selection', async () => { let oldResolve!: (v: number) => void; const load = vi.fn().mockImplementationOnce(() => new Promise<number>(r => { oldResolve = r; })).mockResolvedValue(2); const h = renderHook(({ key }) => useObservabilityQuery(key, load), { initialProps: { key: 'first' } }); h.rerender({ key: 'second' }); await flush(); await act(async () => oldResolve(1)); expect(h.result.current.data).toBe(2); });
it('uses actual RunDetail backend policy to stop for terminal and stale outcomes', async () => {
    const { activeRun } = await import('./RunDetail');
    const { run } = await import('./fixtures.test-support');
    expect(activeRun(run({ status: 'RUNNING', classification: 'ACTIVE' }))).toBe(true);
    expect(activeRun(run({ status: 'STALLED' }))).toBe(true);
    expect(activeRun(run({ status: 'RUNNING', classification: 'STALE' }))).toBe(false);
    for (const status of ['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED'] as const)
        expect(activeRun(run({ status, classification: 'TERMINAL' }))).toBe(false);
});
it('inactive section cancels polling and clears data before reactivation', async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValue(1);
    const h = renderHook(({ active }) => useObservabilityQuery('active', load, () => true, active), { initialProps: { active: true } });
    await flush();
    h.rerender({ active: false });
    expect(h.result.current.data).toBeUndefined();
    await act(async () => vi.advanceTimersByTime(60000));
    expect(load).toHaveBeenCalledTimes(1);
    h.rerender({ active: true });
    await flush();
    expect(load).toHaveBeenCalledTimes(2);
});
it.each([401, 403])('M16 authorization loss %i clears every mounted section and rejects late results', async status => {
    let resolve!: (v: string) => void;
    const load = vi.fn().mockResolvedValueOnce('protected').mockRejectedValue(new ApiError('denied', status));
    const a = renderHook(() => useObservabilityQuery('diagnostics', load));
    const b = renderHook(() => useObservabilityQuery('finops', () => Promise.resolve('financial')));
    const d = renderHook(() => useObservabilityQuery('retention', () => Promise.resolve('policy')));
    const c = renderHook(() => useObservabilityQuery('tracing', () => new Promise<string>(r => { resolve = r; })));
    await flush();
    expect(b.result.current.data).toBe('financial');
    act(() => a.result.current.refresh());
    await flush();
    expect(b.result.current.data).toBeUndefined();
    expect(d.result.current.data).toBeUndefined();
    await act(async () => resolve('obsolete protected result'));
    expect(c.result.current.data).toBeUndefined();
});
it('initial role denial does not revoke another independently permitted scope', async () => {
    const allowed = renderHook(() => useObservabilityQuery('health', () => Promise.resolve('permitted')));
    const denied = renderHook(() => useObservabilityQuery('configuration', () => Promise.reject(new ApiError('denied', 403))));
    await flush();
    expect(allowed.result.current.data).toBe('permitted');
    expect(denied.result.current.error).toBe('denied');
});
it('an intervening outage cannot hide subsequent permission loss from sibling sections', async () => {
    const load = vi.fn().mockResolvedValueOnce('authorized').mockRejectedValueOnce(new ApiError('outage', 503)).mockRejectedValue(new ApiError('denied', 403));
    const a = renderHook(() => useObservabilityQuery('financial', load));
    const b = renderHook(() => useObservabilityQuery('health', () => Promise.resolve('protected')));
    await flush();
    act(() => a.result.current.refresh());await flush();
    expect(b.result.current.data).toBe('protected');
    act(() => a.result.current.refresh());await flush();
    expect(b.result.current.data).toBeUndefined();
});
it.each([401, 403])('M16 late %i invalidates every Settings query family', async status => {
    const keys = ['overview-health', 'overview-active', 'overview-recent', 'tracing', 'reliability', 'slos',
        'financial-sources', 'billing-summary', 'pricing-status', 'reconciliation', 'unit-economics',
        'finops-controls', 'retention', 'integrations', 'access-profile', 'access-operational', 'access-config', 'timeline'];
    const resolves: ((value: string) => void)[] = [];
    const loads = keys.map(() => vi.fn().mockResolvedValueOnce('protected').mockImplementationOnce(
        () => new Promise<string>(resolve => { resolves.push(resolve); })));
    const deny = vi.fn().mockResolvedValueOnce('permitted').mockRejectedValue(new ApiError('denied', status));
    const sections = renderHook(() => keys.map((key, i) => useObservabilityQuery(key, loads[i])));
    const trigger = renderHook(() => useObservabilityQuery('run:revocation', deny));
    await flush();
    expect(sections.result.current.every(query => query.data === 'protected')).toBe(true);
    act(() => sections.result.current.forEach(query => query.refresh()));
    await flush();
    expect(resolves).toHaveLength(keys.length);
    act(() => trigger.result.current.refresh());
    await flush();
    expect(sections.result.current.every(query => query.data === undefined)).toBe(true);
    await act(async () => resolves.forEach(resolve => resolve('obsolete protected result')));
    expect(sections.result.current.every(query => query.data === undefined)).toBe(true);
});

it('replacement access retry clears scope, cancels older reads and rejects late authority', async () => {
    const pending: { signal: AbortSignal; resolve: (v: string) => void }[] = [];
    const load = vi.fn((signal: AbortSignal) => new Promise<string>(resolve => pending.push({ signal, resolve })));
    const h = renderHook(() => useObservabilityQuery('console-access', load, undefined, true, 'replace'));
    await act(async () => pending[0].resolve('initial scope'));
    expect(h.result.current.data).toBe('initial scope');
    act(() => h.result.current.refresh());
    expect(h.result.current.data).toBeUndefined();
    act(() => h.result.current.refresh());
    expect(pending[1].signal.aborted).toBe(true);
    await act(async () => pending[1].resolve('obsolete privileged scope'));
    expect(h.result.current.data).toBeUndefined();
    await act(async () => pending[2].resolve('new scope'));
    expect(h.result.current.data).toBe('new scope');
});
it.each([502, 503, 504])('read service failure %i is distinct from authentication', async status => {
    const h = renderHook(() => useObservabilityQuery('access', () => Promise.reject(new ApiError('unavailable', status))));
    await flush(); expect(h.result.current.error).toBe('unavailable');
});
it('network failure is a service-unavailable state', async () => {
    const h = renderHook(() => useObservabilityQuery('access', () => Promise.reject(new TypeError('Failed to fetch'))));
    await flush(); expect(h.result.current.error).toBe('unavailable');
});
