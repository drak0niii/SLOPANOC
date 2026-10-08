import { it, expect, vi, afterEach } from "vitest";
import { getActiveRuns, getRecentRuns, getRun, getRunTimeline, getSessionRuns, getObservabilityHealth, getObservabilityConfig } from './observability';
import { runId, run, health, config, event, at, response } from '../components/shell/settings/observability/fixtures.test-support';
import { ApiError } from './client';
afterEach(() => vi.restoreAllMocks());
it('uses all seven GET routes, validated DTOs, no auth headers and cancellation', async () => {
    const bodies = [{ items: [run()], as_of: at }, { items: [], as_of: at }, run(), { items: [event], truncated_count: 0 }, { items: [], as_of: at }, health, config];
    const fetcher = vi.spyOn(globalThis, 'fetch');
    bodies.forEach(b => fetcher.mockResolvedValueOnce(response(b)));
    const signal = new AbortController().signal;
    await getActiveRuns({ limit: 25, status: 'STALLED' }, signal);
    await getRecentRuns({}, signal);
    await getRun(runId, signal);
    await getRunTimeline(runId, { cursor: 'YWJj==' }, signal);
    await getSessionRuns('session:one', { limit: 25 }, signal);
    await getObservabilityHealth(signal);
    await getObservabilityConfig(signal);
    expect(fetcher.mock.calls.map(c => c[0])).toEqual(['/api/observability/active?limit=25&status=STALLED', '/api/observability/runs', `/api/observability/runs/${runId}`, `/api/observability/runs/${runId}/timeline?cursor=YWJj%3D%3D`, '/api/observability/sessions/session%3Aone/runs?limit=25', '/api/observability/health', '/api/observability/config']);
    fetcher.mock.calls.forEach(c => expect(c[1]).toEqual({ signal }));
});
it.each([401, 403, 404, 500, 503])('normalizes %i without response content or diagnostic console output', async (status) => {
    const log = vi.spyOn(console, 'debug');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ userMessage: 'SECRET', errorCode: 'x', retryable: false, correlationId: 'secret' }, status));
    await expect(getRun(runId)).rejects.toMatchObject({ status, message: 'Diagnostic request unavailable.' });
    expect(log).not.toHaveBeenCalled();
});
it('forwards abort and rejects malformed or out-of-bounds requests', async () => {
    const c = new AbortController();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_p, init) => { expect(init?.signal).toBe(c.signal); throw new DOMException('Aborted', 'AbortError'); });
    c.abort();
    await expect(getActiveRuns({}, c.signal)).rejects.toHaveProperty('name', 'AbortError');
    expect(() => getActiveRuns({ limit: 101 })).toThrow();
    expect(() => getRun('bad')).toThrow();
    vi.mocked(fetch).mockResolvedValue(response({ items: 'bad' }));
    await expect(getActiveRuns()).rejects.toHaveProperty('kind', 'malformed');
});
it('retains network failure safely for presentation', async () => { vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Network failed')); await expect(getObservabilityHealth()).rejects.toBeInstanceOf(TypeError); expect(new ApiError('x', 403).status).toBe(403); });
