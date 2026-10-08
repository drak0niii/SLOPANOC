import { useState } from 'react';
import { getRunTimeline } from '../../../../api/observability';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, buttonClass } from './ReadState';
import { StatusBadge } from './StatusBadge';
import { dependencyName, duration, errorExplanation, name, timestamp } from './format';
export function Timeline({ runId }: {
    runId: string;
}) {
    const [cursor, setCursor] = useState<string>();
    const query = useObservabilityQuery(`timeline:${runId}:${cursor ?? ''}`, s => getRunTimeline(runId, { limit: 25, cursor }, s));
    return <section className="mt-5"><h4 className="mb-2 text-sm font-medium text-primary">Operational timeline</h4>
 <p className="mb-3 text-sm text-tertiary">Significant events only; this is not a complete distributed trace. M7 retains at most 128 events.</p>
 <ReadState query={{ ...query, refresh: () => { if (cursor)
            setCursor(undefined);
        else
            query.refresh(); } }}>{page => <>
 {page.truncated_count > 0 && <p className="mb-2 text-sm text-warning">Earlier diagnostic events were truncated. ({page.truncated_count})</p>}
 {!page.items.length ? <p className="text-sm text-tertiary">No diagnostic events available.</p> : <ol className="space-y-2">{page.items.map(e => <li key={e.event_seq} className="rounded-lg border border-subtle/50 p-3 text-sm text-secondary">
 <div className="flex flex-wrap items-center gap-2"><StatusBadge status={e.status}/><span>{e.event_type} · {e.stage}</span><span className="text-tertiary">+{duration(e.elapsed_ms)}</span></div>
 <time dateTime={e.timestamp} className="text-xs text-tertiary">{timestamp(e.timestamp)}</time>
 <p>{name(e.agent)} · {name(e.tool)} · {dependencyName(e.dependency)}</p>
 {e.error_code && <p>{e.error_code} — {errorExplanation(e.error_code)}</p>}
 </li>)}</ol>}
 <div className="mt-3 flex gap-2">{cursor && <button className={buttonClass} onClick={() => setCursor(undefined)}>First events</button>}{page.next_cursor && <button className={buttonClass} onClick={() => setCursor(page.next_cursor)}>Next events</button>}</div>
 </>}</ReadState></section>;
}
