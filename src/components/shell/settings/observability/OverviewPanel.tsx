import { useState } from 'react';
import { getActiveRuns, getObservabilityHealth, getRecentRuns } from '../../../../api/observability';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass, buttonClass } from './ReadState';
import { StatusBadge } from './StatusBadge';
import { RunTable } from './RunTable';
import { RunDetail } from './RunDetail';
export function OverviewPanel() {
    const health = useObservabilityQuery('overview-health', getObservabilityHealth);
    const active = useObservabilityQuery('overview-active', s => getActiveRuns({ limit: 5 }, s));
    const recent = useObservabilityQuery('overview-recent', s => getRecentRuns({ limit: 5 }, s));
    const [selected, setSelected] = useState<string>();
    if (selected)
        return <><button className={buttonClass} onClick={() => setSelected(undefined)}>Back to overview</button><RunDetail key={selected} runId={selected}/></>;
    return <section><h3 className="mb-3 text-base font-medium text-primary">Overview</h3><ReadState query={health}>{h => <div className="mb-4 grid gap-3 sm:grid-cols-2"><div className={cardClass}><h4 className="mb-2 text-sm text-secondary">Persistence projection</h4><StatusBadge status={h.persistence}/><p className="mt-2 text-xs text-tertiary">Writer counters are process-local; durable reads cover authorized environments.</p><p className="mt-2 text-sm text-secondary">Pending writes: {h.pending} · Terminal pending: {h.terminal_pending}</p></div><div className={cardClass}><h4 className="mb-2 text-sm text-secondary">Exporter</h4><StatusBadge status={h.exporter_state}/><p className="mt-2 text-sm text-tertiary">Collector endpoint: {h.collector_configured ? 'configured' : 'not configured'}. Configuration does not prove delivery.</p></div></div>}</ReadState>
 <h4 className="my-3 text-sm font-medium text-primary">Active runs preview</h4><ReadState query={active}>{p => <RunTable page={p} onSelect={setSelected}/>}</ReadState>
 <h4 className="my-3 text-sm font-medium text-primary">Recent runs</h4><ReadState query={recent}>{p => <RunTable page={p} onSelect={setSelected} empty="No recent runs were returned for this filter."/>}</ReadState>
 <p className="mt-4 text-sm text-tertiary">SLO evaluation becomes available in M9. Accounting starts in M10. Model and agent aggregates are not reported.</p></section>;
}
