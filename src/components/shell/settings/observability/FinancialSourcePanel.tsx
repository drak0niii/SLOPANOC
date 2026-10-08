import {useState} from 'react';
import {getBillingHealth,getBillingSummary,getPricingStatus} from '../../../../api/finops';
import {useObservabilityQuery} from './useObservabilityQuery';
import {ReadState,cardClass,buttonClass} from './ReadState';
import {name,timestamp} from './format';
export function FinancialSourcePanel(){
    const [basis,setBasis]=useState<'usage'|'invoice'>('usage');
    const [invoice,setInvoice]=useState(new Date().toISOString().slice(0,7));
    const health=useObservabilityQuery('financial-sources',getBillingHealth);
    const billing=useObservabilityQuery('billing-summary:'+basis+':'+invoice,s=>getBillingSummary(s,basis,basis==='invoice'?invoice.replace('-',''):undefined));
    const pricing=useObservabilityQuery('pricing-status',getPricingStatus);
    return <div className="mt-4 space-y-3">
      <section aria-label="Billing source" className={cardClass}><h4 className="text-sm text-secondary">Billing Source</h4>
        <ReadState query={health}>{data=><div>{data.sources.map(s=><p key={s.source} className="text-xs text-tertiary">{name(s.source)}: {name(s.state)} · {s.mode==='TEST_FIXTURE'?'TEST FIXTURE':name(s.mode)} · Source updated: {timestamp(s.as_of??undefined)} · {name(s.reason)} · Cadence {s.cadence_seconds}s · Delayed after {s.delayed_seconds}s · Stale after {s.stale_seconds}s</p>)}<p className="text-xs text-tertiary">Freshness policy is provisional. Configuration is read-only.</p></div>}</ReadState>
      </section>
      <section aria-label="Actual billed amount" className={cardClass}><h4 className="text-sm text-secondary">Actual billed amount</h4>
        <label className="block text-sm text-secondary">Period basis <select aria-label="Billing period basis" className={buttonClass} value={basis} onChange={e=>setBasis(e.target.value as 'usage'|'invoice')}><option value="usage">Usage time</option><option value="invoice">Invoice month</option></select></label>
        {basis==='invoice'&&<label className="text-sm text-secondary">Invoice month <input aria-label="Invoice month" type="month" value={invoice} onChange={e=>setInvoice(e.target.value)}/></label>}
        <ReadState query={billing}>{data=><div>
          {data.source.mode==='TEST_FIXTURE'&&<p className="text-sm text-secondary">TEST FIXTURE — synthetic financial data</p>}
          <p className="text-xs text-tertiary">{name(data.source.state)} · {data.period_basis==='invoice'?`Invoice ${data.invoice_month}`:`Usage ${data.start} – ${data.end}`} · {data.coverage_complete?'Covered source window':'Partial source coverage'}</p>
          {data.amounts===null?<p className="text-sm text-tertiary">Actual billing unavailable.</p>:data.amounts.length===0?<p className="text-sm text-tertiary">No source charges in the covered range.</p>:data.amounts.map(a=><div key={a.currency+':'+a.category} className="mt-2 text-sm text-secondary">
            <p>{name(a.category)} · {a.currency}</p><p>Gross regular charges: {a.gross} {a.currency} · Credits: {a.credits} {a.currency}</p>
            <p>Adjustments: {a.adjustments} {a.currency} · Tax: {a.tax} {a.currency} · Rounding: {a.rounding} {a.currency}</p>
            <p>Net including source tax: {a.net} {a.currency}</p>{a.eur_state==='EUR_REPORTING_UNAVAILABLE'&&<p className="text-xs text-tertiary">EUR reporting unavailable.</p>}
          </div>)}
          <p className="text-xs text-tertiary">Source amounts are unreconciled. Usage-time totals may differ from invoice-period charges.</p>
        </div>}</ReadState>
      </section>
      <section aria-label="Catalog pricing source" className={cardClass}><h4 className="text-sm text-secondary">Catalog pricing source</h4>
        <ReadState query={pricing}>{data=><div><p className="text-sm text-secondary">{name(data.source.state)} · {data.source.mode==='TEST_FIXTURE'?'TEST FIXTURE':name(data.source.mode)}</p><p className="text-xs text-tertiary">Known pricing snapshots: {timestamp(data.earliest_pricing_as_of??undefined)} – {timestamp(data.latest_pricing_as_of??undefined)}</p><p className="text-xs text-tertiary">Pricing outside known snapshot coverage is unavailable. Catalog prices remain separate from actual billed amounts.</p></div>}</ReadState>
      </section>
    </div>;
}
