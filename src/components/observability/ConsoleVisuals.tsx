import type { ReactNode } from 'react';
import { Activity, ArrowUpRight, CircleCheck, Coins, Gauge, Layers, ShieldCheck } from 'lucide-react';
import type { QueryState } from '../shell/settings/observability/useObservabilityQuery';
import type { BillingSummary, UsagePage } from '../../api/finops';
import type { ControlPage } from '../../api/financialControls';
import type { RunPage, HealthView } from '../../api/observabilityTypes';
import type { SLOPage } from '../../api/sloTypes';
import { Link } from '../../lib/router';
import { OBSERVABILITY_ROUTE } from '../../lib/routes';
import { StatusBadge } from '../shell/settings/observability/StatusBadge';
import { duration, name, timestamp } from '../shell/settings/observability/format';
import { observedQuantity, sumDecimals, agentLabels } from './DashboardData';

export const formatCount = (value: number | null | undefined) => value == null ? '—' : new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 }).format(value);
export const formatPercent = (value: number | null | undefined) => value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 2 }).format(value);
const categoryLabels: Record<string, string> = { MODEL_AI: 'Model & AI', CLOUD_RUN: 'Cloud Run', CLOUD_SQL: 'Cloud SQL', BIGQUERY: 'BigQuery', GCS: 'Cloud Storage', MONITORING_LOGGING: 'Monitoring & Logging', NETWORK: 'Network', OTHER: 'Other', UNMAPPED: 'Unmapped' };
const icons = { tokens: Layers, cost: Coins, runs: Activity, agents: CircleCheck, success: ShieldCheck, coverage: Gauge };
export function Kpi({ label, value, detail, kind, query, tone }: { label: string; value: ReactNode; detail: string; kind: keyof typeof icons; query: QueryState<unknown>; tone?: string }) {
    const Icon = icons[kind];
    return <article className="obs-kpi" data-tone={tone} aria-busy={query.loading}>
        <div className="obs-kpi-label"><span>{label}</span><Icon size={16} strokeWidth={1.6} aria-hidden="true" /></div>
        {query.loading && !query.data ? <div className="obs-skeleton obs-skeleton-value" aria-label={`${label} loading`} /> : <div className="obs-kpi-value" title={typeof value === 'string' ? value : undefined}>{query.error ? '—' : value}</div>}
        <p className="obs-kpi-note" title={detail}>{query.error ? (['denied', 'unauthorized'].includes(query.error) ? 'Access restricted' : 'Source unavailable') : detail}</p>
    </article>;
}
export function Panel({ title, subtitle, children, className = '', link }: { title: string; subtitle?: string; children: ReactNode; className?: string; link?: { label: string; path: string } }) {
    return <section className={`obs-panel ${className}`}><header className="obs-panel-heading"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{link && <Link to={`${OBSERVABILITY_ROUTE}/${link.path}`} className="obs-text-link">{link.label}<ArrowUpRight size={14} aria-hidden="true" /></Link>}</header>{children}</section>;
}
export function TokensByAgent({ page }: { page: UsagePage }) {
    const ranked = [...page.items].sort((a, b) => (b.quantities.total_tokens.observed ?? -Infinity) - (a.quantities.total_tokens.observed ?? -Infinity));
    const max = Math.max(...ranked.map(i => i.quantities.total_tokens.observed ?? 0), 0);
    const unknown = page.items.reduce((n, i) => n + i.quantities.total_tokens.unknown + i.attempts - i.captured_attempts, 0);
    return <><div className="obs-chart-summary"><span className="obs-chart-context">Reported token quantities</span><span className="obs-source-chip">{page.next_cursor ? 'PARTIAL PAGE' : unknown ? `${unknown} UNKNOWN CALLS` : observedQuantity(page, 'total_tokens') == null ? 'UNKNOWN QUANTITY' : 'REPORTED TOTALS'}</span></div>
        {!ranked.length && <div className="obs-empty">No data for selected period</div>}
        <div className="obs-ranked-chart" role="list" aria-label="Token Usage by Agent">{ranked.slice(0, 8).map(i => {
            const quantity = i.quantities.total_tokens;
            return <div role="listitem" className="obs-ranked-row" key={i.dimension}><div className="obs-ranked-label"><span title={`${quantity.unknown} unknown quantities`}>{agentLabels[i.dimension] ?? name(i.dimension)}</span><strong title={quantity.observed?.toLocaleString()}>{formatCount(quantity.observed)}</strong></div>
                <div className="obs-bar-track" role="img" aria-label={`${agentLabels[i.dimension] ?? i.dimension}: ${quantity.observed ?? 'Unknown'} observed tokens; ${quantity.unknown} unknown calls`}><div className="obs-bar" style={{ width: `${quantity.observed != null && max > 0 ? Math.max(0, quantity.observed / max * 100) : 0}%` }} /></div>

            </div>;
        })}</div>
        {ranked.length > 8 && <p className="obs-footnote">Top 8 observed attributions; all rows in Agents.</p>}
        <details className="obs-source-details"><summary>Usage coverage &amp; source</summary><p>{page.start} — {page.end} · {page.environment}. Total is the reported total_tokens component. Unknown quantities are not zero; components may overlap. {page.next_cursor ? 'Incomplete page; range aggregate unavailable.' : ''} Usage freshness is not published in this DTO.</p></details>
    </>;
}
// Percentages are presentation of already-billed amounts, never estimates or FX.
export function decimalShare(value: string, total: string): number | null {
    const scale = Math.max(value.split('.')[1]?.length ?? 0, total.split('.')[1]?.length ?? 0);
    const units = (s: string) => { const [a, b = ''] = s.split('.'); return BigInt(a + b.padEnd(scale, '0')); };
    const denominator = units(total), numerator = units(value);
    if (denominator <= 0n || numerator < 0n || numerator > denominator) return null;
    return Number((numerator * 10000n + denominator / 2n) / denominator) / 100;
}
export function CostComposition({ page }: { page: BillingSummary }) {
    const currencies = [...new Set(page.amounts?.map(a => a.currency))];
    if (!page.amounts?.length || page.next_cursor) return <div className="obs-empty">{page.next_cursor ? 'Incomplete financial page' : page.amounts === null ? 'Cost data unavailable' : 'No billed data for selected period'}<p>Actual cost composition appears when published billing data is available.</p></div>;
    return <>{page.source.state === 'STALE' && <span className="obs-source-chip" data-tone="warning">STALE</span>}{currencies.map(currency => {
        const amounts = page.amounts!.filter(a => a.currency === currency);
        const total = sumDecimals(amounts.map(a => a.net));
        const signed = amounts.some(a => a.net.startsWith('-'));
        const shares = amounts.map(a => signed ? null : decimalShare(a.net, total));
        return <div key={currency} className="obs-cost-group"><div className="obs-chart-summary"><strong>{total} <span>{currency}</span></strong><span className="obs-source-chip">{page.coverage_complete ? 'COMPLETE COVERAGE' : 'PARTIAL COVERAGE'}</span></div>
            {!signed && shares.every(s => s !== null) && <div className="obs-cost-stack" role="img" aria-label={`Actual cost composition in ${currency}`}>{amounts.map((a, index) => <div key={a.category} className={`obs-cost-segment obs-swatch-${index % 6}`} style={{ flexGrow: shares[index] ?? 0, flexBasis: 0 }} title={`${categoryLabels[a.category] ?? name(a.category)}: ${a.net} ${currency} (${shares[index]}%)`} />)}</div>}
            {signed && <p className="obs-footnote">Signed credits or adjustments · composition percentages unavailable</p>}
            <ul className="obs-cost-legend">{amounts.map((a, index) => <li key={a.category}><span className={`obs-swatch obs-swatch-${index % 6}`} aria-hidden="true" /><span>{categoryLabels[a.category] ?? name(a.category)}</span><strong>{a.net} <span>{currency}</span></strong><small>{shares[index] == null ? '—' : `${shares[index]!.toFixed(2)}%`}</small></li>)}</ul>
        </div>;
    })}<details className="obs-source-details"><summary>Financial source</summary><p>{page.start} — {page.end} · {page.source.state} · Source as of {page.source.as_of ?? 'Unavailable'}. Actual billed net cost; source tax separate. Currencies remain separate.</p></details></>;
}
export function RecentRunTable({ page, onSelect }: { page: RunPage; onSelect: (id: string) => void }) {
    if (!page.items.length) return <div className="obs-empty">No runs for selected period</div>;
    return <><div className="obs-table-scroll" tabIndex={0} aria-label="Recent runs scroll area"><table className="obs-runs-table"><caption className="sr-only">Recent durable runs</caption><thead><tr>{['Run', 'Observed agent', 'Status', 'Duration', 'Started'].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>{page.items.slice(0, 5).map(r => <tr key={r.run_id}><th scope="row"><button className="obs-run-link" aria-label={`Open run ${r.run_id}`} onClick={() => onSelect(r.run_id)}>{r.run_id.slice(0, 8)}<ArrowUpRight size={12} aria-hidden="true" /></button></th><td>{agentLabels[r.current_agent ?? ''] ?? name(r.current_agent)}</td><td><StatusBadge status={r.status} classification={r.classification} /></td><td className="obs-tabular">{duration(r.elapsed_ms)}</td><td><time dateTime={r.started_at} title={timestamp(r.started_at)}>{new Intl.DateTimeFormat(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(r.started_at))}</time></td></tr>)}</tbody></table></div><p className="obs-footnote">Latest reported agent · {Math.min(5, page.items.length)} runs shown{page.next_cursor || page.items.length > 5 ? ' · More in Traces' : ''}.</p></>;
}
export function SloSummary({ page }: { page: SLOPage }) {
    const wanted = ['availability', 'terminal_completion', 'model_reliability', 'dependency_reliability', 'cost_ledger_completeness'];
    const dependencies = page.items.filter(i => i.slo_id.startsWith('dependency_'));
    const dependency = dependencies.find(i => i.state !== 'HEALTHY') ?? dependencies[0];
    const items = page.items.filter(i => wanted.includes(i.slo_id) || i === dependency);
    const renderRow = (i: typeof items[number]) => <div className="obs-slo-row" key={i.slo_id}><span>{i.name}</span><span className="obs-slo-values" title={`Actual ${formatPercent(i.current_value)}; target ${formatPercent(i.objective)}; source ${i.source_status}`}>{formatPercent(i.current_value)} <small>/ {formatPercent(i.objective)}</small></span><span className="obs-slo-state" data-tone={i.state === 'HEALTHY' ? 'success' : ['BREACHED', 'BURNING'].includes(i.state) ? 'danger' : 'warning'}>{i.state === 'HEALTHY' ? 'PASS' : i.state === 'BREACHED' ? 'BREACH' : i.state === 'BURNING' ? 'BURNING' : i.state === 'STALE_DATA' ? 'STALE' : 'UNKNOWN'}</span></div>;
    return <div className="obs-slo-summary"><div className="obs-section-caption">SLOs <span>Rolling 28 days</span></div>{items.slice(0, 2).map(renderRow)}{items.length > 2 && <details className="obs-slo-more"><summary>{items.length - 2} more SLOs</summary>{items.slice(2).map(renderRow)}</details>}</div>;

}
export interface FinancialSnapshot { budgets: ControlPage; forecasts: ControlPage; anomalies: ControlPage; errors: (string | undefined)[] }
export function AttentionList({ slo, financial, health, ledgerState }: { slo: QueryState<SLOPage>; financial: QueryState<FinancialSnapshot>; health: QueryState<HealthView>; ledgerState?: string }) {
    const rows: { key: string; label: string; state: string; severity: number }[] = [];
    for (const i of slo.data?.items ?? []) if (i.state !== 'HEALTHY') rows.push({ key: i.slo_id, label: i.name, state: i.state, severity: ['BREACHED', 'BURNING'].includes(i.state) ? 3 : 1 });
    for (const i of [...financial.data?.anomalies.items ?? [], ...financial.data?.budgets.items ?? [], ...financial.data?.forecasts.items ?? []]) if (['ACTIVE', 'OBSERVED', 'WATCH', 'AT_RISK', 'AT_LIMIT', 'EXCEEDED', 'SOURCE_STALE', 'SOURCE_QUALITY_DEGRADATION'].includes(i.state) || i.risk === 'PROJECTED_OVER') rows.push({ key: `${i.kind}:${i.generation}`, label: name(i.type ?? i.scope) + (i.kind === 'ANOMALY' ? ' anomaly' : ` ${i.kind.toLowerCase()}`), state: i.risk === 'PROJECTED_OVER' ? i.risk : i.state, severity: ['CRITICAL', 'HIGH'].includes(i.severity) || i.state === 'EXCEEDED' ? 3 : 2 });
    for (const source of financial.data?.errors ?? []) rows.push({ key: String(source), label: `${source} source failed. Other sources remain available.`, state: 'UNAVAILABLE', severity: 2 });
    if (health.data && ['degraded', 'unavailable'].includes(health.data.persistence)) rows.push({ key: 'db', label: 'Read-model connectivity', state: health.data.persistence, severity: 2 });
    if (health.data?.stale_records) rows.push({ key: 'records', label: `${health.data.stale_records} stale durable records`, state: 'STALE', severity: 2 });
    if (ledgerState && ledgerState !== 'HEALTHY') rows.push({ key: 'ledger', label: 'Accounting ledger', state: ledgerState, severity: 2 });
    for (const [label, q] of [['SLO', slo], ['Financial', financial], ['Diagnostics', health]] as const) if (q.error) rows.push({ key: label, label: `${label} source`, state: ['denied', 'unauthorized'].includes(q.error) ? 'ACCESS RESTRICTED' : 'UNAVAILABLE', severity: 1 });
    rows.sort((a, b) => b.severity - a.severity);
    return <><ul className="obs-attention-list" aria-label="Overview attention items">{rows.slice(0, 5).map(r => <li key={r.key} data-tone={r.severity === 3 ? 'danger' : 'warning'}><span className="obs-state-dot" aria-hidden="true" /><div><p>{r.label}</p><span>{name(r.state)}</span></div></li>)}</ul>{!rows.length && <p className="obs-footnote">{slo.loading || financial.loading || health.loading ? 'Checking available sources…' : 'No issues reported by available sources.'}</p>}{rows.length > 5 && <Link to={`${OBSERVABILITY_ROUTE}/alerts`} className="obs-text-link">{rows.length - 5} additional states · View Alerts <ArrowUpRight size={14} aria-hidden="true" /></Link>}</>;
}
