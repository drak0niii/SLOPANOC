import type { RunPage } from '../../../../api/observabilityTypes';
import { buttonClass } from './ReadState';
import { StatusBadge } from './StatusBadge';
import { dependencyName, duration, name, timestamp } from './format';
export function RunTable({ page, onSelect, empty = 'No active runs.' }: {
    page: RunPage;
    onSelect: (id: string) => void;
    empty?: string;
}) {
    if (!page.items.length)
        return <p className="text-sm text-tertiary">{empty}</p>;
    return <><p className="mb-2 text-sm text-tertiary">Showing {page.items.length} matching runs on this page{page.next_cursor ? ' · More results available.' : '.'} Snapshot: {timestamp(page.as_of)}</p>
 <div className="max-w-full overflow-x-auto rounded-lg border border-subtle/50" tabIndex={0} aria-label="Run table scroll area">
 <table className="w-full text-left text-sm"><caption className="sr-only">Operational diagnostic runs</caption>
 <thead className="bg-surface-hover text-tertiary"><tr>{['Run ID', 'Status', 'Stage', 'Agent', 'Tool', 'Dependency', 'Started', 'Last progress', 'Elapsed', 'Environment'].map(h => <th key={h} scope="col" className="whitespace-nowrap px-3 py-2 font-medium">{h}</th>)}</tr></thead>
 <tbody>{page.items.map(r => <tr key={r.run_id} className="border-t border-subtle/50 text-secondary">
 <th scope="row" className="px-2 py-2"><button type="button" className={buttonClass} onClick={() => onSelect(r.run_id)} aria-label={`Open run ${r.run_id}`}>{r.run_id.slice(0, 8)}…</button></th>
 <td className="px-3 py-2"><StatusBadge status={r.status} classification={r.classification}/></td>
 <td className="px-3 py-2">{r.current_stage}</td><td className="px-3 py-2">{name(r.current_agent)}</td><td className="px-3 py-2">{name(r.current_tool)}</td>
 <td className="px-3 py-2">{r.dependencies.length ? r.dependencies.map(d => dependencyName(d.dependency)).join(', ') : 'None reported'}</td>
 <td className="whitespace-nowrap px-3 py-2"><time dateTime={r.started_at}>{timestamp(r.started_at)}</time></td>
 <td className="px-3 py-2">{duration(r.progress_age_ms)} ago (backend)</td><td className="px-3 py-2">{duration(r.elapsed_ms)}</td><td className="px-3 py-2">{r.environment}</td>
 </tr>)}</tbody></table></div></>;
}
