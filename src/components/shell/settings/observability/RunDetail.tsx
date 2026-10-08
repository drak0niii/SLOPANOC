import { useEffect, useRef } from 'react';
import { getRun } from '../../../../api/observability';
import type { RunView } from '../../../../api/observabilityTypes';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass } from './ReadState';
import { StatusBadge } from './StatusBadge';
import { Timeline } from './Timeline';
import { deadline, dependencyName, duration, errorExplanation, name, timestamp } from './format';
export const activeRun = (r: RunView) => ['RUNNING', 'STALLED', 'PENDING'].includes(r.status) && r.classification !== 'STALE' && r.classification !== 'Unknown';
export function RunDetail({ runId, poll = false }: {
    runId: string;
    poll?: boolean;
}) {
    const query = useObservabilityQuery(`run:${runId}`, s => getRun(runId, s), poll ? activeRun : undefined);
    const heading = useRef<HTMLHeadingElement>(null);
    useEffect(() => { heading.current?.focus(); }, [runId]);
    return <section><h3 ref={heading} tabIndex={-1} className="mb-3 break-all text-base font-medium text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50">Run {runId}</h3>
 <ReadState query={query}>{r => <>
 <StatusBadge status={r.status} classification={r.classification}/>
 {r.classification === 'STALE' && <p className="mt-2 text-sm text-warning">Stale diagnostic record. Canonical status remains {r.status}; the outcome is unknown.</p>}
 <p className="my-2 text-xs text-tertiary">Classification as of {timestamp(r.classification_as_of)} · Durable diagnostic projection</p>
 <div className="grid min-w-0 gap-3 sm:grid-cols-2">
 <Fields title="Current state" values={{ Stage: r.current_stage, Agent: name(r.current_agent), Tool: name(r.current_tool), Environment: r.environment }}/>
 <Fields title="Timing" values={{ Started: timestamp(r.started_at), 'Last progress': timestamp(r.last_progress_at), 'Progress age (backend)': duration(r.progress_age_ms), Heartbeat: timestamp(r.heartbeat_at), Elapsed: duration(r.elapsed_ms), 'Work deadline': timestamp(r.work_deadline_at), 'Total deadline': timestamp(r.total_deadline_at), 'Remaining deadline': deadline(r.total_deadline_at), Terminal: timestamp(r.terminal_at) }}/>
 <Fields title="Reliability" values={{ Cleanup: r.cleanup_status, Delivery: r.delivery_status, Error: r.error_code ?? 'None reported', Explanation: errorExplanation(r.error_code) }}/>
 <div className={cardClass}><h4 className="text-sm font-medium text-primary">Dependencies</h4>{r.dependencies.length ? r.dependencies.map((d, i) => <p key={i} className="mt-2 text-sm text-secondary">{dependencyName(d.dependency)} · {d.operation}<br />{name(d.agent)} · {name(d.tool)}<br />Started {timestamp(d.started_at)}</p>) : <p className="mt-2 text-sm text-tertiary">None reported.</p>}</div>
 </div>
 {r.technical ? <Fields title="Deployment & trace correlation" values={{ 'Trace ID': r.technical.trace_id ?? 'Not reported', 'Service version': r.technical.service_version ?? 'Not reported', Release: r.technical.release_id ?? 'Not reported', Revision: r.technical.revision ?? 'Not reported', 'Schema version': String(r.technical.telemetry_schema_version), 'Config version': r.technical.config_version ?? 'Not reported' }}/> : <p className="mt-3 text-sm text-tertiary">Technical metadata is not available with this response.</p>}
 <Timeline runId={r.run_id}/>
 </>}</ReadState></section>;
}
function Fields({ title, values }: {
    title: string;
    values: Record<string, string>;
}) { return <section className={`${cardClass} mt-2 min-w-0`}><h4 className="mb-2 text-sm font-medium text-primary">{title}</h4><dl className="space-y-2 text-sm">{Object.entries(values).map(([k, v]) => <div key={k}><dt className="text-tertiary">{k}</dt><dd className="break-words text-secondary [overflow-wrap:anywhere]">{v}</dd></div>)}</dl></section>; }
