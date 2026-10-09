import { useState } from 'react';
import { getControls } from '../../../../api/financialControls';
import { useObservabilityQuery } from './useObservabilityQuery';
import { ReadState,cardClass,buttonClass } from './ReadState';
import { name,timestamp } from './format';
export function BudgetPanel(){
 const [cursor,setCursor]=useState<string>();
 const q=useObservabilityQuery('budgets:'+(cursor??''),s=>getControls('budgets',s,cursor));
 return <section className="mt-4"><h4 className="text-sm font-medium text-primary">Budgets</h4>
 <p className="my-2 text-xs text-tertiary">Read-only, provisional monthly UTC usage budgets. Overlapping scopes are independent views.</p>
 <ReadState query={q}>{p=><>{p.state==='NOT_CONFIGURED'?<p>No budget configured</p>:!p.items.length?<p>Budget evaluation unavailable · {name(p.state)}</p>:p.items.map((b,i)=><div className={cardClass} key={b.generation+':'+i}>
 <p>{b.budget_id} · {name(b.state)} · {b.month} · {b.currency}</p>
 <p className="text-sm text-secondary">Budget: {b.amount??'Unavailable'} · Actual eligible: {b.actual_eligible??'Unavailable'} · Observed actual: {b.actual_observed??'Unavailable'}</p>
 <p className="text-xs text-tertiary">Utilization: {b.utilization_pct===null?'Unavailable':b.utilization_pct+'%'} · Coverage: {b.coverage??'Unavailable'} · {b.freshness} · Version {b.budget_version} · Issued {timestamp(b.issue)}</p>
 {b.positive_zero_budget_breach&&<p>Positive spend against zero budget</p>}</div>)}
 {p.next_cursor&&<button className={buttonClass} onClick={()=>setCursor(p.next_cursor!)}>Next budgets</button>}</>}</ReadState></section>;
}
