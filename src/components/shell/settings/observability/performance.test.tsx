import { it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import { AppStateProvider } from '../../../../state/AppState';
import { SettingsModal } from '../SettingsModal';
import ObservabilitySettings from './ObservabilitySettings';
import { RunTable } from './RunTable';
import { RunDetail } from './RunDetail';
import { mockApi, run, runId, at } from './fixtures.test-support';
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('records five local render repetitions with bounded 25-row fixtures', async () => {
    const api = mockApi();
    const times: Record<string, number[]> = { settings: [], observability: [], table: [], detail: [] };
    for (let i = 0; i < 5; i++) {
        let start = performance.now();
        const before = api.mock.calls.length;
        render(<AppStateProvider><SettingsModal open onOpenChange={() => { }}/></AppStateProvider>);
        await screen.findByRole('heading', { name: 'Settings' });
        times.settings.push(performance.now() - start);
        expect(api.mock.calls.slice(before).filter(c => String(c[0]).includes('/observability/'))).toHaveLength(0);
        cleanup();
        start = performance.now();
        render(<ObservabilitySettings />);
        await screen.findAllByRole('button', { name: /Open run/ });
        times.observability.push(performance.now() - start);
        cleanup();
        start = performance.now();
        render(<RunTable page={{ items: Array.from({ length: 25 }, (_, n) => run({ run_id: `11111111-1111-4111-8111-${String(n).padStart(12, '0')}` })), as_of: at }} onSelect={() => { }}/>);
        times.table.push(performance.now() - start);
        expect(screen.getAllByRole('row')).toHaveLength(26);
        cleanup();
        start = performance.now();
        render(<RunDetail runId={runId}/>);
        await screen.findByText('Operational timeline');
        times.detail.push(performance.now() - start);
        cleanup();
    }
    console.info('M8 jsdom local render ms', Object.fromEntries(Object.entries(times).map(([name, values]) => [name, { median: [...values].sort((a, b) => a - b)[2], max: Math.max(...values), repetitions: 5 }])));
});
