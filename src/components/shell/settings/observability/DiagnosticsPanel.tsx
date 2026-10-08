import { useEffect, useRef, useState } from 'react';
import { getActiveRuns, getRecentRuns } from '../../../../api/observability';
import type { RunQuery } from '../../../../api/observabilityTypes';
import { isRunId, STAGES, STATUSES } from './contract';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, buttonClass } from './ReadState';
import { RunTable } from './RunTable';
import { RunDetail } from './RunDetail';
const field = 'min-w-0 rounded-lg border border-subtle bg-surface px-3 py-2 text-sm text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50';
export function DiagnosticsPanel({ tracing = false }: {
    tracing?: boolean;
}) {
    const [selected, setSelected] = useState<string>();
    const [returnFocus, setReturnFocus] = useState<string>();
    const [lookup, setLookup] = useState('');
    const [invalid, setInvalid] = useState(false);
    return <div><h3 className="mb-2 text-base font-medium text-primary">{tracing ? 'Tracing' : 'Diagnostics'}</h3>
 {tracing && <p className="mb-3 text-sm text-tertiary">Select a run to inspect its operational timeline and available trace correlation.</p>}
 <form onSubmit={e => { e.preventDefault(); if (isRunId(lookup.trim())) {
        setSelected(lookup.trim());
        setInvalid(false);
    }
    else
        setInvalid(true); }} className="mb-4 flex flex-wrap items-end gap-2">
 <label className="min-w-0 flex-1 text-sm text-tertiary">Exact run ID<input aria-label="Exact run ID" className={`${field} mt-1 w-full`} value={lookup} maxLength={36} onChange={e => setLookup(e.target.value)}/></label>
 <button className={buttonClass} type="submit">Find run</button></form>
 {invalid && <p role="alert" className="mb-3 text-sm text-danger">Enter a canonical run UUID.</p>}
 {selected && <><button className={`${buttonClass} mb-3`} onClick={() => { setReturnFocus(selected); setSelected(undefined); }}>Back to runs</button><RunDetail key={selected} runId={selected} poll={!tracing}/></>}
 <div hidden={!!selected}><RunListing key={tracing ? 'tracing' : 'diagnostics'} onSelect={setSelected} poll={!tracing} active={!selected} focusId={returnFocus}/></div>
 </div>;
}
function RunListing({ onSelect, poll, active, focusId }: {
    onSelect: (id: string) => void;
    poll: boolean;
    active: boolean;
    focusId?: string;
}) {
    const [mode, setMode] = useState<'active' | 'recent'>('active');
    const [filters, setFilters] = useState<RunQuery>({});
    const [cursor, setCursor] = useState<string>();
    const [previous, setPrevious] = useState<(string | undefined)[]>([]);
    const [environment, setEnvironment] = useState('');
    const [since, setSince] = useState('');
    const [until, setUntil] = useState('');
    const [filterError, setFilterError] = useState(false);
    const query = useObservabilityQuery(JSON.stringify([mode, filters, cursor]), s => (mode === 'active' ? getActiveRuns : getRecentRuns)({ ...filters, limit: 25, cursor }, s), poll && mode === 'active' && !cursor ? () => true : undefined, active);
    const listRef = useRef<HTMLDivElement>(null);
    const focusRestored = useRef(false);
    useEffect(() => {
        if (!active) focusRestored.current = false;
        else if (query.data && focusId && !focusRestored.current) {
            listRef.current?.querySelector<HTMLButtonElement>(`button[aria-label="Open run ${focusId}"]`)?.focus();
            focusRestored.current = true;
        }
    }, [active, query.data, focusId]);
    function change(next: RunQuery) { setFilters(next); setCursor(undefined); setPrevious([]); }
    return <>
 <nav aria-label="Run list" className="mb-3 flex gap-2">{(['active', 'recent'] as const).map(m => <button key={m} className={buttonClass} aria-current={mode === m ? 'page' : undefined} onClick={() => { setMode(m); setCursor(undefined); setPrevious([]); }}>{m === 'active' ? 'Active runs' : 'Recent runs'}</button>)}</nav>
 <div className="mb-4 flex flex-wrap gap-2">
 <label className="text-sm text-tertiary">Status<select aria-label="Status filter" className={`${field} ml-2`} value={filters.status ?? ''} onChange={e => change({ ...filters, status: e.target.value as RunQuery['status'] || undefined })}><option value="">All statuses</option>{STATUSES.map(s => <option key={s}>{s}</option>)}</select></label>
 <label className="min-w-0 text-sm text-tertiary">Stage<select aria-label="Stage filter" className={`${field} ml-2 max-w-[210px]`} value={filters.stage ?? ''} onChange={e => change({ ...filters, stage: e.target.value as RunQuery['stage'] || undefined })}><option value="">All stages</option>{STAGES.map(s => <option key={s}>{s}</option>)}</select></label>
 </div>
 <details className="mb-3 text-sm text-tertiary"><summary className="cursor-pointer rounded-md focus-visible:ring-2 focus-visible:ring-accent/50">Environment & time filters</summary>
 <form className="mt-3 grid gap-2 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); const start = since ? Date.parse(since) : undefined, end = until ? Date.parse(until) : undefined; if ((environment && !/^[A-Za-z0-9_.-]{1,64}$/.test(environment)) || (start !== undefined && !Number.isFinite(start)) || (end !== undefined && !Number.isFinite(end)) || (start !== undefined && end !== undefined && (end < start || end - start > 90 * 86400000))) {
        setFilterError(true);
        return;
    } setFilterError(false); change({ ...filters, environment: environment || undefined, since: start === undefined ? undefined : new Date(start).toISOString(), until: end === undefined ? undefined : new Date(end).toISOString() }); }}>
 <label>Environment<input className={`${field} mt-1 w-full`} aria-label="Environment filter" maxLength={64} value={environment} onChange={e => setEnvironment(e.target.value)}/></label>
 <label>From (local time)<input type="datetime-local" className={`${field} mt-1 w-full`} aria-label="From time filter" value={since} onChange={e => setSince(e.target.value)}/></label>
 <label>Until (local time)<input type="datetime-local" className={`${field} mt-1 w-full`} aria-label="Until time filter" value={until} onChange={e => setUntil(e.target.value)}/></label>
 <button type="submit" className={buttonClass}>Apply filters</button>
 </form>{filterError && <p role="alert">Use a valid environment and a time range of at most 90 days.</p>}</details>
 <ReadState query={{ ...query, refresh: () => { if (cursor) {
            setCursor(undefined);
            setPrevious([]);
        }
        else
            query.refresh(); } }}>{page => <>
 <div ref={listRef}><RunTable page={page} onSelect={onSelect} empty={mode === 'active' ? 'No active runs.' : 'No recent runs were returned for this filter.'}/></div>
 <div className="mt-3 flex gap-2"><button className={buttonClass} disabled={!previous.length} onClick={() => { setCursor(previous.at(-1)); setPrevious(p => p.slice(0, -1)); }}>Previous page</button><button className={buttonClass} disabled={!page.next_cursor || previous.length >= 20} onClick={() => { setPrevious(p => [...p, cursor]); setCursor(page.next_cursor); }}>Next page</button></div>
 {previous.length >= 20 && <p className="text-sm text-tertiary">Page navigation limit reached. Refresh or narrow the filters.</p>}
 </>}</ReadState>
 </>;
}
