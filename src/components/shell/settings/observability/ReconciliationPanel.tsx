import { useState } from 'react';
import { getReconciliation } from '../../../../api/reconciliation';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass, buttonClass } from './ReadState';
import { name } from './format';
export function ReconciliationPanel(){
 const [period,setPeriod]=useState('');
 const query=useObservabilityQuery('reconciliation:'+period,s=>getReconciliation(s,period||undefined));
 return <section className="mt-4 space-y-3" aria-label="Reconciliation and allocation">
  <h4 className="text-sm font-medium text-secondary">Reconciliation</h4>
  <label className="block text-sm text-secondary">UTC usage day <input aria-label="Reconciliation day" type="date" className={buttonClass} value={period} onChange={e=>setPeriod(e.target.value)}/></label>
  <ReadState query={query}>{data=><div className="space-y-3">{data.items.map(v=><div key={v.period} className={cardClass}>
   <p className="text-sm text-primary">{v.display_state} · {v.period} · {data.environment}</p>
   {v.source_mode==='TEST_FIXTURE'&&<p className="text-xs text-secondary">TEST FIXTURE — synthetic reconciliation</p>}
   <p className="text-xs text-tertiary">Billing: {name(v.billing_freshness)} · Pricing: {name(v.pricing_freshness)} · {name(v.status)}</p>
   <p className="text-xs text-tertiary">Source invoice months: {v.invoice_months.join(', ')||'Unavailable'} · Runtime source: {name(v.runtime_freshness)} · Current accounting debt: {v.current_accounting_debt??'Unavailable'}</p>
   <p className="text-xs text-tertiary">{v.reasons.map(name).join(' · ')}</p>
   {!v.generation&&<p className="text-sm text-secondary">Reconciliation source unavailable.</p>}
   {v.runtime_quality&&<p className="text-xs text-secondary">Ledger completeness ratio: {v.runtime_quality.completeness??'Unavailable'} · Accounting debt: {v.runtime_quality.debt} · Source debt: {v.runtime_quality.global_debt} · Pending: {v.runtime_quality.pending}</p>}
   {v.coverage&&<><p className="text-xs text-secondary">Mapping amount coverage ratio: {v.coverage.mapping_amount??'Unavailable'} · Allocation coverage ratio: {v.coverage.allocation??'Unavailable'} · Monetary accounting ratio: {v.coverage.monetary_accounting??'Unavailable'}</p><p className="text-xs text-secondary">Known runtime quantity groups ratio: {v.coverage.runtime_quantity_known??'Unavailable'} · Known billing quantity groups ratio: {v.coverage.billing_quantity_known??'Unavailable'} · Compatible quantity groups ratio: {v.coverage.compatible_quantity??'Unavailable'} · Price reference coverage ratio: {v.coverage.price_reference??'Unavailable'}</p></>}
   {v.quantity_detail_state==='BOUNDED_SUMMARY'&&<p className="text-xs text-secondary">Bounded quantity summary · {v.quantity_count} component buckets in the complete generation.</p>}
   {v.quantities.map((q,i)=><p key={i} className="text-xs text-secondary">{q.model} · {name(q.component)} · {q.unit}: Runtime {q.runtime_quantity??'Unknown'} ({q.runtime_unknown} unknown) · Billed {q.billing_quantity??'Unknown'} ({q.billing_unknown} unknown) · Difference {q.difference??'Unavailable'} · Variance % {q.variance_pct??'Unavailable'} · {name(q.confidence)}</p>)}
   {v.amounts.map(a=><div key={a.currency} className="mt-3 space-y-1 text-sm text-secondary">
    <p>Actual billed net: {a.net} {a.currency}</p><p className="text-xs">Gross {a.gross} · Credits {a.credit} · Adjustments {a.adjustment} · Tax {a.tax} · Rounding {a.rounding} {a.currency}</p>
    <p className="text-xs">Catalog reference: {a.catalog_reference??'Unavailable'} · Monetary variance: {a.monetary_variance??'Unavailable'}</p>
    {a.eur_state==='EUR_UNAVAILABLE'&&<p className="text-xs">EUR reporting unavailable.</p>}
    <h5 className="pt-2 font-medium">Allocation · {a.currency}</h5>
    <p>Direct: {a.direct} {a.currency} · Absolute exposure {a.percentages.direct===null?'Unavailable':a.percentages.direct+'%'}</p>
    <p>Shared: {a.shared} {a.currency} · Absolute exposure {a.percentages.shared===null?'Unavailable':a.percentages.shared+'%'}</p>
    <p className="font-medium text-primary">Unallocated: {a.unallocated} {a.currency} · Absolute exposure {a.percentages.unallocated===null?'Unavailable':a.percentages.unallocated+'%'}</p>
    <p className="text-xs text-tertiary">Ratio basis: absolute source components {a.absolute} {a.currency}. Signed amounts preserve credits and corrections.</p>
   </div>)}
  </div>)}</div>}</ReadState>
  <p className="text-xs text-tertiary">Mapping and allocation policy are read-only. M10 stored monetary estimates are unavailable. Reconciliation describes the observed source generation; it does not establish invoice finality.</p>
 </section>;
}
