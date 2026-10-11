import { describe, expect, it } from 'vitest';
import { decodeRunAuditPage } from './observabilityAudit';
const run = '00000000-0000-4000-8000-000000000001';
function page(outcome = 'UNKNOWN') { return {
    schema_version: 1, run_id: run, environment: 'development', as_of: '2026-10-11T12:00:00Z',
    actions: [], events: [{ processed_at: '2026-10-11T12:00:00Z', event: { schema_version: 1, event_class: 'AUDIT',
        event_id: '00000000-0000-4000-8000-000000000002', event_type: 'action.outcome_unknown', run_id: run,
        environment: 'development', timestamp: '2026-10-11T11:59:00Z', payload_safe: { source_record_id: run,
            execution: { consistency_class: 'D', outcome } } } }], next_cursor: null, completeness: 'PARTIAL', consistency_notice: 'eventual' }; }
describe('run audit data contract', () => {
    it('exposes unresolved dispatch as UNKNOWN without rewriting historical facts', () => {
        const p = { ...page(), actions: [{ execution_id: run, state: 'DISPATCHING', outcome: 'UNKNOWN', updated_at: '2026-10-11T12:00:00Z' }] };
        expect(decodeRunAuditPage(p).actions[0].outcome).toBe('UNKNOWN');
        expect(() => decodeRunAuditPage({ ...p, actions: [{ ...p.actions[0], outcome: 'COMPLETED' }] })).toThrow();
    });
    it('preserves UNKNOWN and partial availability', () => { const r = decodeRunAuditPage(page()); expect(r.events[0].outcome).toBe('UNKNOWN'); expect(r.completeness).toBe('PARTIAL'); });
    it('rejects inferred complete coverage', () => { expect(() => decodeRunAuditPage({ ...page(), completeness: 'COMPLETE' })).toThrow(); });
    it('rejects cross-run data', () => { const p = page(); p.events[0].event.run_id = '00000000-0000-4000-8000-000000000099'; expect(() => decodeRunAuditPage(p)).toThrow(); });
    it('rejects raw outcome prose', () => { expect(() => decodeRunAuditPage(page('PRIVATE RESPONSE'))).toThrow(); });
    it('does not expose arbitrary content as summary fields', () => { const p = page(); Object.assign(p.events[0].event.payload_safe, { prompt: 'PRIVATE' }); expect(() => decodeRunAuditPage(p)).toThrow(); });
});
