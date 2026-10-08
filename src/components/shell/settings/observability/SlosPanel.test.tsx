import { it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { SlosPanel } from './SlosPanel';
import { SLO_STATES } from '../../../../api/sloTypes';
import { decodeSLOPage } from '../../../../api/sloContract';
import { sloFixture } from '../../../../api/sloFixture';
const at = '2026-10-08T12:00:00Z';
function page(state: string = 'HEALTHY') { return { schema_version: 1, environment: 'development', evaluated_at: at, items: [{ schema_version: 1, slo_id: 'availability', name: 'Availability', objective: .995, objective_status: 'PROVISIONAL', window_seconds: 2419200, window_start: '2026-09-10T12:00:00Z', window_end: at, threshold_seconds: null, eligible: 1000, good: 995, bad: 5, unknown: 0, excluded: 1, current_value: .995, remaining_fraction: -.5, coverage: 1, burn_tiers: state === 'BURNING' ? ['fast'] : [], state, evaluated_at: at, source_last_updated: at, freshness_seconds: 300, source_status: 'AVAILABLE' }] }; }
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it.each(SLO_STATES.filter(s => s !== 'DEFINED_NOT_EVALUATED'))('renders backend %s values without changing truth', async state => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page(state)), { status: 200 }));
    render(<SlosPanel />); await screen.findByText('Availability');
    expect(screen.getAllByText(/Provisional/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('99.50%')).toHaveLength(2);
    expect(screen.getByText('-50.00%')).toBeInTheDocument();
    expect(screen.getByText(/rolling 28 days/)).toBeInTheDocument();
    if (state === 'BURNING') expect(screen.getByText('fast')).toBeInTheDocument();
});
it('cost definition awaits M10 with no fake percentage', async () => {
    const fixture = page('DEFINED_NOT_EVALUATED'); const row = fixture.items[0];
    Object.assign(row, { slo_id: 'cost_ledger_completeness', name: 'Cost-ledger completeness', objective_status: 'ARCHITECTURAL', current_value: null, remaining_fraction: null });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(fixture), { status: 200 }));
    render(<SlosPanel />); await screen.findByText('Data source available after M10');
    expect(screen.queryByText('100.00%')).not.toBeInTheDocument();
});
it.each([401,403])('permission %s renders scoped denial', async status => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status }));
    render(<SlosPanel />); await screen.findByRole('alert'); expect(screen.queryByText('Availability')).not.toBeInTheDocument();
});
it('decoder projects bounded fields and rejects incompatible, malformed or fabricated cost', () => {
    const fixture = page(); Object.assign(fixture.items[0], { prompt: 'SYNTHETIC_PRIVACY_MARKER', run_id: 'SYNTHETIC_PRIVACY_MARKER' });
    expect(JSON.stringify(decodeSLOPage(fixture))).not.toContain('SYNTHETIC_PRIVACY_MARKER');
    expect(() => decodeSLOPage({ ...fixture, schema_version: 2 })).toThrow();
    expect(() => decodeSLOPage({ ...fixture, items: [{ ...fixture.items[0], state: 'GREEN_FAKE' }] })).toThrow();
    expect(() => decodeSLOPage({ ...fixture, items: [{ ...fixture.items[0], state: 'DEFINED_NOT_EVALUATED' }] })).toThrow();
});

it('renders the captured backend DTO and measures five local full-panel repetitions', async () => {
    expect(decodeSLOPage(sloFixture).items).toHaveLength(16);
    const samples: number[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(sloFixture), { status: 200 }));
    for (let i = 0; i < 5; i++) {
        const started = performance.now();
        render(<SlosPanel />);
        await screen.findByText('Data source available after M10');
        samples.push(performance.now() - started);
        expect(screen.getAllByRole('article')).toHaveLength(16);
        cleanup();
    }
    samples.sort((a,b) => a-b);
    console.log('M9 local jsdom SLO panel ms', { median: samples[2], max: samples[4], repetitions: 5, cards: 16 });
});
