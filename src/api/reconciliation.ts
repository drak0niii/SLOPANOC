import { getJson, ApiError } from './client';
import { ContractError } from '../components/shell/settings/observability/contract';
export interface ReconciliationAmount {currency:string;gross:string;credit:string;adjustment:string;tax:string;rounding:string;net:string;absolute:string;direct:string;shared:string;unallocated:string;catalog_reference:string|null;monetary_variance:string|null;eur_state:string;percentages:{direct:string|null;shared:string|null;unallocated:string|null};ratios:{direct:string|null;shared:string|null;unallocated:string|null}}
export interface ReconciliationQuantity {model:string;component:string;unit:string;runtime_quantity:string|null;billing_quantity:string|null;difference:string|null;variance_pct:string|null;runtime_unknown:number;billing_unknown:number;confidence:string;reason:string|null}
export interface ReconciliationView {period:string;invoice_months:string[];runtime_freshness:string;current_accounting_debt:number|null;generation:string|null;status:string;display_state:string;billing_freshness:string;pricing_freshness:string;source_mode:string;reasons:string[];quantity_count:number;quantity_detail_state:string;quantities:ReconciliationQuantity[];amounts:ReconciliationAmount[];coverage:Record<string,string|null>|null;runtime_quality:{debt:number;global_debt:number;pending:number;completeness:string|null}|null}
export interface ReconciliationPage {environment:string;items:ReconciliationView[];next_cursor:string|null}
const statuses=['UNRECONCILED','PARTIALLY_RECONCILED','RECONCILED','VARIANCE_REVIEW','CORRECTED'];
const confidence=['EXACT','HIGH','AGGREGATED','PARTIAL','UNMATCHED','AMBIGUOUS','SOURCE_UNAVAILABLE'];
const reasons=['LATE_BILLING','SKU_UNMAPPED','UNIT_MISMATCH','RUNTIME_USAGE_UNKNOWN','BILLING_QUANTITY_UNKNOWN','CREDIT_ADJUSTMENT','SHARED_INFRASTRUCTURE','SOURCE_STALE','AMBIGUOUS_MATCH','ACCOUNTING_DEBT','PRICING_UNAVAILABLE','INPUT_DETAIL_UNAVAILABLE','SCOPE_UNPROVEN','FINANCIAL_IMBALANCE','NO_DATA','SOURCE_UNAVAILABLE','TEMPORAL_GRAIN_MISMATCH','QUANTITY_VARIANCE','MONETARY_VARIANCE','MAPPING_INVALID','BATCH_LIMIT','PUBLICATION_FAILED'];
const fresh=['FRESH','DELAYED','STALE','UNAVAILABLE','NOT_CONFIGURED'];
const fields=['input_tokens','output_tokens','candidate_tokens','cached_tokens','total_tokens','thought_tokens','tool_input_tokens','billable_characters'];
function obj(v:unknown):Record<string,unknown>{if(!v||typeof v!=='object'||Array.isArray(v))throw new ContractError();return v as Record<string,unknown>;}
function choice(v:unknown,values:string[]):string{if(typeof v!=='string'||!values.includes(v))throw new ContractError();return v;}
function decimal(v:unknown):string{if(typeof v!=='string'||v.length>100||!/^\-?\d+(?:\.\d+)?$/.test(v))throw new ContractError();return v;}
function optional(v:unknown):string|null{return v===null?null:decimal(v);}
function count(v:unknown):number{if(typeof v!=='number'||!Number.isSafeInteger(v)||v<0)throw new ContractError();return v;}
function currency(v:unknown):string{if(typeof v!=='string'||!/^[A-Z]{3}$/.test(v))throw new ContractError();return v;}
function list(v:unknown,max:number):unknown[]{if(!Array.isArray(v)||v.length>max)throw new ContractError();return v;}
export function decodeReconciliation(value:unknown):ReconciliationPage {
 const page=obj(value);if(page.schema_version!==1)throw new ContractError('incompatible');
 const items=list(page.items,31).map(raw=>{const r=obj(raw);
  if(typeof r.period!=='string'||!/^\d{4}-\d\d-\d\d$/.test(r.period)||!Number.isFinite(Date.parse(r.period)))throw new ContractError();
  if(r.generation!==null&&(typeof r.generation!=='string'||!/^[a-f0-9]{64}$/.test(r.generation)))throw new ContractError();
  const quantities=list(r.quantities,100).map(raw=>{const q=obj(raw);if(typeof q.model!=='string'||!/^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$/.test(q.model))throw new ContractError();
   return {model:q.model,component:choice(q.component,fields),unit:choice(q.unit,['tokens','characters','requests','seconds','bytes','byte-seconds']),runtime_quantity:optional(q.runtime_quantity),billing_quantity:optional(q.billing_quantity),difference:optional(q.difference),variance_pct:optional(q.variance_pct),runtime_unknown:count(q.runtime_unknown),billing_unknown:count(q.billing_unknown),confidence:choice(q.confidence,confidence),reason:q.reason===null?null:choice(q.reason,reasons)};});
  const amounts=list(r.amounts,100).map(raw=>{const a=obj(raw);const ratios=obj(a.ratios);const percentages=obj(a.percentages);
   const result={currency:currency(a.currency),percentages:{direct:optional(percentages.direct),shared:optional(percentages.shared),unallocated:optional(percentages.unallocated)},ratios:{direct:optional(ratios.direct),shared:optional(ratios.shared),unallocated:optional(ratios.unallocated)},catalog_reference:optional(a.catalog_reference),monetary_variance:optional(a.monetary_variance),eur_state:choice(a.eur_state,['NATIVE_EUR','EUR_UNAVAILABLE'])} as ReconciliationAmount;
   for(const key of ['gross','credit','adjustment','tax','rounding','net','absolute','direct','shared','unallocated'] as const)result[key]=decimal(a[key]);return result;});
  let coverage:Record<string,string|null>|null=null;
  if(r.coverage!==null){const c=obj(r.coverage);coverage={};for(const k of ['runtime_representation','ledger_completeness','mapping_rows','mapping_amount','billing_classification','monetary_accounting','allocation','runtime_quantity_known','billing_quantity_known','compatible_quantity','price_reference'])coverage[k]=optional(c[k]);}
  let runtime_quality:ReconciliationView['runtime_quality']=null;if(r.runtime_quality!==null){const q=obj(r.runtime_quality);runtime_quality={debt:count(q.debt),global_debt:count(q.global_debt),pending:count(q.pending),completeness:optional(q.completeness)};}
  const invoice_months=list(r.invoice_months,100).map(v=>{if(typeof v!=='string'||!/^\d{4}(0[1-9]|1[0-2])$/.test(v))throw new ContractError();return v;});
  return {period:r.period,invoice_months,runtime_freshness:choice(r.runtime_freshness,['FRESH','STALE','UNAVAILABLE','SNAPSHOT_ONLY']),current_accounting_debt:r.current_accounting_debt===null?null:count(r.current_accounting_debt),generation:r.generation as string|null,status:choice(r.status,statuses),display_state:choice(r.display_state,['Reconciled','Partial','Unresolved','Stale source','Source unavailable']),billing_freshness:choice(r.billing_freshness,fresh),pricing_freshness:choice(r.pricing_freshness,fresh),source_mode:choice(r.source_mode,['TEST_FIXTURE','BIGQUERY','NOT_CONFIGURED']),reasons:list(r.reasons,32).map(v=>choice(v,reasons)),quantity_count:count(r.quantity_count),quantity_detail_state:choice(r.quantity_detail_state,['COMPLETE','BOUNDED_SUMMARY']),quantities,amounts,coverage,runtime_quality};
 });
 if(page.next_cursor!==null&&(typeof page.next_cursor!=='string'||page.next_cursor.length>512||!/^[A-Za-z0-9_=-]+$/.test(page.next_cursor)))throw new ContractError();
 return {environment:choice(page.environment,['local','development','staging','production']),items,next_cursor:page.next_cursor as string|null};
}
export async function getReconciliation(signal?:AbortSignal,period?:string):Promise<ReconciliationPage>{
 if(period&&!/^\d{4}-\d\d-\d\d$/.test(period))throw new ContractError();
 try{return decodeReconciliation(await getJson('/api/observability/finops/reconciliation'+(period?'/'+period:''),signal));}
 catch(e){if(e instanceof ApiError)throw new ApiError('Reconciliation is unavailable.',e.status);if(e instanceof SyntaxError)throw new ContractError();throw e;}
}
