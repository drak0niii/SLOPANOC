import { expect, it, vi } from 'vitest';
import serialized from '../../backend/tests/fixtures/hybrid_activity.json?raw';
import { decodeHybridActivity, getHybridActivity } from './observabilityHybrid';
import { getObservabilityJson } from './observabilityClient';
vi.mock('./observabilityClient', () => ({ getObservabilityJson: vi.fn() }));
const fixture = JSON.parse(serialized);
it('accepts the shared Python DTO, including truthful count scope and freshness', () => {
    expect(decodeHybridActivity(fixture)).toEqual(fixture);
});
it.each(['prompt', 'response', 'authorization', 'sql', 'chain_of_thought'])('rejects forbidden or unknown field %s at every DTO layer', field => {
    for (const bad of [{ ...fixture, [field]: 'FORBIDDEN' }, { ...fixture, runs: [{ ...fixture.runs[0], [field]: 'FORBIDDEN' }] },
        { ...fixture, agents: [{ ...fixture.agents[0], [field]: 'FORBIDDEN' }] }, { ...fixture, debt: { ...fixture.debt, [field]: 'FORBIDDEN' } }]) expect(() => decodeHybridActivity(bad)).toThrow();
});
it('rejects bad schema, identities, counters and duplicate projections', () => {
    for (const bad of [{ ...fixture, schema_version: 2 }, { ...fixture, environment: 'foreign' },
        { ...fixture, completed_in_page: -1 }, { ...fixture, runs: [fixture.runs[0], fixture.runs[0]] },
        { ...fixture, runs: [{ ...fixture.runs[0], trace_id: '0'.repeat(32) }] },
        { ...fixture, agents: [{ ...fixture.agents[0], model_attempts_started: '1' }] }]) expect(() => decodeHybridActivity(bad)).toThrow();
});
it('uses only the independent GET read client', async () => {
    vi.mocked(getObservabilityJson).mockResolvedValue(fixture);
    expect(await getHybridActivity()).toEqual(fixture);
    expect(getObservabilityJson).toHaveBeenCalledWith('/api/observability/hybrid/activity?limit=50', undefined);
});
