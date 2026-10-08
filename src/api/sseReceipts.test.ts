import { it, expect, vi, afterEach } from 'vitest';
import { submitSSEReceipt } from './sseReceipts';
const runId = '11111111-1111-4111-8111-111111111111';
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
it('receipt has only run correlation and canonical event, no auth claims or business content', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }));
    submitSSEReceipt(runId); await Promise.resolve();
    const init = fetcher.mock.calls[0][1];
    expect(JSON.parse(String(init?.body))).toEqual({ run_id: runId, event: 'message.completed' });
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' });
});
it('failure is best effort and invalid correlation rejected', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('SYNTHETIC_FAILURE_MARKER'));
    expect(() => submitSSEReceipt(runId)).not.toThrow();
    await Promise.resolve(); await Promise.resolve();
    submitSSEReceipt('invalid'); expect(fetcher).toHaveBeenCalledTimes(1);
});
it('hung receipt is bounded and aborted', async () => {
    vi.useFakeTimers(); const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => undefined));
    submitSSEReceipt(runId); await vi.advanceTimersByTimeAsync(3000);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
});
