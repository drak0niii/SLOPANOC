import { getJson, ApiError } from './client';
import { ContractError } from '../components/shell/settings/observability/contract';
import { decodeSLOPage } from './sloContract';
import type { SLOView } from './sloTypes';
export const quantities = ['input_tokens','output_tokens','candidate_tokens','cached_tokens','total_tokens','thought_tokens','tool_input_tokens','billable_characters'] as const;
export type Quantity = typeof quantities[number];
export interface UsageGroup { dimension: string; attempts: number; captured_attempts: number; quantity_coverage: number|null; quantity_known: number; quantity_partial: number; quantities: Record<Quantity,{observed:number|null;known:number;unknown:number}> }
export interface UsagePage { environment: string; start: string; end: string; group: string; items: UsageGroup[]; next_cursor: string|null }
export interface LedgerHealth { environment:string;state:'HEALTHY'|'DEGRADED'|'STALE_DATA'|'DATA_SOURCE_UNAVAILABLE';pending:number;debt:number;conflicts:number;exhausted:number;admissions_failed:number;updated_at:string|null;last_observation:string|null;last_persistence:string|null;oldest_pending_at:string|null;oldest_debt_at:string|null;coverage_start:string|null;retention_months:number;retention_execution:'NOT_ACTIVATED' }
function object(v:unknown):Record<string,unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) throw new ContractError();return v as Record<string,unknown>; }
function integer(v:unknown,signed=false):number { if (typeof v!=='number' || !Number.isSafeInteger(v) || (!signed && v<0)) throw new ContractError();return v; }
function text(v:unknown):string { if (typeof v!=='string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(v)) throw new ContractError();return v; }
function date(v:unknown):string { if (typeof v!=='string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(v) || !Number.isFinite(Date.parse(v))) throw new ContractError();return v; }
function env(v:unknown):string { if (!['local','development','staging','production'].includes(String(v))) throw new ContractError();return String(v); }
function version(r:Record<string,unknown>) { if (r.schema_version!==1) throw new ContractError('incompatible'); }
export function decodeUsage(v:unknown):UsagePage {
    const r=object(v);version(r);
    if (!Array.isArray(r.items) || r.items.length>100) throw new ContractError();
    const items=r.items.map(v=>{const x=object(v);const raw=object(x.quantities);const projected={} as UsageGroup['quantities'];
        const attempts=integer(x.attempts),captured=integer(x.captured_attempts);
        if(captured>attempts || (x.quantity_coverage!=null && (typeof x.quantity_coverage!=='number'||!Number.isFinite(x.quantity_coverage)||x.quantity_coverage<0||x.quantity_coverage>1)))throw new ContractError();
        for (const q of quantities) { const f=object(raw[q]);projected[q]={observed:f.observed==null?null:integer(f.observed,true),known:integer(f.known),unknown:integer(f.unknown)};if(projected[q].known+projected[q].unknown!==captured)throw new ContractError(); }
        return {dimension:text(x.dimension),attempts,captured_attempts:captured,quantity_coverage:x.quantity_coverage as number|null,quantity_known:integer(x.quantity_known),quantity_partial:integer(x.quantity_partial),quantities:projected}; });
    const group=text(r.group);if(!['model','provider','agent','workload','operation','operation_type'].includes(group))throw new ContractError();
    if(r.next_cursor!=null && (typeof r.next_cursor!=='string'||r.next_cursor.length>512||!/^[A-Za-z0-9_=-]+$/.test(r.next_cursor)))throw new ContractError();
    return {environment:env(r.environment),start:date(r.start),end:date(r.end),group,items,next_cursor:r.next_cursor as string|null};
}
export function decodeLedgerHealth(v:unknown):LedgerHealth {
    const r=object(v);version(r);const state=text(r.state);
    if(!['HEALTHY','DEGRADED','STALE_DATA','DATA_SOURCE_UNAVAILABLE'].includes(state)||r.retention_execution!=='NOT_ACTIVATED')throw new ContractError();
    const result={environment:env(r.environment),state,pending:integer(r.pending),debt:integer(r.debt),conflicts:integer(r.conflicts),exhausted:integer(r.exhausted),admissions_failed:integer(r.admissions_failed),retention_months:integer(r.retention_months),retention_execution:'NOT_ACTIVATED'} as LedgerHealth;
    for(const k of ['updated_at','last_observation','last_persistence','oldest_pending_at','oldest_debt_at','coverage_start'] as const)result[k]=r[k]==null?null:date(r[k]);
    return result;
}
async function read<T>(path:string,decode:(v:unknown)=>T,signal?:AbortSignal) {
    try{return decode(await getJson<unknown>('/api/observability/finops/'+path,signal));}
    catch(e){if(e instanceof ApiError)throw new ApiError('Runtime usage is unavailable.',e.status);if(e instanceof SyntaxError)throw new ContractError();throw e;}
}
export const getRuntimeUsage=(group='model',signal?:AbortSignal,start?:string,end?:string,cursor?:string)=>{
    if(!['model','provider','agent','workload','operation','operation_type'].includes(group))throw new ContractError();
    const p=new URLSearchParams({group});if(start)p.set('start',start);if(end)p.set('end',end);if(cursor)p.set('cursor',cursor);
    return read('runtime-usage?'+p,decodeUsage,signal);
};
export const getLedgerHealth=(signal?:AbortSignal)=>read('ledger-health',decodeLedgerHealth,signal);
export const getLedgerCompleteness=(signal?:AbortSignal):Promise<SLOView>=>read('ledger-completeness',v=>{const r=object(v);return decodeSLOPage({schema_version:1,environment:'development',evaluated_at:r.evaluated_at,items:[r]}).items[0];},signal);

export interface FinancialSourceHealth {
    source:'DETAILED_BILLING'|'PRICING_EXPORT'|'FOCUS';state:'FRESH'|'DELAYED'|'STALE'|'UNAVAILABLE'|'NOT_CONFIGURED';
    reason:string;mode:'TEST_FIXTURE'|'BIGQUERY'|'NOT_CONFIGURED';as_of:string|null;last_extraction:string|null;
    coverage_start:string|null;coverage_end:string|null;generation:string|null;row_count:number;unmapped_rows:number;
    price_evidence_rows:number;pending_partitions:number;policy:'PROVISIONAL';delayed_seconds:number;stale_seconds:number;cadence_seconds:number;
}
export interface BillingAmounts {currency:string;category:string;gross:string;credits:string;adjustments:string;tax:string;rounding:string;pre_credit:string;net:string;rows:number;unmapped_rows:number;mapping_coverage:string|null;eur_net:string|null;eur_state:'NATIVE_EUR'|'EUR_REPORTING_UNAVAILABLE'}
export interface BillingSummary {environment:string;period_basis:'usage'|'invoice';start:string;end:string;invoice_month:string|null;amount_basis:'ACTUAL_BILLED_COST';tax_basis:'SOURCE_TAX_SEPARATE';source:FinancialSourceHealth;coverage_complete:boolean;amounts:BillingAmounts[]|null;next_cursor:string|null}
export interface PricingStatus {environment:string;amount_basis:'PRICING_EXPORT_CATALOG';source:FinancialSourceHealth;earliest_pricing_as_of:string|null;latest_pricing_as_of:string|null;historical_policy:'NO_EXTRAPOLATION'}
const decimalText=(v:unknown):string=>{if(typeof v!=='string'||v.length>80||! /^-?\d+(?:\.\d+)?$/.test(v))throw new ContractError();return v;};
const optionalDate=(v:unknown)=>v===null?null:date(v);
export function decodeSource(v:unknown):FinancialSourceHealth {
    const r=object(v);
    if(!['DETAILED_BILLING','PRICING_EXPORT','FOCUS'].includes(String(r.source))||!['FRESH','DELAYED','STALE','UNAVAILABLE','NOT_CONFIGURED'].includes(String(r.state))||!['TEST_FIXTURE','BIGQUERY','NOT_CONFIGURED'].includes(String(r.mode))||r.policy!=='PROVISIONAL')throw new ContractError();
    if(!['SOURCE_CURRENT','SOURCE_AGE','NO_DATA','SOURCE_UNAVAILABLE','SCHEMA_INCOMPATIBLE','COVERAGE_PARTIAL','NOT_CONFIGURED'].includes(String(r.reason)))throw new ContractError();
    if(r.generation!==null&&(typeof r.generation!=='string'||!/^[a-f0-9]{64}$/.test(r.generation)))throw new ContractError();
    const delayed=integer(r.delayed_seconds),stale=integer(r.stale_seconds),cadence=integer(r.cadence_seconds);
    if(delayed<1||stale<=delayed||stale>2592000||cadence<1||cadence>604800)throw new ContractError();
    return {source:r.source,state:r.state,reason:r.reason,mode:r.mode,policy:'PROVISIONAL',generation:r.generation,delayed_seconds:delayed,stale_seconds:stale,cadence_seconds:cadence,
        as_of:optionalDate(r.as_of),last_extraction:optionalDate(r.last_extraction),coverage_start:optionalDate(r.coverage_start),coverage_end:optionalDate(r.coverage_end),
        row_count:integer(r.row_count),unmapped_rows:integer(r.unmapped_rows),price_evidence_rows:integer(r.price_evidence_rows),pending_partitions:integer(r.pending_partitions)} as FinancialSourceHealth;
}
export function decodeBillingHealth(v:unknown):{environment:string;sources:FinancialSourceHealth[]} {
    const r=object(v);version(r);if(!Array.isArray(r.sources)||r.sources.length!==3)throw new ContractError();
    const sources=r.sources.map(decodeSource);if(new Set(sources.map(s=>s.source)).size!==3)throw new ContractError();
    return {environment:env(r.environment),sources};
}
export function decodeBillingSummary(v:unknown):BillingSummary {
    const r=object(v);version(r);
    if(!['usage','invoice'].includes(String(r.period_basis))||r.amount_basis!=='ACTUAL_BILLED_COST'||r.tax_basis!=='SOURCE_TAX_SEPARATE'||typeof r.coverage_complete!=='boolean')throw new ContractError();
    const day=(v:unknown)=>{if(typeof v!=='string'||!/^\d{4}-\d\d-\d\d$/.test(v)||!Number.isFinite(Date.parse(v)))throw new ContractError();return v;};
    if(r.invoice_month!==null&&(typeof r.invoice_month!=='string'||!/^\d{4}(0[1-9]|1[0-2])$/.test(r.invoice_month)))throw new ContractError();
    if(r.amounts!==null&&(!Array.isArray(r.amounts)||r.amounts.length>100))throw new ContractError();
    const amounts=r.amounts===null?null:(r.amounts as unknown[]).map(v=>{
        const a=object(v);if(typeof a.currency!=='string'||!/^[A-Z]{3}$/.test(a.currency)||!['MODEL_AI','CLOUD_RUN','CLOUD_SQL','BIGQUERY','GCS','MONITORING_LOGGING','NETWORK','OTHER','UNMAPPED'].includes(String(a.category))||!['NATIVE_EUR','EUR_REPORTING_UNAVAILABLE'].includes(String(a.eur_state)))throw new ContractError();
        const projected={currency:a.currency,category:a.category,rows:integer(a.rows),unmapped_rows:integer(a.unmapped_rows),mapping_coverage:a.mapping_coverage===null?null:decimalText(a.mapping_coverage),eur_net:a.eur_net===null?null:decimalText(a.eur_net),eur_state:a.eur_state} as BillingAmounts;
        for(const k of ['gross','credits','adjustments','tax','rounding','pre_credit','net'] as const)projected[k]=decimalText(a[k]);
        if((a.currency==='EUR')!==(a.eur_state==='NATIVE_EUR')||((a.eur_state==='NATIVE_EUR')!== (a.eur_net!==null)))throw new ContractError();
        return projected;
    });
    if(r.next_cursor!==null&&(typeof r.next_cursor!=='string'||r.next_cursor.length>512||!/^[A-Za-z0-9_=-]+$/.test(r.next_cursor)))throw new ContractError();
    return {environment:env(r.environment),period_basis:r.period_basis as BillingSummary['period_basis'],start:day(r.start),end:day(r.end),invoice_month:r.invoice_month as string|null,amount_basis:'ACTUAL_BILLED_COST',tax_basis:'SOURCE_TAX_SEPARATE',source:decodeSource(r.source),coverage_complete:r.coverage_complete,amounts,next_cursor:r.next_cursor as string|null};
}
export function decodePricingStatus(v:unknown):PricingStatus {
    const r=object(v);version(r);if(r.amount_basis!=='PRICING_EXPORT_CATALOG'||r.historical_policy!=='NO_EXTRAPOLATION')throw new ContractError();
    return {environment:env(r.environment),amount_basis:'PRICING_EXPORT_CATALOG',source:decodeSource(r.source),earliest_pricing_as_of:optionalDate(r.earliest_pricing_as_of),latest_pricing_as_of:optionalDate(r.latest_pricing_as_of),historical_policy:'NO_EXTRAPOLATION'};
}
export const getBillingHealth=(signal?:AbortSignal)=>read('billing-health',decodeBillingHealth,signal);
export const getPricingStatus=(signal?:AbortSignal)=>read('pricing-status',decodePricingStatus,signal);
export const getBillingSummary=(signal?:AbortSignal,basis:'usage'|'invoice'='usage',invoice?:string)=>{
    const p=new URLSearchParams({period_basis:basis});if(invoice)p.set('invoice_month',invoice);
    return read('billing-summary?'+p,decodeBillingSummary,signal);
};
