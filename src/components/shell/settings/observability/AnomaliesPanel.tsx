import { useState } from 'react';
import { getControls } from '../../../../api/financialControls';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState,cardClass,buttonClass } from './ReadState';
import { name } from './format';
export function AnomaliesPanel(){
 const [cursor,setCursor]=useState<string>();const q=useObservabilityQuery('anomalies:'+(cursor??''),s=>getControls('anomalies',s,cursor));
 return <section className="mt-4"><h4 className="text-sm font-medium text-primary">Anomalies</h4>
 <p className="my-2 text-xs text-tertiary">Deterministic daily financial, usage and source-quality signals. Alerts remain disabled.</p>
 <ReadState query={q}>{p=><>{p.state==='EVALUATED'&&p.evaluated>0&&p.items.every(a=>a.state==='NORMAL')?<p>No anomaly detected for evaluated periods</p>:p.state==='UNEVALUATED'||p.state==='INSUFFICIENT_DATA'?<p>Anomaly evaluation unavailable · {name(p.state)}</p>:null}
 {p.items.filter(a=>a.state!=='NORMAL').map((a,i)=><div className={cardClass} key={(a.anomaly_id??a.generation)+':'+i}>
 <p>{name(a.type??'')} · {a.label} · {a.day} · {a.label==='USAGE'?a.quantity_unit:a.currency} · {a.severity} · {name(a.state)}</p>
 {(a.model||a.unit_id)&&<p className="text-xs text-tertiary">{a.provider} {a.model} {a.unit_id}</p>}
 <p className="text-sm text-secondary">Observed: {a.observed??'Unknown'} · Baseline: {a.baseline??'Unavailable'} · Absolute deviation: {a.absolute_deviation??'Unavailable'} · Relative deviation: {a.relative_deviation??'Unavailable'}</p>
 <p className="text-xs text-tertiary">{a.reasons.map(name).join(' · ')} · Confidence: {a.confidence} · Persistence: {a.persistence} distinct days · Freshness: {a.freshness} · Robust score: {a.robust_score??'Unavailable'}</p>
 {a.impact_type==='FIXED_POPULATION_UNIT_EXPOSURE'&&<p className="text-xs text-tertiary">Fixed-population exposure estimate: {a.impact} {a.currency} · {a.unit_population} units. This is separate from actual billed spend.</p>}
 {a.drivers.map((d,j)=><p key={j} className="text-xs text-tertiary">{name(d.driver)}: {d.amount}</p>)}{a.residual!==null&&<p className="text-xs text-tertiary">Unexplained residual: {a.residual}</p>}</div>)}
 {p.next_cursor&&<button className={buttonClass} onClick={()=>setCursor(p.next_cursor!)}>Next anomalies</button>}</>}</ReadState></section>;
}
