import { expect, it } from 'vitest';
import serialized from '../../backend/tests/fixtures/observability_health.json?raw';
import { decodeHealth } from '../components/shell/settings/observability/contract';

// Python tests compare these fixtures to the actual FastAPI serialized response.
const fixtures = JSON.parse(serialized) as Record<string, Record<string, unknown>>;
it.each(Object.keys(fixtures))('accepts canonical backend health serialization: %s', key => {
    const dto = decodeHealth(fixtures[key]);
    expect(dto.persistence).toBe(fixtures[key].persistence);
    expect(dto.pending).toBe(fixtures[key].pending);
    expect(dto.accepted).toBe(fixtures[key].accepted);
    expect(dto.archive_state).toBe('unknown');
    expect(dto.source_freshness).toBe('unknown');
});
it('rejects omitted or malformed required nullable fields and invented reader counters', () => {
    for (const key of ['pending', 'admitted', 'terminal_pending', 'collector_configured', 'accepted']) {
        const dto = { ...fixtures.reader_unavailable };
        delete dto[key];
        expect(() => decodeHealth(dto)).toThrow();
        expect(() => decodeHealth({ ...fixtures.reader_unavailable, [key]: '0' })).toThrow();
    }
    expect(() => decodeHealth({ ...fixtures.reader_unavailable, accepted: 0 })).toThrow();
    expect(() => decodeHealth({ ...fixtures.reader_unavailable, archive_state: 'healthy' })).toThrow();
    expect(() => decodeHealth({ ...fixtures.reader_unavailable, source_freshness: 'healthy' })).toThrow();
});
