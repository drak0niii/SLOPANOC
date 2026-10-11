import { getObservabilityJson } from './observabilityClient';
import { STAGES, AGENTS, ContractError } from '../components/shell/settings/observability/contract';
export interface ArchiveSpan { span_id: string; parent_span_id: string | null; name: string; start_ns: number; end_ns: number; status: number; agent?: string }
export interface ArchivePage { spans: ArchiveSpan[]; next_offset: number | null }
const spanNames = ['http.client', 'db.client', 'db.connection', 'db.transaction', 'storage.client', 'secretmanager.client', 'knowledge.retrieval', 'slopanoc.agent', 'slopanoc.tool', 'slopanoc.model.operation', 'gen_ai.request', 'slopanoc.model.validation', 'slopanoc.turn', 'request', 'session.load', 'attachments', 'orchestration', 'planning', 'thread.resolve', 'pending_interaction.resolve', 'context.select', 'finalization', 'persistence.final_answer', 'sse.complete'];
export async function getArchive(trace: string, digest: string, offset: number, signal?: AbortSignal): Promise<ArchivePage> {
    if (!/^[0-9a-f]{32}$/.test(trace) || !/^[0-9a-f]{64}$/.test(digest) || !Number.isInteger(offset) || offset < 0 || offset > 10000) throw new ContractError();
    const raw = await getObservabilityJson<Record<string, unknown>>(`/api/observability/archive/${trace}?digest=${digest}&offset=${offset}&limit=50`, signal);
    if (raw.trace_id !== trace || raw.digest !== digest || !Array.isArray(raw.spans) || raw.spans.length > 100) throw new ContractError();
    const spans = raw.spans.map((v: unknown) => {
        if (!v || typeof v !== 'object') throw new ContractError();
        const s = v as Record<string, unknown>;
        if (s.trace_id !== trace || typeof s.span_id !== 'string' || !/^[0-9a-f]{16}$/.test(s.span_id) ||
            (s.parent_span_id != null && (typeof s.parent_span_id !== 'string' || !/^[0-9a-f]{16}$/.test(s.parent_span_id))) ||
            typeof s.name !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(s.name) ||
            typeof s.start_ns !== 'number' || typeof s.end_ns !== 'number' || !Number.isFinite(s.start_ns) || !Number.isFinite(s.end_ns) || s.start_ns < 0 || s.end_ns < s.start_ns || typeof s.status !== 'number' || ![0, 1, 2].includes(s.status)) throw new ContractError();
        // Raw attributes/events/content never enter React state through this view.
        const attributes = s.attributes && typeof s.attributes === 'object' ? s.attributes as Record<string, unknown> : {};
        const agent = typeof attributes['slopanoc.agent'] === 'string' && AGENTS.includes(attributes['slopanoc.agent'] as typeof AGENTS[number]) ? attributes['slopanoc.agent'] as string : undefined;
        return { span_id: s.span_id, parent_span_id: s.parent_span_id ?? null, name: spanNames.includes(s.name) || (STAGES as readonly string[]).includes(s.name) ? s.name : 'Unknown span', start_ns: s.start_ns, end_ns: s.end_ns, status: s.status, agent } as ArchiveSpan;
    });
    if (new Set(spans.map(s => s.span_id)).size !== spans.length || (raw.next_offset != null && (!Number.isInteger(raw.next_offset) || Number(raw.next_offset) <= offset || Number(raw.next_offset) > 10000))) throw new ContractError();
    return { spans, next_offset: (raw.next_offset ?? null) as number | null };
}
