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
