import { it, expect } from 'vitest';
import { decodePolicies, decodeAccess } from './observabilityPolicy';
import { policyFixture } from './observabilityPolicy.test-support';
it('projects backend policy and capability DTOs without unknown content or identity', () => {
    const marker = 'PRIVATE_PROMPT_RESPONSE_TEAMS_BEARER_REASONING';
    const p = decodePolicies({ ...policyFixture, secret: marker, prompt: marker });
    expect(JSON.stringify(p)).not.toContain(marker);
    expect(p.retention.find(r => r.dataset === 'cloud_trace')?.horizon).toBe(30);
    expect(p.retention.find(r => r.dataset === 'exceptional_archive')?.horizon).toBe(90);
    expect(p.retention.find(r => r.dataset === 'analytical_metrics')?.semantics).toBe('minimum_analytical_access');
    const a = decodeAccess({ verified: true, capabilities: ['effective_configuration'], environments: ['production'], subject: marker, email: marker, role: 'Admin' });
    expect(a.capabilities).toEqual(['effective_configuration']);
    expect(JSON.stringify(a)).not.toContain(marker);
    expect(a).not.toHaveProperty('role');
});
it('rejects malformed policies and unrecognized capabilities rather than widening scope', () => {
    expect(() => decodePolicies({ ...policyFixture, production_archive: 'LIVE' })).toThrow();
    expect(() => decodePolicies({ ...policyFixture, sdk_capture_percent: 10 })).toThrow();
    expect(() => decodePolicies({ ...policyFixture, retention: policyFixture.retention.slice(1) })).toThrow();
    expect(() => decodeAccess({ verified: false, capabilities: ['financial_data'], environments: ['production'] })).toThrow();
    expect(() => decodeAccess({ verified: true, capabilities: ['all'], environments: ['production'] })).toThrow();
});

it('keeps instrumentation, cloud and archive states separate and strips arbitrary fields', async () => {
    const { decodeRunRetention } = await import('./observabilityPolicy');
    const value = { run_id: '00000000-0000-4000-8000-000000000001', instrumentation: 'COMPLETE', cloud_trace: 'NOT_LIVE_CONFIRMED', exceptional_archive: 'PENDING_OR_DEGRADED', production_binding: 'NOT_DEPLOYED', prompt: 'PRIVATE_PROMPT', signed_url: 'PRIVATE_URL' };
    const safe = decodeRunRetention(value);
    expect(safe.instrumentation).toBe('COMPLETE');
    expect(safe.exceptional_archive).toBe('PENDING_OR_DEGRADED');
    expect(JSON.stringify(safe)).not.toContain('PRIVATE_');
    expect(() => decodeRunRetention({ ...value, cloud_trace: 'CONFIRMED' })).toThrow();
});
