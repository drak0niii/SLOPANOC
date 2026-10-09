import { getJson, ApiError } from './client';
import { ContractError } from '../components/shell/settings/observability/contract';
export const unitIds=['PROVIDER_ATTEMPT','MODEL_OPERATION','ACCEPTED_TURN','AVAILABLE_TURN','TIMELY_TERMINAL_TURN','GENERAL_TURN','TEAMS_LOOKUP_TURN','GOVERNED_TROUBLESHOOTING_TURN','COMPLEX_MULTI_AGENT_TURN','RESOLVED_INCIDENT','FIRST_TIME_CORRECT','ACCEPTED_RECOMMENDATION','SUCCESSFUL_REMEDIATION','AUTOMATION_ARTIFACT','GOVERNED_ANSWER','TICKET','TROUBLESHOOTING_CASE','AGENT_INVOCATION','TOOL_INVOCATION','KNOWLEDGE_RETRIEVAL'];
const states=['AVAILABLE','PARTIAL','INSUFFICIENT_DATA','FINANCIAL_SCOPE_UNAVAILABLE','DENOMINATOR_UNAVAILABLE','RECONCILIATION_INCOMPLETE','SOURCE_STALE','DRIVER_UNAVAILABLE','CURRENCY_UNAVAILABLE','DEFINED_NOT_AVAILABLE'];
const moneyFields=['direct_amount','direct_without_unit_scope','distributed_shared','undistributed_shared','fully_loaded_amount','unallocated_amount','actual_net'] as const;
export interface UnitResult {
 operational_context:{source:string;availability_ratio:string|null;timely_terminal_ratio:string|null;mean_latency_ms:string|null;latency_observed:number;eligible_turns:number}|null;
 unit_id:string;name:string;definition:string;dimension:string;currency:string;state:string;direct_state:string;fully_loaded_state:string;unit_count:number|null;
 direct_amount:string;direct_without_unit_scope:string;direct_cost_per_unit:string|null;distributed_shared:string;undistributed_shared:string;fully_loaded_amount:string;fully_loaded_cost_per_unit:string|null;unallocated_amount:string;actual_net:string;
 coverage:Record<string,string|null>;denominator_freshness:string;financial_freshness:string;analytical_view:string;additive:false;price_semantics:string;outcome_source:string;quality_context:string;reasons:string[];
}
export interface UnitDay {period:string;generation:string|null;reconciliation_generation:string|null;denominator_generations:string[];unit_version:string;driver_version:string;algorithm_version:string;published_at:string|null;job_state:string;results:UnitResult[];result_count:number;bounded_summary:boolean}
export interface UnitPage {schema_version:1;environment:string;period_basis:'usage';configuration:'READ_ONLY';analytical_views:'ALTERNATIVE_NON_ADDITIVE';items:UnitDay[];next_cursor:string|null}
function obj(v:unknown):Record<string,unknown>{if(!v||typeof v!=='object'||Array.isArray(v))throw new ContractError();return v as Record<string,unknown>;}
function list(v:unknown,max:number):unknown[]{if(!Array.isArray(v)||v.length>max)throw new ContractError();return v;}
function choice(v:unknown,values:string[]):string{if(typeof v!=='string'||!values.includes(v))throw new ContractError();return v;}
function text(v:unknown,max:number):string{if(typeof v!=='string'||!v.length||v.length>max)throw new ContractError();return v;}
function decimal(v:unknown):string{if(typeof v!=='string'||v.length>100||!/^\-?\d+(?:\.\d{1,38})?$/.test(v))throw new ContractError();return v;}
function optional(v:unknown):string|null{return v===null?null:decimal(v);}
function hash(v:unknown):string|null{if(v===null)return null;if(typeof v!=='string'||!/^[a-f0-9]{64}$/.test(v))throw new ContractError();return v;}
function count(v:unknown):number{if(typeof v!=='number'||!Number.isSafeInteger(v)||v<0)throw new ContractError();return v;}
function day(v:unknown):string{if(typeof v!=='string'||!/^\d{4}-\d\d-\d\d$/.test(v)||!Number.isFinite(Date.parse(v))||new Date(v).toISOString().slice(0,10)!==v)throw new ContractError();return v;}
export function decodeUnitEconomics(value:unknown):UnitPage {
 const p=obj(value);if(p.schema_version!==1)throw new ContractError('incompatible');
 const items=list(p.items,31).map(raw=>{const d=obj(raw);const results=list(d.results,100).map(raw=>{const r=obj(raw);const c=obj(r.coverage);const coverage:Record<string,string|null>={};
  for(const k of ['reconciliation','numerator_scope','denominator','shared_driver','currency','mapping','unallocated_share'])coverage[k]=optional(c[k]);
  if(r.additive!==false||r.quality_context!=='M19_OUTCOMES_UNAVAILABLE')throw new ContractError();
  const currency=text(r.currency,3);if(!/^[A-Z]{3}$/.test(currency))throw new ContractError();
  const dimension=text(r.dimension,128);if(!/^(ALL|other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$/.test(dimension))throw new ContractError();
  const result={unit_id:choice(r.unit_id,unitIds),name:text(r.name,80),definition:text(r.definition,400),dimension,currency,state:choice(r.state,states),direct_state:choice(r.direct_state,states),fully_loaded_state:choice(r.fully_loaded_state,states),unit_count:r.unit_count===null?null:count(r.unit_count),direct_cost_per_unit:optional(r.direct_cost_per_unit),fully_loaded_cost_per_unit:optional(r.fully_loaded_cost_per_unit),coverage,financial_freshness:choice(r.financial_freshness,['FRESH','STALE','UNAVAILABLE']),denominator_freshness:choice(r.denominator_freshness,['FRESH','STALE','UNAVAILABLE','RETAINED']),analytical_view:choice(r.analytical_view,unitIds),additive:false as const,price_semantics:choice(r.price_semantics,['AGGREGATE_AVERAGE','YIELD_ADJUSTED_AVERAGE']),outcome_source:choice(r.outcome_source,['M9','M10','M19','ITSM','UNAVAILABLE']),quality_context:'M19_OUTCOMES_UNAVAILABLE',reasons:list(r.reasons,10).map(v=>choice(v,states))} as UnitResult;
  if(r.operational_context==null)result.operational_context=null;
  else {const o=obj(r.operational_context);result.operational_context={source:choice(o.source,['M9_ACCEPTANCE_COHORT']),availability_ratio:optional(o.availability_ratio),timely_terminal_ratio:optional(o.timely_terminal_ratio),mean_latency_ms:optional(o.mean_latency_ms),latency_observed:count(o.latency_observed),eligible_turns:count(o.eligible_turns)};}
  for(const k of moneyFields)result[k]=decimal(r[k]);return result;
 });
 if(typeof d.bounded_summary!=='boolean'||typeof d.driver_version!=='string'||!/^[A-Za-z0-9_.-]{1,32}$/.test(d.driver_version))throw new ContractError();
 if(d.published_at!==null&&(typeof d.published_at!=='string'||!Number.isFinite(Date.parse(d.published_at))))throw new ContractError();
 return {period:day(d.period),generation:hash(d.generation),reconciliation_generation:hash(d.reconciliation_generation),denominator_generations:list(d.denominator_generations,2).map(v=>{const x=hash(v);if(!x)throw new ContractError();return x;}),unit_version:choice(d.unit_version,['units-v1']),driver_version:d.driver_version,algorithm_version:choice(d.algorithm_version,['unit-economics-v1']),published_at:d.published_at as string|null,job_state:choice(d.job_state,['AVAILABLE','UNAVAILABLE','FAILED']),results,result_count:count(d.result_count),bounded_summary:d.bounded_summary};
 });
 if(p.next_cursor!==null&&(typeof p.next_cursor!=='string'||p.next_cursor.length>512||!/^[A-Za-z0-9_=-]+$/.test(p.next_cursor)))throw new ContractError();
 return {schema_version:1,environment:choice(p.environment,['local','development','staging','production']),period_basis:choice(p.period_basis,['usage']) as 'usage',configuration:choice(p.configuration,['READ_ONLY']) as 'READ_ONLY',analytical_views:choice(p.analytical_views,['ALTERNATIVE_NON_ADDITIVE']) as 'ALTERNATIVE_NON_ADDITIVE',items,next_cursor:p.next_cursor as string|null};
}
export async function getUnitEconomics(signal?:AbortSignal,period?:string,unit?:string):Promise<UnitPage>{
 const params=new URLSearchParams();if(period){day(period);params.set('start',period);const end=new Date(period);end.setUTCDate(end.getUTCDate()+1);params.set('end',end.toISOString().slice(0,10));}
 if(unit&&!unitIds.includes(unit))throw new ContractError();
 try{return decodeUnitEconomics(await getJson('/api/observability/finops/unit-economics'+(unit?'/'+unit:'')+'?'+params,signal));}
 catch(e){if(e instanceof ApiError)throw new ApiError('Unit economics is unavailable.',e.status);if(e instanceof SyntaxError)throw new ContractError();throw e;}
}
