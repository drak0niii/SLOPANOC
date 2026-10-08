import { useState } from 'react';
import { FinancialSourcePanel } from './FinancialSourcePanel';
import { getRuntimeUsage, getLedgerHealth, getLedgerCompleteness } from '../../../../api/finops';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass, buttonClass } from './ReadState';
import { name, timestamp } from './format';
const percent = (v: number|null) => v===null?'Unavailable':new Intl.NumberFormat(undefined,{style:'percent',maximumFractionDigits:4}).format(v);
export function FinOpsPanel() {
    const [group,setGroup]=useState('model');
    const query=useObservabilityQuery('finops:'+group,async signal=>{
        const [usage,health,completeness]=await Promise.all([getRuntimeUsage(group,signal),getLedgerHealth(signal),getLedgerCompleteness(signal)]);
        return {usage,health,completeness};
    },()=>true);
    const [page,setPage]=useState<{cursor:string;start:string;end:string}|undefined>();
    const paged=useObservabilityQuery('finops-page:'+group+':'+(page?.cursor??''),signal=>getRuntimeUsage(group,signal,page?.start,page?.end,page?.cursor),undefined,!!page && !!query.data);
    return <section><h3 className="text-base font-medium text-primary">FinOps</h3>
      <p className="my-3 text-sm text-secondary">Observed provider usage and durable ledger capture. Quantity coverage is separate from ledger completeness.</p>
      <label className="mb-3 block text-sm text-secondary">Group usage by <select aria-label="Group usage by" className={buttonClass} value={group} onChange={e=>{setGroup(e.target.value);setPage(undefined);}}>{['model','provider','agent','workload','operation','operation_type'].map(g=><option key={g} value={g}>{name(g)}</option>)}</select></label>
      <ReadState query={query}>{({usage,health,completeness})=><>
        <div className="mb-3 grid gap-2 sm:grid-cols-2">
          <div className={cardClass}><h4 className="text-sm text-secondary">Ledger health</h4><p>{name(health.state)}</p><p className="text-xs text-tertiary">Pending inbox: {health.pending} · Unresolved attempts: {health.debt} · Conflicts: {health.conflicts}</p><p className="text-xs text-tertiary">Last durable persistence: {timestamp(health.last_persistence??undefined)}</p></div>
          <div className={cardClass}><h4 className="text-sm text-secondary">Ledger completeness</h4><p>{percent(completeness.current_value)} · {name(completeness.state)}</p><p className="text-xs text-tertiary">Eligible: {completeness.eligible} · Captured: {completeness.good} · Uncaptured: {completeness.bad}</p><p className="text-xs text-tertiary">Freshness: {completeness.source_status}</p></div>
        </div>
        <p className="mb-2 text-xs text-tertiary">{usage.environment} · {timestamp(usage.start)} – {timestamp(usage.end)}. Totals contain observed quantities; missing metadata remains unknown.</p>
        {!usage.items.length && <p className="text-sm text-tertiary">No runtime usage in this range.</p>}
        <div className="space-y-2">{(page && paged.data ? paged.data.items : usage.items).map(item=><div key={item.dimension} className={cardClass}><h4 className="text-sm text-secondary">{name(item.dimension)}</h4><p className="text-xs text-tertiary">Provider attempts: {item.attempts} · Captured: {item.captured_attempts} · Quantity coverage: {percent(item.quantity_coverage)} · Quantity metadata known: {item.quantity_known} · Partial: {item.quantity_partial}</p><dl className="mt-2 grid grid-cols-2 gap-1 text-xs">{(['input_tokens','output_tokens','candidate_tokens','total_tokens','cached_tokens','thought_tokens','billable_characters'] as const).map(q=><div key={q}><dt className="text-tertiary">{name(q)}</dt><dd className="text-secondary">{item.quantities[q].observed??'Unknown'} · {item.quantities[q].unknown} unknown attempts</dd></div>)}</dl></div>)}</div>
        {page && paged.error && <p role="alert">Usage page unavailable.</p>}
        {(page ? paged.data?.next_cursor : usage.next_cursor) && <button className={buttonClass} disabled={paged.loading} onClick={()=>{const cursor=page?paged.data?.next_cursor:usage.next_cursor;if(cursor)setPage({cursor,start:page?.start??usage.start,end:page?.end??usage.end});}}>Next usage page</button>}
        <p className="mt-3 text-xs text-tertiary">Ledger retention policy: {health.retention_months} months. Retention execution is not activated. Configuration is read-only.</p>
      </>}</ReadState>
      <FinancialSourcePanel/>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">{[['Reconciliation','M12'],['Allocation','M12'],['Unit economics','M13'],['Budgets','M14'],['Forecasts','M14'],['Anomalies','M14']].map(([label,m])=><div key={label} className={cardClass}><h4 className="text-sm text-secondary">{label}</h4><p className="mt-1 text-xs text-tertiary">Available after {m}</p></div>)}</div>
    </section>;
}
