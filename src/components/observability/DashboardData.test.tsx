import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { BilledSummary, UsageSummary, observedQuantity, sumDecimals } from './DashboardData';
import { decodeBillingSummary, decodeUsage } from '../../api/finops';
import { usage, group, billingAmount, billingSummary, financialSource } from '../../api/finops.test-support';
afterEach(cleanup);
it('uses provider total once, preserves unknown quantities and rejects paginated aggregates', () => {
    const p = decodeUsage({ ...usage, items: [{ ...group, quantities: { ...group.quantities, total_tokens: { observed: 123, known: 95, unknown: 5 }, cached_tokens: { observed: 100, known: 95, unknown: 5 } } }] });
    expect(observedQuantity(p, 'total_tokens')).toBe(123);
    expect(observedQuantity(p, 'output_tokens')).toBeNull();
    expect(observedQuantity({ ...p, next_cursor: 'page' }, 'total_tokens')).toBeNull();
    render(<UsageSummary page={p} />);
    expect(screen.getByText('123')).toBeInTheDocument();
    expect(screen.getByText(/5 calls with unknown total quantity/)).toBeInTheDocument();
});
it('aggregates exact signed decimals and keeps multiple currencies separate', () => {
    expect(sumDecimals(['9007199254740993.01', '-0.02', '0.01'])).toBe('9007199254740993.00');
    const p = decodeBillingSummary({ ...billingSummary, source: { ...financialSource, state: 'STALE' }, amounts: [billingAmount, { ...billingAmount, currency: 'USD', net: '2.12', eur_net: null, eur_state: 'EUR_REPORTING_UNAVAILABLE' }] });
    render(<BilledSummary page={p} />);
    expect(screen.getByText('0.08 EUR')).toBeInTheDocument();
    expect(screen.getByText('2.12 USD')).toBeInTheDocument();
    expect(screen.getByText(/Stale/)).toBeInTheDocument();
    expect(screen.queryByText('2.20 EUR')).not.toBeInTheDocument();
});
it('does not turn empty or missing billing/usage into zero', () => {
    expect(observedQuantity(decodeUsage({ ...usage, items: [] }), 'total_tokens')).toBeNull();
    render(<BilledSummary page={decodeBillingSummary(billingSummary)} />);
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.queryByText('0 EUR')).not.toBeInTheDocument();
});
