import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RouterProvider } from '../../lib/router';
import ObservabilityConsole, { consoleSections } from './ObservabilityConsole';
import App from '../../App';
// Canvas animation is unrelated to navigation and is not implemented by JSDOM.
vi.mock('../landing/PixelHero', () => ({ PixelHero: () => null }));
import { response, run, event, health as operationalHealth, config, at } from '../shell/settings/observability/fixtures.test-support';
import { sloFixture } from '../../api/sloFixture';
import { usage, health, controlPage, controlItem, billingSources, billingSummary, billingAmount, pricingStatus, reconciliationPage, unitPage } from '../../api/finops.test-support';

function mockConsole(options: { denied?: boolean; stale?: boolean; empty?: boolean; unavailable?: boolean } = {}) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
        const path = String(input).split('?')[0];
        if (path.endsWith('/sessions')) return response({ sessions: [] });
        if (path.endsWith('/access')) return response({ verified: true, capabilities: ['operational_metadata', 'technical_diagnostics', 'financial_data', 'effective_configuration'], environments: ['development'] });
        if (options.denied) return response({}, 403);
        if (options.unavailable) return response({}, 503);
        if (path.endsWith('/slos') || path.endsWith('/ledger-completeness')) {
            const s = { ...sloFixture, items: sloFixture.items.map(i => options.stale ? { ...i, state: 'STALE_DATA', source_status: 'STALE' } : i) };
            return response(path.endsWith('/ledger-completeness') ? s.items[0] : s);
        }
        if (path.endsWith('/config')) return response(config);
        if (path.endsWith('/health')) return response({ ...operationalHealth, writer_scope: 'process_local_anoc_unavailable', pending: null, admitted: null, terminal_pending: null, collector_configured: null, read_plane_state: 'healthy', anoc_state: 'unknown', collector_state: 'unknown', ...Object.fromEntries(['accepted', 'coalesced', 'attempts', 'persisted', 'failed', 'retries', 'rejected', 'terminal_unpersisted', 'shutdown_incomplete', 'invalid'].map(key => [key, null])) });
        if (path.endsWith('/active') || path.endsWith('/runs')) return response({ items: options.empty ? [] : [run()], as_of: at });
        if (path.endsWith('/timeline')) return response({ items: [event], truncated_count: 0 });
        if (path.endsWith('/retention')) return response({ run_id: run().run_id, instrumentation: 'UNKNOWN', cloud_trace: 'NOT_LIVE_CONFIRMED', exceptional_archive: 'UNAVAILABLE', production_binding: 'NOT_DEPLOYED' });
        if (path.endsWith('/runtime-usage')) return response({ ...usage, group: 'agent', items: [{ ...usage.items[0], dimension: 'team_manager' }] });
        if (path.endsWith('/ledger-health')) return response(health);
        if (path.endsWith('/billing-health')) return response(billingSources);
        if (path.endsWith('/billing-summary')) return response({ ...billingSummary, amounts: [{ ...billingAmount, net: '50', eur_net: '50' }] });
        if (path.endsWith('/pricing-status')) return response(pricingStatus);
        if (/\/(budgets|forecasts|anomalies)$/.test(path)) return response({ ...controlPage, state: 'AVAILABLE', items: [{ ...controlItem, kind: path.endsWith('/budgets') ? 'BUDGET' : 'FORECAST', forecast: '75' }] });
        if (path.endsWith('/reconciliation')) return response(reconciliationPage);
        if (path.endsWith('/unit-economics')) return response(unitPage);
        return response(run());
    });
}
function mount(path = '/resources/observability') { window.history.replaceState({}, '', path); return render(<RouterProvider><ObservabilityConsole /></RouterProvider>); }
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/'); });
it('direct route and refresh use Resources shell without ANOC chrome', async () => {
    mockConsole(); window.history.replaceState({}, '', '/resources/observability');
    const view = render(<App />); await screen.findByRole('main', { name: 'Observability Console' }, { timeout: 5000 });
    expect(document.querySelector('[data-shell=resources]')).toBeInTheDocument();
    expect(document.documentElement).toHaveClass('resources-dark');
    expect(screen.getByRole('link', { name: 'Open app' })).toHaveAttribute('href', '/app');
    for (const label of ['New chat', 'Projects', 'Scheduler', 'Chats', 'Return to workspace']) expect(screen.queryByText(label)).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'Settings' })).not.toBeInTheDocument();
    expect(screen.queryByText('Reasoning Steps')).not.toBeInTheDocument();
    view.unmount(); render(<App />); await screen.findByRole('main', { name: 'Observability Console' }, { timeout: 5000 });
});
it('all nine URL-backed sections work and history selects the requested view', async () => {
    mockConsole(); mount();
    for (const section of consoleSections) {
        fireEvent.click(screen.getByRole('link', { name: section }));
        expect(window.location.pathname).toBe('/resources/observability/' + section.toLowerCase());
        expect(screen.getByRole('link', { name: section })).toHaveAttribute('aria-current', 'page');
        await waitFor(() => expect(screen.queryAllByText(/Loading diagnostics/)).toHaveLength(0));
    }
    act(() => { window.history.replaceState({}, '', '/resources/observability/agents'); window.dispatchEvent(new PopStateEvent('popstate')); });
    expect(screen.getByRole('heading', { name: 'Agents' })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Reasoning Steps');
});
it('Overview has authoritative KPIs, safe execution and bounded request ownership', async () => {
    const stored = vi.fn(); vi.stubGlobal('localStorage', { setItem: stored });
    const fetcher = mockConsole(); mount();
    await screen.findAllByText('50 EUR');
    expect(screen.queryByText('P95 Turn Latency')).not.toBeInTheDocument();
    expect(screen.getByText('Financial Coverage')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Token Usage by Agent' })).toBeInTheDocument();
    expect(screen.getByText('Total Tokens')).toBeInTheDocument();
    await screen.findByText('Recent Runs');
    for (const suffix of ['/slos', '/budgets', '/forecasts', '/anomalies', '/runs']) expect(fetcher.mock.calls.filter(([p]) => String(p).split('?')[0].endsWith(suffix))).toHaveLength(1);
    const activeUrl = String(fetcher.mock.calls.find(([p]) => String(p).split('?')[0].endsWith('/active'))?.[0]);
    expect(activeUrl).not.toContain('since=');
    expect(activeUrl).not.toContain('until=');
    expect(fetcher.mock.calls.filter(([p]) => String(p).split('?')[0].endsWith('/runtime-usage'))).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([p]) => String(p).split('?')[0].endsWith('/billing-summary'))).toHaveLength(1);
    expect(fetcher.mock.calls.length).toBeLessThanOrEqual(11);
    expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
    expect(stored).not.toHaveBeenCalled();
});
it.each(['Traces', 'Agents', 'Reliability', 'SLOs', 'FinOps', 'Alerts', 'Diagnostics', 'Configuration'])('%s denial remains safe', async section => {
    mockConsole({ denied: true }); mount('/resources/observability/' + section.toLowerCase());
    await screen.findAllByRole('alert'); expect(document.body.textContent).not.toContain('50 EUR');
    expect(document.body.textContent).not.toContain('PRIVATE_REASONING');
});
it('empty traces and unavailable sources are distinct; stale SLOs keep backend state', async () => {
    mockConsole({ empty: true }); const view = mount('/resources/observability/traces');
    await screen.findByText('No active runs.'); view.unmount(); vi.restoreAllMocks();
    mockConsole({ unavailable: true }); const unavailable = mount('/resources/observability/reliability');
    await screen.findAllByRole('alert'); unavailable.unmount(); vi.restoreAllMocks();
    mockConsole({ stale: true }); mount('/resources/observability/slos'); await screen.findAllByText('Stale data');
});
it('environment/time selectors apply bounded scope to independent service GETs', async () => {
    vi.stubEnv('VITE_OBSERVABILITY_API_BASE_URL', 'http://127.0.0.1:8001'); const fetcher = mockConsole(); mount();
    await screen.findByRole('option', { name: 'development' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Console environment' }), { target: { value: 'development' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Console time range' }), { target: { value: '1' } });
    await waitFor(() => expect(fetcher.mock.calls.some(([p]) => String(p).includes('environment=development') && String(p).includes('since='))).toBe(true));
    expect(fetcher.mock.calls.every(([p]) => String(p).startsWith('http://127.0.0.1:8001/'))).toBe(true);
});
it('revocation clears protected data and ignores older delayed responses across panels', async () => {
    const fetcher = mockConsole(); mount(); await screen.findAllByText('50 EUR');
    let resolve: (r: Response) => void = () => {};
    fetcher.mockImplementation(input => {
        if (String(input).includes('/runs')) return new Promise<Response>(r => { resolve = r; });
        return Promise.resolve(response({}, 401));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Refresh console' }));
    await screen.findAllByRole('alert');
    await act(async () => resolve(response({ items: [run()], as_of: at })));
    expect(screen.queryAllByText('50 EUR')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /Open run/ })).not.toBeInTheDocument();
});
it('records local Overview render including asynchronous reads', async () => {
    mockConsole(); const start = performance.now(); mount(); await screen.findAllByText('50 EUR');
    console.info('OBSERVABILITY_OVERVIEW_LOCAL_RENDER_MS=' + (performance.now() - start));
});

it('Resources menu opens console and Open app returns to the unchanged app shell', async () => {
    mockConsole(); window.history.replaceState({}, '', '/'); render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Open menu' }, { timeout: 5000 }));
    const mobile = screen.getByRole('navigation', { name: 'Mobile' });
    await user.click(within(mobile).getByRole('button', { name: 'Resources' }));
    await user.click(await within(mobile).findByRole('link', { name: /Observability/ }));
    await screen.findByRole('main', { name: 'Observability Console' });
    expect(window.location.pathname).toBe('/resources/observability');
    expect(document.querySelector('[data-shell=resources]')).toBeInTheDocument();
    expect(document.documentElement).toHaveClass('resources-dark');
    await user.click(screen.getByRole('link', { name: 'Open app' }));
    await screen.findByRole('button', { name: /New chat/i }, { timeout: 5000 });
    expect(window.location.pathname).toBe('/app');
    expect(document.querySelector('[data-shell=resources]')).not.toBeInTheDocument();
    expect(document.documentElement).not.toHaveClass('resources-dark');
    expect(screen.queryByRole('main', { name: 'Observability Console' })).not.toBeInTheDocument();
});
it('unverified Resources visitors see authentication state without operational reads', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({}, 401));
    mount('/resources/observability/finops');
    await screen.findByText(/Authentication required/);
    expect(fetcher.mock.calls.every(([url]) => String(url).endsWith('/access'))).toBe(true);
    expect(screen.queryAllByText('50 EUR')).toHaveLength(0);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
});

it('never fetches financial or archive/config sources without their verified capability', async () => {
    const fetcher = mockConsole();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((input, init) => String(input).endsWith('/access') ? Promise.resolve(response({ verified: true, capabilities: ['operational_metadata'], environments: ['development'] })) : original(input, init));
    mount();
    await screen.findByText('Recent Runs');
    await screen.findAllByRole('alert');
    expect(fetcher.mock.calls.some(([url]) => /\/(finops|archive)\//.test(String(url)))).toBe(false);
    expect(screen.queryAllByText('50 EUR')).toHaveLength(0);
});
it('financial source failure preserves successful actuals and runs; 30-day refresh scopes real reads', async () => {
    const fetcher = mockConsole(); const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((input, init) => String(input).split('?')[0].endsWith('/forecasts') ? Promise.resolve(response({}, 503)) : original(input, init));
    mount(); await screen.findAllByText('50 EUR');
    await screen.findByText('Forecasts source failed. Other sources remain available.');
    expect(screen.getByRole('button', { name: /Open run/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Console time range' }), { target: { value: '30' } });
    await waitFor(() => {
        const url = fetcher.mock.calls.map(([u]) => String(u)).filter(u => u.includes('/runtime-usage')).at(-1)!;
        const p = new URL(url, 'http://localhost').searchParams;
        expect((Date.parse(p.get('end')!) - Date.parse(p.get('start')!)) / 86400000).toBe(30);
    });
    const before = fetcher.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh console' }));
    await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(before));
    await screen.findByText(/Last updated \d/);
});

it.each([
    [401, /Authentication required/],
    [403, /Authenticated identity is not permitted/],
    [502, /Observability service unavailable/],
    [503, /Observability service unavailable/],
])('access failure %i is classified safely and never mounts protected panels', async (status, message) => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({}, status as number));
    mount(); await screen.findByText(message as RegExp);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(fetcher.mock.calls.every(([url]) => String(url).endsWith('/access'))).toBe(true);
});
it('access retry recovers after a read-plane outage using a fresh server scope', async () => {
    const fetcher = mockConsole(); const original = fetcher.getMockImplementation()!;
    let offline = true;
    fetcher.mockImplementation((input, init) => offline ? Promise.resolve(response({}, 503)) : original(input, init));
    mount(); await screen.findByText(/Observability service unavailable/);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    offline = false; fireEvent.click(screen.getByRole('button', { name: 'Retry access' }));
    await screen.findByText('Recent Runs');
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/access'))).toHaveLength(2);
});

it('financial-only server scope loads permitted FinOps without operational or technical reads', async () => {
    const fetcher = mockConsole(); const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((input, init) => String(input).endsWith('/access') ? Promise.resolve(response({ verified: true, capabilities: ['financial_data'], environments: ['development'] })) : original(input, init));
    mount('/resources/observability/finops');
    await screen.findAllByText('50 EUR');
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/finops/'))).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => /\/(runs|active|archive|config|policies)(?:[/?]|$)/.test(String(url)))).toBe(false);
});
it('technical server scope preserves run visibility while financial panels stay denied', async () => {
    const fetcher = mockConsole(); const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation((input, init) => String(input).endsWith('/access') ? Promise.resolve(response({ verified: true, capabilities: ['operational_metadata', 'technical_diagnostics'], environments: ['development'] })) : original(input, init));
    mount(); await screen.findByText('Recent Runs');
    await screen.findByRole('button', { name: /Open run/ });
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/finops/'))).toBe(false);
    expect(screen.queryAllByText('50 EUR')).toHaveLength(0);
});
