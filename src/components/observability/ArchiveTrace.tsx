import { name } from '../shell/settings/observability/format';
import { useState } from 'react';
import { getArchive, type ArchiveSpan } from '../../api/observabilityArchive';
import { useObservabilityQuery } from '../shell/settings/observability/useObservabilityQuery';
import { ReadState, buttonClass } from '../shell/settings/observability/ReadState';
export function ArchiveTrace({ traceId }: { traceId: string }) {
    const [input, setInput] = useState('');
    const [digest, setDigest] = useState('');
    const [offset, setOffset] = useState(0);
    const q = useObservabilityQuery(`archive:${traceId}:${digest}:${offset}`, signal => getArchive(traceId, digest, offset, signal), undefined, !!digest);
    function depth(span: ArchiveSpan, spans: ArchiveSpan[]) {
        let parent = span.parent_span_id, level = 0;
        const visited = new Set([span.span_id]);
        while (parent && level < 20) { const next = spans.find(s => s.span_id === parent); if (!next || visited.has(parent)) break; visited.add(parent); level++; parent = next.parent_span_id; }
        return level;
    }
    return <section className="mt-4"><h4 className="mb-2 text-sm text-primary">Protected exceptional trace</h4><p className="mb-2 text-xs text-tertiary">Provide the retained manifest digest. Technical capability and environment are verified by the server. Parentage is shown only from retained spans; parents outside this page are marked.</p>
        <form className="mb-3 flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); if (/^[0-9a-f]{64}$/.test(input)) { setDigest(input); setOffset(0); } }}><label className="flex-1 text-xs">Manifest digest<input aria-label="Archive manifest digest" className="mt-1 w-full rounded border border-subtle bg-surface p-2" maxLength={64} pattern="[0-9a-f]{64}" required value={input} onChange={e => setInput(e.target.value)} /></label><button className={buttonClass}>Read retained trace</button></form>
        {digest && <ReadState query={q}>{p => {
            const first = Math.min(...p.spans.map(s => s.start_ns)), last = Math.max(...p.spans.map(s => s.end_ns));
            return <><ol className="space-y-2">{p.spans.map(s => <li key={s.span_id} className="border-l border-subtle px-2 text-xs" style={{ marginLeft: depth(s, p.spans) * 12 }}><p>{s.agent ? name(s.agent) : s.name} · {s.status === 2 ? 'Error' : s.status === 1 ? 'OK' : 'Unset'} · {((s.end_ns - s.start_ns) / 1e6).toFixed(2)} ms</p><div aria-label="Retained span timing" className="my-1 h-1 bg-surface-hover"><div className="h-1 bg-accent/50" style={{ marginLeft: `${last > first ? ((s.start_ns - first) / (last - first)) * 100 : 0}%`, width: `${last > first ? ((s.end_ns - s.start_ns) / (last - first)) * 100 : 0}%` }} /></div>{s.parent_span_id && !p.spans.some(v => v.span_id === s.parent_span_id) && <p className="text-tertiary">Parent outside this page.</p>}</li>)}</ol>{!p.spans.length && <p>No retained spans in this page.</p>}{p.next_offset !== null && <button className={buttonClass} onClick={() => setOffset(p.next_offset!)}>Next retained spans</button>}</>;
        }}</ReadState>}
    </section>;
}
