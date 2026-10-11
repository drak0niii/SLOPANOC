import type { BillingSummary, UsagePage, Quantity } from '../../api/finops';

export const agents = [
    ['team_manager', 'Team Manager'],
    ['technical_authority_engineer', 'Technical Authority Engineer'],
    ['incident_manager', 'Incident Manager'],
    ['problem_manager', 'Problem Manager'],
    ['automated_operations_engineer', 'Automated Operations Engineer'],
];
export const agentLabels: Record<string, string> = Object.fromEntries(agents);
/** Provider total is a separate component; cached/candidate/thought can overlap. */
export function observedQuantity(page: UsagePage, component: Quantity) {
    if (page.next_cursor || !page.items.length) return null;
    const values = page.items.map(i => i.quantities[component].observed);
    if (values.every(v => v === null)) return null;
    const sum = values.reduce<number>((n, v) => n + (v ?? 0), 0);
    return Number.isSafeInteger(sum) ? sum : null;
}
/** Exact display aggregation only, never currency conversion or financial estimation. */
export function sumDecimals(values: string[]): string {
    const scale = Math.max(...values.map(v => v.split('.')[1]?.length ?? 0), 0);
    const total = values.reduce((sum, value) => {
        const negative = value.startsWith('-');
        const [whole, fraction = ''] = value.replace(/^-/, '').split('.');
        const units = BigInt(whole + fraction.padEnd(scale, '0'));
        return sum + (negative ? -units : units);
    }, 0n);
    const digits = (total < 0n ? -total : total).toString().padStart(scale + 1, '0');
    return (total < 0n ? '-' : '') + (scale ? digits.slice(0, -scale) + '.' + digits.slice(-scale) : digits);
}
export function UsageSummary({ page }: { page: UsagePage }) {
    const total = observedQuantity(page, 'total_tokens');
    const unknown = page.items.reduce((n, i) => n + i.quantities.total_tokens.unknown + i.attempts - i.captured_attempts, 0);
    return <><p className="text-xl tabular-nums text-primary">{total?.toLocaleString() ?? '—'}</p>
        <p className="my-2 text-xs text-tertiary">{page.next_cursor ? 'Incomplete page · aggregate unavailable' : !page.items.length ? 'Empty usage query · quantity unavailable' : `${unknown} calls with unknown total quantity · observed total only`}</p>
        <dl className="grid grid-cols-2 gap-2 text-xs">{(['input_tokens', 'output_tokens', 'cached_tokens', 'thought_tokens', 'tool_input_tokens', 'billable_characters'] as const).map(component => <div key={component}><dt className="text-tertiary">{component.replaceAll('_', ' ')}</dt><dd>{observedQuantity(page, component)?.toLocaleString() ?? '—'} · unknown {page.items.reduce((n, i) => n + i.quantities[component].unknown, 0)}</dd></div>)}</dl>
        <p className="mt-2 text-xs text-tertiary">Components may overlap; characters are separate units. Source freshness unavailable in this usage DTO.</p></>;
}
export function AgentUsageTable({ page, compact = false }: { page: UsagePage; compact?: boolean }) {
    const extra = page.items.filter(i => !agents.some(([id]) => id === i.dimension)).map(i => [i.dimension, i.dimension]);
    return <><p className={compact ? 'sr-only' : 'mb-2 text-xs text-tertiary'}>{page.environment} · {page.start} — {page.end}{page.next_cursor ? ' · Partial page' : ''}. Model calls observed; execution, online state, failures, duration and tool calls unavailable.</p>
        <div className="max-w-full overflow-x-auto" tabIndex={0} aria-label="Agent activity table scroll area"><table className="obs-agent-table w-full text-left text-xs"><caption className="sr-only">Authoritative agent model usage</caption><thead><tr>{(compact ? ['Agent', 'Model calls', 'Coverage'] : ['Agent', 'Model calls', 'Input tokens', 'Output tokens', 'Coverage']).map(v => <th className="p-2 whitespace-nowrap" scope="col" key={v}>{v}</th>)}</tr></thead><tbody>{[...agents, ...extra].map(([id, label]) => {
            const i = page.items.find(v => v.dimension === id);
            return <tr key={id} className="border-t border-subtle"><th className="p-2" scope="row">{label}</th><td className="p-2">{i?.attempts ?? '—'}</td><>{!compact && <><td className="p-2">{i?.quantities.input_tokens.observed ?? '—'}</td><td className="p-2">{i?.quantities.output_tokens.observed ?? '—'}</td></>}</><td className="p-2">{i?.quantity_coverage == null ? '—' : `${(i.quantity_coverage * 100).toFixed(1)}%`}</td></tr>;
        })}</tbody></table></div></>;
}
export function BilledSummary({ page, breakdown = false }: { page: BillingSummary; breakdown?: boolean }) {
    const currencies = [...new Set(page.amounts?.map(a => a.currency))];
    return <><p className="mb-2 text-xs text-tertiary">{page.source.state === 'STALE' ? 'Stale' : page.source.state} · {page.start} — {page.end} · {page.coverage_complete ? 'Complete coverage' : 'Partial / unknown coverage'} · Source as of {page.source.as_of ?? 'Unavailable'}</p>
        {page.amounts === null || !page.amounts.length || page.next_cursor ? <p>Unavailable{page.next_cursor ? ' · Incomplete financial page' : ''}</p> : breakdown ? <div className="max-w-full overflow-x-auto"><table className="obs-agent-table w-full text-left text-xs"><caption className="sr-only">Actual billed cost by category and currency</caption><thead><tr><th scope="col">Category</th><th scope="col">Actual net cost</th></tr></thead><tbody>{page.amounts.map(a => <tr key={`${a.category}:${a.currency}`}><th className="py-2" scope="row">{a.category}</th><td>{a.net} {a.currency}</td></tr>)}</tbody></table></div> : currencies.map(currency => <p key={currency} className="text-xl tabular-nums text-primary">{sumDecimals(page.amounts!.filter(a => a.currency === currency).map(a => a.net))} {currency}</p>)}
        <p className="mt-2 text-xs text-tertiary">Actual billed net amounts · source tax separate · no estimated cost or currency conversion.</p></>;
}
