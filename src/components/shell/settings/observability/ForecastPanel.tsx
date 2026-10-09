import { useState } from 'react';
import { getControls } from '../../../../api/financialControls';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState,cardClass,buttonClass } from './ReadState';
import { name,timestamp } from './format';
export function ForecastPanel(){
 const [cursor,setCursor]=useState<string>();const q=useObservabilityQuery('forecasts:'+(cursor??''),s=>getControls('forecasts',s,cursor));
 return <section className="mt-4"><h4 className="text-sm font-medium text-primary">Forecasts</h4>
 <p className="my-2 text-xs text-tertiary">Forecasts are estimates, separate from actual spend. Scenario ranges are provisional and are not statistical confidence intervals.</p>
 <ReadState query={q}>{p=><>{!p.items.length&&<p>Forecast unavailable · {name(p.state)}</p>}{p.items.map((f,i)=><div className={cardClass} key={f.generation+':'+i}>
 <p>{f.month} · {f.currency} · {name(f.state)} · {f.freshness}</p>
 {f.forecast===null?<p>Forecast unavailable — {name(f.state)}</p>:<p>Actual to cutoff: {f.actual_to_cutoff} · Remaining estimate: {f.remaining} · Month-end forecast: {f.forecast}</p>}
 <p className="text-xs text-tertiary">Tier {f.tier} · {f.algorithm} · Confidence: {f.confidence} · Risk: {name(f.risk)} · {f.eligible_days} eligible days / {f.period_days} calendar days</p>
 {f.range_type==='SCENARIO_RANGE'&&<p>Scenario range: {f.lower} – {f.upper} {f.currency}</p>}
 <p className="text-xs text-tertiary">Issued {timestamp(f.issue)} · Backtest WAPE: {f.backtest_quality?.wape??'Unavailable'} · Tier 3 not implemented</p></div>)}
 {p.next_cursor&&<button className={buttonClass} onClick={()=>setCursor(p.next_cursor!)}>Next forecasts</button>}</>}</ReadState></section>;
}
