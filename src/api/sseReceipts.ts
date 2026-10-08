import { apiFetch } from './client';
/** Best-effort diagnostic receipt. No retry, identity headers, response parsing or chat dependency. */
export function submitSSEReceipt(runId: string): void {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(runId)) return;
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 3000);
    try {
        void apiFetch('/api/observability/sse-receipts', { method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run_id: runId, event: 'message.completed' }),
            signal: controller.signal }).catch(() => undefined).finally(() => clearTimeout(deadline));
    } catch { clearTimeout(deadline); }
}
