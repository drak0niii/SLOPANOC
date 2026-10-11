import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { TokensByAgent, CostComposition, AttentionList, decimalShare, SloSummary } from './ConsoleVisuals';
import { decodeUsage, decodeBillingSummary } from '../../api/finops';
import { usage, group, billingAmount, billingSummary, financialSource } from '../../api/finops.test-support';
import { sloFixture } from '../../api/sloFixture';
import { decodeSLOPage } from '../../api/sloContract';
import { RouterProvider } from '../../lib/router';
afterEach(cleanup);
it('ranks real totals and labels unknown calls without inventing values', () => {
    const item = (dimension: string, value: number | null) => ({ ...group, dimension, quantities: { ...group.quantities, total_tokens: { observed: value, known: value == null ? 0 : 95, unknown: value == null ? 100 : 5 } } });
    const p = decodeUsage({ ...usage, group: 'agent', items: [item('incident_manager', 100), item('team_manager', 300), item('problem_manager', null)] });
    render(<TokensByAgent page={p} />);
    const rows = screen.getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('Team Manager');
    expect(rows[1]).toHaveTextContent('Incident Manager');
    expect(rows[2]).toHaveTextContent('—');
    expect(screen.getByText('110 UNKNOWN CALLS')).toBeInTheDocument();
    expect(screen.queryByText(/online|previous period/i)).not.toBeInTheDocument();
});
it('shows exact money and per-currency composition; signed costs never become a donut', () => {
    expect(decimalShare('0.1', '0.3')).toBe(33.33);
    expect(decimalShare('-1', '3')).toBeNull();
    expect(decimalShare('0', '0')).toBeNull();
    const p = decodeBillingSummary({ ...billingSummary, source: { ...financialSource, state: 'STALE' }, amounts: [{ ...billingAmount, net: '0.10' }, { ...billingAmount, category: 'MODEL_AI', net: '0.20' }, { ...billingAmount, currency: 'USD', net: '-1.50', eur_net: null, eur_state: 'EUR_REPORTING_UNAVAILABLE' }] });
    render(<CostComposition page={p} />);
    expect(screen.getByText('STALE')).toBeInTheDocument();
    expect(screen.getByText('33.33%')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Actual cost composition in EUR' })).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: 'Actual cost composition in USD' })).not.toBeInTheDocument();
    expect(screen.getByText(/Signed credits/)).toBeInTheDocument();
});
it('uses backend SLO states and caps overview attention at five', () => {
    const page = decodeSLOPage({ ...sloFixture, items: sloFixture.items.map(i => ({ ...i, state: 'STALE_DATA', source_status: 'STALE' })) });
    render(<RouterProvider><AttentionList slo={{ loading: false, data: page }} financial={{ loading: false }} health={{ loading: false }} /><SloSummary page={page} /></RouterProvider>);
    expect(within(screen.getByRole('list', { name: 'Overview attention items' })).getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByRole('link', { name: /additional states/ })).toHaveAttribute('href', '/resources/observability/alerts');
    expect(screen.getAllByText('STALE').length).toBeGreaterThan(0);
    expect(screen.queryByText('PASS')).not.toBeInTheDocument();
});
it('does not draw cost composition for missing or paginated billing', () => {
    render(<CostComposition page={decodeBillingSummary({ ...billingSummary, next_cursor: 'page' })} />);
    expect(screen.getByText('Incomplete financial page')).toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
