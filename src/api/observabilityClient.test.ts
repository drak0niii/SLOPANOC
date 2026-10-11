import { afterEach, expect, it, vi } from 'vitest';
import { getObservabilityJson, getObservabilityApiBaseUrl } from './observabilityClient';
import { getJson, postJson } from './client';
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); });
it('read base is separate from business base, including SSE receipt writes', async () => {
    vi.stubEnv('VITE_OBSERVABILITY_API_BASE_URL', 'http://127.0.0.1:8001/');
    vi.stubEnv('VITE_SLOPANOC_API_BASE_URL', 'http://127.0.0.1:8000');
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}'));
    await getObservabilityJson('/api/observability/config'); await getJson('/api/sessions'); await postJson('/api/observability/sse-receipts', {});
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['http://127.0.0.1:8001/api/observability/config', 'http://127.0.0.1:8000/api/sessions', 'http://127.0.0.1:8000/api/observability/sse-receipts']);
    expect(() => getObservabilityJson('/api/observability/sse-receipts')).toThrow();
});
it('unset development base uses the dedicated same-origin proxy; production fails closed', () => {
    vi.stubEnv('VITE_OBSERVABILITY_API_BASE_URL', '');
    vi.stubEnv('DEV', true); expect(getObservabilityApiBaseUrl()).toBe('');
    vi.stubEnv('DEV', false); vi.stubEnv('PROD', true); expect(getObservabilityApiBaseUrl).toThrow();
    for (const bad of ['javascript:alert(1)', 'https://user:password@example.com', 'https://example.com?token=x']) { vi.stubEnv('VITE_OBSERVABILITY_API_BASE_URL', bad); expect(getObservabilityApiBaseUrl).toThrow(); }
});
it('identical reads within a query bundle share only the in-flight request', async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(r => { resolve = r; }));
    const controller = new AbortController();
    const first = getObservabilityJson('/api/observability/runs', controller.signal), second = getObservabilityJson('/api/observability/runs', controller.signal);
    expect(fetcher).toHaveBeenCalledTimes(1); resolve(new Response('{}')); await Promise.all([first, second]);
    fetcher.mockResolvedValue(new Response('{}')); await getObservabilityJson('/api/observability/runs', controller.signal);
    expect(fetcher).toHaveBeenCalledTimes(2);
});

it('scoped subscribers share a read while one consumer may cancel independently', async () => {
    const { registerObservabilityScope } = await import('./observabilityScope');
    const scope = { environment: 'development', since: '2026-10-01T00:00:00.000Z', until: '2026-10-02T00:00:00.000Z' };
    let resolve!: (response: Response) => void;
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(r => { resolve = r; }));
    const a = new AbortController(), b = new AbortController();
    registerObservabilityScope(a.signal, scope); registerObservabilityScope(b.signal, scope);
    const first = getObservabilityJson('/api/observability/runs', a.signal);
    const cancelled = expect(first).rejects.toMatchObject({ name: 'AbortError' });
    const second = getObservabilityJson('/api/observability/runs', b.signal);
    expect(fetcher).toHaveBeenCalledTimes(1);
    a.abort(); await cancelled;
    expect((fetcher.mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(false);
    resolve(new Response('{"items":[]}')); expect(await second).toEqual({ items: [] });
    expect(fetcher.mock.calls[0][0]).toContain('environment=development');
});
it('cursor pages retain environment and time scope', async () => {
    const { registerObservabilityScope, scopedObservabilityPath } = await import('./observabilityScope');
    const controller = new AbortController();
    registerObservabilityScope(controller.signal, { environment: 'development', since: '2026-10-01T00:00:00.000Z', until: '2026-10-02T00:00:00.000Z' });
    const url = new URL(scopedObservabilityPath('/api/observability/runs?cursor=opaque', controller.signal), 'http://local');
    expect(url.searchParams.get('cursor')).toBe('opaque');
    expect(url.searchParams.get('environment')).toBe('development');
    expect(url.searchParams.get('since')).toBe('2026-10-01T00:00:00.000Z');
});

it('local reader requests preserve native first-party session transport without asserted authority', async () => {
    vi.stubEnv('DEV', true); vi.stubEnv('VITE_OBSERVABILITY_API_BASE_URL', '');
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
    await getObservabilityJson('/api/observability/access');
    expect(fetcher.mock.calls[0][0]).toBe('/api/observability/access');
    // Fetch defaults to same-origin credentials; no fabricated Authorization or roles.
    expect(fetcher.mock.calls[0][1]?.credentials ?? 'same-origin').toBe('same-origin');
    expect(fetcher.mock.calls[0][1]?.headers).toBeUndefined();
});
