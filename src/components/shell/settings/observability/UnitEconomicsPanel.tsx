import { useState } from 'react';
import { getUnitEconomics, unitIds } from '../../../../api/unitEconomics';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState, cardClass, buttonClass } from './ReadState';
import { name, timestamp } from './format';
export function UnitEconomicsPanel(){
 const [period,setPeriod]=useState('');const [unit,setUnit]=useState('');
 const query=useObservabilityQuery('unit-economics:'+period+':'+unit,s=>getUnitEconomics(s,period||undefined,unit||undefined));
 return <section className="mt-4 space-y-3" aria-label="Unit economics">
  <h4 className="text-sm font-medium text-secondary">Unit economics</h4>
  <p className="text-xs text-tertiary">Reconciled aggregate averages. Alternative unit views are not additive and must not be summed. They do not establish individual billed lineage.</p>
  <div className="flex flex-wrap gap-3"><label className="text-sm text-secondary">UTC usage day <input aria-label="Unit economics day" type="date" className={buttonClass} value={period} onChange={e=>setPeriod(e.target.value)}/></label>
   <label className="text-sm text-secondary">Unit <select aria-label="Unit economics unit" className={buttonClass} value={unit} onChange={e=>setUnit(e.target.value)}><option value="">All units</option>{unitIds.map(id=><option key={id} value={id}>{name(id)}</option>)}</select></label></div>
  <ReadState query={query}>{data=><div className="space-y-3">{data.items.map(day=><div key={day.period}>
   <p className="text-xs text-tertiary">{day.period} · {data.environment} · Published {timestamp(day.published_at??undefined)}</p>
   {day.bounded_summary&&<p className="text-xs text-secondary">Bounded summary. Select a unit to inspect its model comparison.</p>}
   {!day.results.length&&<p className="text-sm text-secondary">No unit economics for this selection.</p>}
   {day.results.map(v=><article key={v.unit_id+':'+v.dimension+':'+v.currency} className={cardClass}>
    <h5 className="text-sm font-medium text-primary">{v.name} · {v.dimension==='ALL'?'All compatible activity':v.dimension}</h5>
    <p className="text-xs text-secondary">{name(v.state)} · Eligible units: {v.unit_count??'Unknown'}</p>
    {v.state==='DEFINED_NOT_AVAILABLE'?<p className="text-sm text-secondary">Not available — authoritative outcome data required.</p>:<>
     <p className="text-sm text-secondary">Direct average cost / unit: {v.direct_cost_per_unit===null?'Unavailable':v.direct_cost_per_unit+' '+v.currency} · {name(v.direct_state)}</p>
     <p className="text-sm text-secondary">Fully loaded average cost / unit: {v.fully_loaded_cost_per_unit===null?'Unavailable':v.fully_loaded_cost_per_unit+' '+v.currency} · {name(v.fully_loaded_state)}</p>
     {v.price_semantics==='YIELD_ADJUSTED_AVERAGE'&&<p className="text-xs text-tertiary">Yield-adjusted average: eligible workload cost includes unsuccessful activity.</p>}
     {v.operational_context?<p className="text-xs text-secondary">M9 acceptance cohort: availability ratio {v.operational_context.availability_ratio??'Unknown'} · Timely terminal ratio {v.operational_context.timely_terminal_ratio??'Unknown'} · Mean latency {v.operational_context.mean_latency_ms??'Unknown'} ms ({v.operational_context.latency_observed} observed / {v.operational_context.eligible_turns} turns). Operational availability is separate from business quality.</p>:<p className="text-xs text-tertiary">Latency and reliability comparison unavailable at this unit grain.</p>}
     <details className="mt-2 text-xs text-secondary"><summary className="cursor-pointer">Unit cost details</summary>
      <p>Direct amount: {v.direct_amount} {v.currency} · Direct without unit scope: {v.direct_without_unit_scope} {v.currency}</p>
      <p>Distributed shared: {v.distributed_shared} {v.currency} · Fully loaded amount: {v.fully_loaded_amount} {v.currency}</p>
      <p>Actual source net: {v.actual_net} {v.currency}</p>
      <p>Denominator: {v.definition}</p>
      {Object.entries(v.coverage).map(([key,value])=><p key={key}>{name(key)} coverage ratio: {value??'Unavailable'}</p>)}
      <p>Financial freshness: {name(v.financial_freshness)} · Denominator freshness: {name(v.denominator_freshness)}</p>
      <p>Definition {day.unit_version} · Driver {day.driver_version} · Generation {day.generation??'Unavailable'}</p>
     </details>
     <p className="mt-2 text-sm font-medium text-primary">Undistributed shared: {v.undistributed_shared} {v.currency}</p>
     <p className="text-sm font-medium text-primary">Unallocated exposure: {v.unallocated_amount} {v.currency}</p>
     <p className="text-xs text-tertiary">Source amounts are contextual exposure, not additional unit prices. {v.reasons.map(name).join(' · ')}</p>
    </>}
   </article>)}
  </div>)}</div>}</ReadState>
  <p className="text-xs text-tertiary">Definitions and allocation policies are read-only. Quality and first-time-correct comparisons require authoritative outcome data in M19.</p>
 </section>;
}
