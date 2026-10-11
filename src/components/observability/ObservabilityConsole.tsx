import { RefreshCw, ChevronRight, LockKeyhole } from 'lucide-react';
import { Kpi, Panel, TokensByAgent, CostComposition, RecentRunTable, SloSummary, AttentionList, formatCount } from './ConsoleVisuals';
import { AgentUsageTable, sumDecimals, observedQuantity } from './DashboardData';
import ObservabilityConfiguration from './ObservabilityConfiguration';
import type { SLOPage } from '../../api/sloTypes';
import type { QueryState } from '../shell/settings/observability/useObservabilityQuery';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useRouter } from '../../lib/router';
import { OBSERVABILITY_ROUTE } from '../../lib/routes';
import { ObservabilityScopeContext } from '../../api/observabilityScope';
import { getAccess } from '../../api/observabilityPolicy';
import { getActiveRuns, getObservabilityHealth, getRecentRuns, getSLOs } from '../../api/observability';
import { ApiError } from '../../api/client';
import { getControls, type FinancialControl } from '../../api/financialControls';
import { getReconciliation } from '../../api/reconciliation';
import { getBillingSummary, getLedgerHealth, getRuntimeUsage } from '../../api/finops';
import { useObservabilityQuery } from '../shell/settings/observability/useObservabilityQuery';
import { ReadState, buttonClass, cardClass } from '../shell/settings/observability/ReadState';
import { DiagnosticsPanel } from '../shell/settings/observability/DiagnosticsPanel';
import { SlosPanel } from '../shell/settings/observability/SlosPanel';
import { RunDetail } from '../shell/settings/observability/RunDetail';
import { FinOpsPanel } from '../shell/settings/observability/FinOpsPanel';
import { FinancialSourcePanel } from '../shell/settings/observability/FinancialSourcePanel';
import { ReconciliationPanel } from '../shell/settings/observability/ReconciliationPanel';
import { UnitEconomicsPanel } from '../shell/settings/observability/UnitEconomicsPanel';
import { BudgetPanel } from '../shell/settings/observability/BudgetPanel';
import { ForecastPanel } from '../shell/settings/observability/ForecastPanel';
import { AnomaliesPanel } from '../shell/settings/observability/AnomaliesPanel';
import { name } from '../shell/settings/observability/format';

export const consoleSections = ['Overview', 'Traces', 'Agents', 'Reliability', 'SLOs', 'FinOps', 'Alerts', 'Diagnostics', 'Configuration'] as const;
const percent = (value: number | null | undefined) => value == null ? '—' : new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 2 }).format(value);
const total = (items: FinancialControl[]) => {
    const matches = items.filter(i => i.scope === 'TOTAL' && i.currency === 'EUR' && i.freshness === 'FRESH');
    return matches.length === 1 ? matches[0] : undefined;
};
function Metric({ label, value, detail = 'Authoritative source unavailable' }: { label: string; value?: string | null; detail?: string }) {
    return <article className={`${cardClass} min-w-0 bg-surface/30 !p-3`}><h3 className="text-xs text-tertiary">{label}</h3><p className="my-2 text-xl font-medium tabular-nums text-primary">{value ?? '—'}</p><p className="text-xs text-tertiary">{detail}</p></article>;
}
export default function ObservabilityConsole() {
    const { pathname } = useRouter();
    const part = pathname.slice(OBSERVABILITY_ROUTE.length).split('/')[1]?.toLowerCase();
    const section = consoleSections.find(s => s.toLowerCase() === part) ?? 'Overview';
    const tabNav = useRef<HTMLElement>(null);
    useEffect(() => {
        const nav = tabNav.current, active = nav?.querySelector<HTMLElement>('[aria-current=page]');
        if (nav && active) {
            const left = active.offsetLeft - nav.offsetLeft;
            if (left < nav.scrollLeft || left + active.offsetWidth > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = Math.max(0, left - 12);
        }
    }, [section]);
    const [environment, setEnvironment] = useState('');
    const [days, setDays] = useState(7);
    const [refresh, setRefresh] = useState(0);
    const [windowEnd, setWindowEnd] = useState(() => new Date());
    const [lastUpdated, setLastUpdated] = useState<number>();
    const onRetrieved = useCallback((at: number) => setLastUpdated(at), []);
    const access = useObservabilityQuery('console-access', getAccess, undefined, true, 'replace');
    const scope = useMemo(() => {
        const end = new Date(windowEnd); end.setUTCHours(0, 0, 0, 0);
        const start = new Date(end); start.setUTCDate(start.getUTCDate() - days);
        return { environment: environment || undefined, since: start.toISOString(), until: end.toISOString(), refresh, capabilities: access.data?.capabilities, onRetrieved };
    }, [environment, days, refresh, windowEnd, access.data, onRetrieved]);
    return <main className="obs-console min-w-0 w-full" aria-label="Observability Console">
        <header className="obs-page-header">
            <div className="obs-page-title"><p>Resources <ChevronRight size={12} aria-hidden="true" /> Observability</p><h1>Observability &amp; FinOps</h1><span>Operational traces, agent activity, reliability and financial visibility.</span></div>
            <div className="obs-controls">
                <label>Environment<select aria-label="Console environment" value={environment} onChange={e => { setLastUpdated(undefined); setEnvironment(e.target.value); }}><option value="">Authorized default</option>{access.data?.environments.map(e => <option key={e} value={e}>{e}</option>)}</select></label>
                <label>Time range<select aria-label="Console time range" value={days} onChange={e => { setLastUpdated(undefined); setDays(Number(e.target.value)); }}>{[1, 7, 30].map(d => <option key={d} value={d}>{d} completed UTC day{d === 1 ? '' : 's'}</option>)}</select></label>
                <button className="obs-refresh" aria-label="Refresh console" onClick={() => { setLastUpdated(undefined); setWindowEnd(new Date()); setRefresh(v => v + 1); access.refresh(); }}><RefreshCw size={15} aria-hidden="true" /><span>Refresh</span></button>
                <span role="status" className="obs-last-updated">Last updated {access.data && !access.error && lastUpdated ? new Date(lastUpdated).toLocaleTimeString() : '—'}</span>
            </div>
        </header>
        <nav ref={tabNav} aria-label="Observability Console sections" className="obs-tabs">{consoleSections.map(s => <Link key={s} to={`${OBSERVABILITY_ROUTE}/${s.toLowerCase()}`} aria-current={s === section ? 'page' : undefined}>{s}</Link>)}</nav>
        {access.loading && !access.data && <p role="status" className="mb-3 text-sm text-tertiary">Verifying protected access…</p>}
        {access.error && <div role="alert" className="mb-4 rounded-lg border border-subtle p-4 text-sm text-secondary">{access.error === 'unauthorized' ? 'Authentication required. Sign in through your configured identity provider to view protected observability data.' : access.error === 'denied' ? 'Authenticated identity is not permitted to access this view or environment.' : access.error === 'unavailable' ? 'Observability service unavailable. Retry after the read plane recovers.' : 'Observability access could not be verified. Check the read-plane configuration and retry.'}<button className={buttonClass} onClick={access.refresh}>Retry access</button></div>}
        {access.data && !access.error && <ObservabilityScopeContext.Provider value={scope}>
            <div key={section} className={`obs-section obs-section-${section.toLowerCase()} min-w-0 text-sm text-secondary`}>
                {section === 'Configuration' && <ObservabilityConfiguration />}
                {section === 'Overview' && <Overview />}
                {section === 'Traces' && <><h2 className="mb-3 text-base text-primary">Traces</h2><DiagnosticsPanel tracing /><p className="mt-3 text-xs text-tertiary">Request class, model/tool counts and run costs are unavailable in the M7 summary contract. Only reported metadata is shown.</p></>}
                {section === 'Agents' && <Agents />}
                {section === 'Reliability' && <Reliability />}
                {section === 'SLOs' && <SlosPanel />}
                {section === 'FinOps' && <Finance />}
                {section === 'Alerts' && <Alerts />}
                {section === 'Diagnostics' && <><Reliability diagnostics /><DiagnosticsPanel /><p className="mt-3 text-xs text-tertiary">Archive reconstruction requires technical diagnostics. Full retained spans use their original parent IDs; summary events do not invent parentage.</p></>}
            </div>
        </ObservabilityScopeContext.Provider>}
        <footer className="obs-page-footer"><span><LockKeyhole size={12} aria-hidden="true" /> Read-only observability</span><span>SLOs: rolling 28 days · Financial controls: published month</span></footer>
    </main>;
}
function FinancialKPIs() {
    const budgets = useObservabilityQuery('console-financial-budgets', signal => getControls('budgets', signal));
    const forecasts = useObservabilityQuery('console-financial-forecasts', signal => getControls('forecasts', signal));
    const allocation = useObservabilityQuery('console-financial-allocation', getReconciliation);
    const billed = useObservabilityQuery('console-financial-billed', getBillingSummary);
    const b = total(budgets.data?.items ?? []), f = total(forecasts.data?.items ?? []);
    const currencies = [...new Set(billed.data?.amounts?.map(a => a.currency))];
    const actual = billed.data?.amounts?.length && !billed.data.next_cursor ? currencies.map(c => <span key={c}>{sumDecimals(billed.data!.amounts!.filter(a => a.currency === c).map(a => a.net))} {c}</span>) : '—';
    // Daily published allocation is additive within a currency, unlike unit views.
    const days = allocation.data?.items;
    const unallocated = days?.length && !allocation.data?.next_cursor && days.every(d => ['Reconciled', 'Partial'].includes(d.display_state) && d.amounts.length) ? [...new Set(days.flatMap(d => d.amounts.map(a => a.currency)))].map(c => <span key={c}>{sumDecimals(days.flatMap(d => d.amounts.filter(a => a.currency === c).map(a => a.unallocated)))} {c}</span>) : '—';
    return <><div className="obs-finops-kpis">
        <Kpi label="Actual Cost" kind="cost" query={billed} value={actual} detail={billed.data?.source.state ?? 'Billed data unavailable'} />
        <Kpi label="Budget" kind="coverage" query={budgets} value={b?.amount == null ? '—' : `${b.amount} ${b.currency}`} detail={b ? `${b.month} · ${b.state}` : 'Unavailable for published scope'} />
        <Kpi label="Forecast" kind="success" query={forecasts} value={f?.forecast == null ? '—' : `${f.forecast} ${f.currency}`} detail={f ? `${f.confidence} confidence · ${f.risk}` : 'Unavailable for published scope'} />
        <Kpi label="Unallocated" kind="runs" query={allocation} value={unallocated} detail={days?.length ? 'Published days · source coverage applies' : 'Allocation data unavailable'} />
    </div><details className="obs-financial-outlook"><summary>Actual Cost Composition</summary><ReadState compact query={billed}>{p => <CostComposition page={p} />}</ReadState></details></>;
}
function Overview() {
    const slo = useObservabilityQuery('console-overview-slos', getSLOs);
    const runs = useObservabilityQuery('console-overview-runs', signal => getRecentRuns({ limit: 10 }, signal));
    const active = useObservabilityQuery('console-overview-active', signal => getActiveRuns({ limit: 100 }, signal));
    const usage = useObservabilityQuery('console-overview-usage', signal => getRuntimeUsage('agent', signal));
    const billed = useObservabilityQuery('console-overview-billed', getBillingSummary);
    const ledger = useObservabilityQuery('console-overview-ledger', getLedgerHealth);
    const financial = useObservabilityQuery('console-overview-controls', loadAlertControls);
    const health = useObservabilityQuery('console-alert-health', getObservabilityHealth);
    const [selected, setSelected] = useState<string>();
    const availability = slo.data?.items.find(i => i.slo_id === 'availability');
    const tokens = usage.data ? observedQuantity(usage.data, 'total_tokens') : null;
    const unknown = usage.data?.items.reduce((n, i) => n + i.quantities.total_tokens.unknown + i.attempts - i.captured_attempts, 0);
    const currencies = [...new Set(billed.data?.amounts?.map(a => a.currency))];
    const cost = billed.data?.amounts?.length && !billed.data.next_cursor ? currencies.map(c => <span key={c}>{sumDecimals(billed.data!.amounts!.filter(a => a.currency === c).map(a => a.net))} {c}</span>) : '—';
    const actual = total(financial.data?.budgets.items ?? []), forecast = total(financial.data?.forecasts.items ?? []);
    if (selected) return <><button className={`${buttonClass} mb-3`} onClick={() => setSelected(undefined)}>Back to overview</button><RunDetail runId={selected} /></>;
    return <><h2 className="sr-only">Overview</h2>
        <div className="obs-kpi-grid">
            <Kpi label="Total Tokens" kind="tokens" query={usage} value={<span title={tokens?.toLocaleString()}>{formatCount(tokens)}</span>} detail={usage.data?.next_cursor ? 'Partial page · total unavailable' : tokens == null ? 'Quantity unavailable' : `${unknown ?? 0} unknown calls · observed total`} />
            <Kpi label="Actual Cost" kind="cost" query={billed} value={cost} detail={billed.data ? `${billed.data.source.state} · billed net` : 'Billed data unavailable'} />
            <Kpi label="Active Runs" kind="runs" query={active} value={active.data && !active.data.next_cursor ? String(active.data.items.length) : '—'} detail={active.data?.next_cursor ? 'Partial page · total unavailable' : 'Current durable snapshot'} />
            <Kpi label="Agent Activity" kind="agents" query={usage} value={usage.data && !usage.data.next_cursor ? String(usage.data.items.filter(i => i.attempts > 0).length) : '—'} detail="Agents with observed model usage" />
            <Kpi label="Success Rate" kind="success" query={slo} value={percent(availability?.current_value)} detail={availability ? `${availability.state} · rolling 28 days` : 'SLO source unavailable'} tone={availability?.state === 'HEALTHY' ? 'success' : availability?.state === 'BREACHED' ? 'danger' : undefined} />
            <Kpi label="Financial Coverage" kind="coverage" query={billed} value={billed.data ? billed.data.amounts == null ? 'Unknown' : billed.data.coverage_complete ? 'Complete' : 'Partial' : '—'} detail={billed.data ? billed.data.source.state : 'Coverage unavailable'} tone={billed.data?.coverage_complete === false ? 'warning' : undefined} />
        </div>
        <div className="obs-overview-grid">
            <Panel title="Token Usage by Agent" subtitle="Observed provider totals · ranked by usage" className="obs-usage-panel" link={{ label: 'Explore', path: 'agents' }}><ReadState compact query={usage}>{p => <TokensByAgent page={p} />}</ReadState></Panel>
            <Panel title="Agent Activity" subtitle="Durable model usage" className="obs-agents-panel"><ReadState compact query={usage}>{p => <AgentUsageTable page={p} compact />}</ReadState></Panel>
            <Panel title="Recent Runs" subtitle="Latest executions in the selected UTC period" className="obs-runs-panel" link={{ label: 'All traces', path: 'traces' }}><ReadState compact query={runs}>{p => <RecentRunTable page={p} onSelect={setSelected} />}</ReadState></Panel>
            <Panel title="Actual Cost Composition" subtitle="Published billing categories" className="obs-finance-panel" link={{ label: 'FinOps', path: 'finops' }}><ReadState compact query={billed}>{p => <CostComposition page={p} />}</ReadState>
                <details className="obs-financial-outlook"><summary>Budget &amp; forecast <span>Published month</span></summary><ReadState compact query={financial}>{() => <dl><div><dt>Budget</dt><dd>{actual?.amount == null ? 'Unavailable' : `${actual.amount} ${actual.currency}`}</dd></div><div><dt>Forecast</dt><dd>{forecast?.forecast == null ? 'Unavailable' : `${forecast.forecast} ${forecast.currency}`}</dd></div><div><dt>Confidence</dt><dd>{forecast?.confidence ?? 'Unavailable'}</dd></div><div><dt>Risk</dt><dd>{forecast?.risk ?? 'Unavailable'}</dd></div></dl>}</ReadState></details>
            </Panel>
            <Panel title="Reliability & Alerts" subtitle="States requiring attention" className="obs-attention-panel" link={{ label: 'All states', path: 'alerts' }}><AttentionList slo={slo} financial={financial} health={health} ledgerState={ledger.data?.state} /><ReadState compact query={slo}>{p => <SloSummary page={p} />}</ReadState></Panel>
        </div>
    </>;
}
function Agents() {
    const q = useObservabilityQuery('console-agents-usage', signal => getRuntimeUsage('agent', signal));
    const runs = useObservabilityQuery('console-agents-runs', signal => getRecentRuns({ limit: 10 }, signal));
    const [selected, setSelected] = useState<string>();
    if (selected) return <><button className={`${buttonClass} mb-3`} onClick={() => setSelected(undefined)}>Back to agents</button><RunDetail runId={selected} /></>;
    return <section><h2 className="mb-3 text-base text-primary">Agents</h2><ReadState compact query={q}>{p => <div className="obs-agent-page-grid"><Panel title="Token Usage by Agent" subtitle="Observed provider totals"><TokensByAgent page={p} /></Panel><Panel title="Agent Activity" subtitle="Model usage and quantity coverage"><AgentUsageTable page={p} /></Panel></div>}</ReadState><Panel className="obs-agent-runs" title="Recently Observed Runs" subtitle="Latest reported agent · complete participant history unavailable" link={{ label: 'All traces', path: 'traces' }}><ReadState compact query={runs}>{p => <RecentRunTable page={p} onSelect={setSelected} />}</ReadState></Panel></section>;
}
function Reliability({ diagnostics = false }: { diagnostics?: boolean }) {
    const health = useObservabilityQuery('console-health', getObservabilityHealth);
    const active = useObservabilityQuery('console-active', signal => getActiveRuns({ limit: 10 }, signal));
    return <section className="mb-4"><h2 className="mb-3 text-base text-primary">{diagnostics ? 'Diagnostic Sources' : 'Reliability'}</h2><ReadState query={health}>{h => <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4"><Metric label="Read-model connectivity" value={h.persistence} detail={`Durable scope · stale records ${h.stale_records ?? 'unavailable'}`} /><Metric label="Read-plane process" value={h.read_plane_state ?? '—'} detail={h.read_plane_state ? 'Reader-local health only' : undefined} /><Metric label="ANOC service health" value={h.anoc_state ?? '—'} /><Metric label="Collector health" value={h.collector_state ?? '—'} /><Metric label="Archive health" value={h.archive_state ?? 'unknown'} /><Metric label="Source freshness" value={h.source_freshness ?? 'unknown'} /></div>}</ReadState><p className="my-3 text-xs text-tertiary">ANOC-local queues, exporter failures and admission counters are unavailable in the separate reader. Historical failures, timeouts and stalled states appear in run/SLO records; gateway and dependency infrastructure health require published sources.</p><ReadState query={active}>{p => <p>Active runs on this page: {p.items.length}{p.next_cursor ? ' · more available' : ''}. An empty successful query is not evidence of service health.</p>}</ReadState></section>;
}
async function loadAlertControls(signal: AbortSignal) {
    const results = await Promise.allSettled(['budgets', 'forecasts', 'anomalies'].map(k => getControls(k as 'budgets' | 'forecasts' | 'anomalies', signal)));
    for (const result of results) if (result.status === 'rejected' && result.reason instanceof ApiError && [401, 403].includes(result.reason.status)) throw result.reason;
    if (results.every(r => r.status === 'rejected')) throw new Error('Financial sources unavailable.');
    const errors = results.map((r, i) => r.status === 'rejected' ? ['Budgets', 'Forecasts', 'Anomalies'][i] : undefined).filter(Boolean);
    // Failed sources have explicit UI state; successful sources still render.
    const [budgets, forecasts, anomalies] = results.map(r => r.status === 'fulfilled' ? r.value : { environment: '', state: 'FAILED' as const, items: [], next_cursor: null, evaluated: 0 });
    return { budgets, forecasts, anomalies, errors };
}
function Alerts({ compact = false, sloQuery, financialQuery }: { compact?: boolean; sloQuery?: QueryState<SLOPage> & { refresh: () => void }; financialQuery?: QueryState<Awaited<ReturnType<typeof loadAlertControls>>> & { refresh: () => void } }) {
    const ownSlo = useObservabilityQuery('console-alert-slos', getSLOs, undefined, !sloQuery);
    const slo = sloQuery ?? ownSlo;
    const ownControl = useObservabilityQuery('console-alert-financial', loadAlertControls, undefined, !financialQuery);
    const control = financialQuery ?? ownControl;
    const health = useObservabilityQuery('console-alert-health', getObservabilityHealth);
    return <section>{!compact && <h2 className="mb-3 text-base text-primary">Alerts</h2>}<ReadState query={slo}>{p => <>{p.items.filter(i => ['BREACHED', 'BURNING', 'STALE_DATA', 'DATA_SOURCE_UNAVAILABLE'].includes(i.state)).map(i => <p className="mb-2 text-xs text-warning" key={i.slo_id}>{i.name} · {i.state} · {i.source_status}</p>)}{!p.items.some(i => ['BREACHED', 'BURNING'].includes(i.state)) && <p className="text-xs text-tertiary">No SLO breach reported in this snapshot; source coverage still applies.</p>}</>}</ReadState><ReadState query={control}>{p => <>{p.errors.map(e => <p role="alert" key={e}>{e} source failed. Other sources remain available.</p>)}{[...p.budgets.items, ...p.forecasts.items, ...p.anomalies.items].filter(i => ['WATCH', 'AT_RISK', 'AT_LIMIT', 'EXCEEDED', 'SOURCE_STALE', 'ACTIVE', 'OBSERVED'].includes(i.state) || i.risk === 'PROJECTED_OVER').map((i, n) => <p className="mb-2 text-xs text-warning" key={`${i.generation}:${n}`}>{i.kind} · {i.state} · {i.risk} · {i.type ?? i.scope} · {i.freshness}</p>)}<p className="text-xs text-tertiary">Budget: {name(p.budgets.state)} · Forecast: {name(p.forecasts.state)} · Anomalies: {name(p.anomalies.state)}. Delivery/history unavailable.</p></>}</ReadState><ReadState query={health}>{h => <><p className="text-xs text-tertiary">Read plane: {h.read_plane_state ?? 'Unavailable'} · Database: {h.persistence} · Collector: {h.collector_state ?? 'unknown'} · As of {h.as_of}</p>{['degraded', 'unavailable'].includes(h.persistence) && <p role="alert" className="text-xs text-warning">Read-model connectivity {h.persistence}</p>}{h.stale_records != null && h.stale_records > 0 && <p className="text-xs text-warning">Stale durable records: {h.stale_records}</p>}</>}</ReadState><p className="mt-2 text-xs text-tertiary">Derived source states only. No autonomous remediation.</p></section>;
}
function Finance() {
    const sections = ['Runtime usage', 'Billing / Pricing', 'Reconciliation / Allocation', 'Unit Economics', 'Budgets', 'Forecasts', 'Anomalies'];
    const [view, setView] = useState(sections[0]);
    return <section><h2 className="mb-3 text-base text-primary">FinOps</h2><FinancialKPIs /><label className="obs-financial-view">Financial view <select aria-label="Financial view" className={buttonClass} value={view} onChange={e => setView(e.target.value)}>{sections.map(s => <option key={s}>{s}</option>)}</select></label>
        {view === sections[0] && <FinOpsPanel runtimeOnly />}{view === sections[1] && <FinancialSourcePanel />}{view === sections[2] && <ReconciliationPanel />}{view === sections[3] && <UnitEconomicsPanel />}{view === sections[4] && <BudgetPanel />}{view === sections[5] && <ForecastPanel />}{view === sections[6] && <AnomaliesPanel />}
        <p className="mt-3 text-xs text-tertiary">Model/provider/workload usage dimensions are supported. Request-class financial attribution is unavailable. Direct/shared/unallocated values come from published allocation results.</p></section>;
}
