import { getJson, ApiError } from './client';
import { ContractError } from '../components/shell/settings/observability/contract';
export type ControlKind='budgets'|'forecasts'|'anomalies';
export interface FinancialControl {
 generation:string;kind:string;issue:string;month:string;currency:string;scope:string;state:string;freshness:string;
 budget_id:string|null;budget_version:string|null;amount:string|null;actual_observed:string|null;actual_eligible:string|null;
 utilization_pct:string|null;variance:string|null;coverage:string|null;positive_zero_budget_breach:boolean;
 actual_to_cutoff:string|null;remaining:string|null;forecast:string|null;lower:string|null;upper:string|null;
 range_type:string;tier:number;algorithm:string;confidence:string;risk:string;eligible_days:number;period_days:number;prior_eligible_days:number;
 backtest_quality:Record<string,string|null>|null;day_states:Record<string,number>;
 anomaly_id:string|null;type:string|null;day:string|null;observed:string|null;baseline:string|null;absolute_deviation:string|null;
 relative_deviation:string|null;severity:string;reasons:string[];persistence:number;baseline_days:number;label:string|null;quantity_unit:string|null;model:string|null;provider:string|null;unit_id:string|null;drivers:{driver:string;amount:string}[];residual:string|null;robust_score:string|null;impact:string|null;impact_type:string|null;unit_population:number;
}
export interface ControlPage {environment:string;state:string;items:FinancialControl[];next_cursor:string|null;evaluated:number}
const states=['AVAILABLE','NOT_CONFIGURED','UNEVALUATED','INSUFFICIENT_DATA','CURRENCY_UNAVAILABLE','SCOPE_UNAVAILABLE','SOURCE_STALE','RECONCILIATION_INCOMPLETE','ZERO_BUDGET','HEALTHY','INFORMATIONAL','WATCH','AT_RISK','AT_LIMIT','EXCEEDED','POLICY_UNAVAILABLE','SOURCE_QUALITY_DEGRADATION','UNSUPPORTED_NET_CREDIT_REGIME','EVALUATED','FAILED','OBSERVED','ACTIVE','RESOLVED','NORMAL'];
const reasons=['SOURCE_QUALITY_DEGRADATION','BILLING_CORRECTION','WORKLOAD_MIX_SHIFT','PRICE_CHANGE','USAGE_VOLUME_INCREASE','SHARED_INFRASTRUCTURE_CHANGE','UNALLOCATED_MOVEMENT','UNALLOCATED_INCREASE','UNEXPLAINED'];
function obj(v:unknown):Record<string,unknown>{if(!v||typeof v!=='object'||Array.isArray(v))throw new ContractError();return v as Record<string,unknown>}
function choice(v:unknown,values:string[]):string{if(typeof v!=='string'||!values.includes(v))throw new ContractError();return v}
function text(v:unknown,pattern:RegExp,max=140):string{if(typeof v!=='string'||v.length>max||!pattern.test(v))throw new ContractError();return v}
function count(v:unknown):number{if(typeof v!=='number'||!Number.isSafeInteger(v)||v<0)throw new ContractError();return v}
function money(v:unknown):string|null{return v===null?null:text(v,/^-?\d+(?:\.\d+)?$/)}
export function decodeControls(value:unknown):ControlPage {
 const p=obj(value);if(p.schema_version!==1)throw new ContractError('incompatible');
 if(!Array.isArray(p.items)||p.items.length>100)throw new ContractError();
 const items=p.items.map(raw=>{const r=obj(raw);const out={} as FinancialControl;
 out.generation=text(r.generation,/^[a-f0-9]{64}$/);out.kind=choice(r.kind,['BUDGET','FORECAST','ANOMALY']);
 out.issue=text(r.issue,/^\d{4}-\d\d-\d\dT[0-9:.+Z-]+$/,40);out.month=text(r.month,/^\d{4}-(0[1-9]|1[0-2])$/);out.currency=text(r.currency,/^[A-Z]{3}$/);
 out.scope=text(r.scope,/^[A-Za-z0-9_.-]{1,32}$/);out.state=choice(r.state,states);out.freshness=choice(r.freshness,['FRESH','STALE','UNAVAILABLE']);
 for(const k of ['budget_id','budget_version'] as const)out[k]=r[k]===null?null:text(r[k],/^[A-Za-z0-9_.-]{1,32}$/);
 for(const k of ['amount','actual_observed','actual_eligible','utilization_pct','variance','coverage','actual_to_cutoff','remaining','forecast','lower','upper','observed','baseline','absolute_deviation','relative_deviation'] as const)out[k]=money(r[k]);
 if(typeof r.positive_zero_budget_breach!=='boolean')throw new ContractError();out.positive_zero_budget_breach=r.positive_zero_budget_breach;
 out.range_type=choice(r.range_type,['NONE','SCENARIO_RANGE']);out.tier=count(r.tier);if(out.tier>2)throw new ContractError();
 out.algorithm=choice(r.algorithm,['unavailable-v1','eligible-run-rate-v1','weekday-median-v1','robust-daily-v1']);
 out.confidence=choice(r.confidence,['UNAVAILABLE','LOW','QUALIFIED','HIGH','QUALITY']);out.risk=choice(r.risk,['ON_TRACK','WATCH','PROJECTED_OVER','ALREADY_EXCEEDED','UNCERTAIN','INSUFFICIENT_DATA','SOURCE_STALE']);
 for(const k of ['eligible_days','period_days','prior_eligible_days','persistence','baseline_days'] as const)out[k]=count(r[k]);
 out.day_states={};for(const [k,v] of Object.entries(obj(r.day_states))){choice(k,['ELIGIBLE_COMPLETE','OPEN','TOO_RECENT','STALE','MISSING','PARTIAL','EXCLUDED']);out.day_states[k]=count(v)}
 out.backtest_quality=null;if(r.backtest_quality!==null){const q=obj(r.backtest_quality);out.backtest_quality={mae:money(q.mae),wape:money(q.wape),bias:money(q.bias)}}
 out.anomaly_id=r.anomaly_id===null?null:text(r.anomaly_id,/^[a-f0-9]{64}$/);
 out.type=r.type===null?null:choice(r.type,['TOTAL_SPEND','PROVIDER_MODEL_SPEND','SHARED_INFRASTRUCTURE','OBSERVABILITY_SPEND','UNIT_COST','USAGE_VOLUME','BILLING_CORRECTION','UNALLOCATED_EXPOSURE','RECONCILIATION_QUALITY']);
 out.day=r.day===null?null:text(r.day,/^\d{4}-\d\d-\d\d$/);out.severity=choice(r.severity,['NONE','WATCH','HIGH','CRITICAL']);
 if(!Array.isArray(r.reasons)||r.reasons.length>8)throw new ContractError();out.reasons=r.reasons.map(v=>choice(v,reasons));
 out.label=r.label===null?null:choice(r.label,['MONETARY','USAGE','QUALITY']);
 out.quantity_unit=r.quantity_unit===null?null:choice(r.quantity_unit,['tokens','characters','calls']);
 out.model=r.model===null?null:text(r.model,/^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$/,110);out.provider=r.provider===null?null:choice(r.provider,['gcp.vertex_ai','gcp.gemini','other']);out.unit_id=r.unit_id===null?null:text(r.unit_id,/^[A-Z_]{1,32}$/);
 if(!Array.isArray(r.drivers)||r.drivers.length>100)throw new ContractError();out.drivers=r.drivers.map(v=>{const d=obj(v);const amount=money(d.amount);if(amount===null)throw new ContractError();return {driver:choice(d.driver,['USAGE_VOLUME','UNIT_PRICE','WORKLOAD_MIX','SHARED_INFRASTRUCTURE','UNALLOCATED']),amount}});out.residual=money(r.residual);out.robust_score=money(r.robust_score);out.impact=money(r.impact);out.impact_type=r.impact_type===null?null:choice(r.impact_type,['FIXED_POPULATION_UNIT_EXPOSURE','ACTUAL_DEVIATION']);out.unit_population=count(r.unit_population);return out;
 });
 return {environment:choice(p.environment,['local','development','staging','production']),state:choice(p.state,states),items,
 next_cursor:p.next_cursor===null?null:text(p.next_cursor,/^[A-Za-z0-9_=-]+$/,512),evaluated:count(p.evaluated)};
}
export async function getControls(kind:ControlKind,signal?:AbortSignal,cursor?:string,detail?:string):Promise<ControlPage>{
 if(detail&&!/^[A-Za-z0-9_.-]{1,64}$/.test(detail))throw new ContractError();
 const query=cursor?'?cursor='+encodeURIComponent(cursor):'';
 try{return decodeControls(await getJson('/api/observability/finops/'+kind+(detail?'/'+encodeURIComponent(detail):'')+query,signal))}
 catch(e){if(e instanceof ApiError)throw new ApiError('Financial controls unavailable.',e.status);if(e instanceof SyntaxError)throw new ContractError();throw e}
}
