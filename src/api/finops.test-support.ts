// Synthetic safe DTOs for isolated tests only.
export const at='2026-10-08T12:00:00Z';
export const group={dimension:'gemini-2.5-flash',attempts:100,captured_attempts:100,quantity_known:95,quantity_partial:0,quantity_coverage:.95,quantities:Object.fromEntries(['input_tokens','output_tokens','candidate_tokens','cached_tokens','total_tokens','thought_tokens','tool_input_tokens','billable_characters'].map(q=>[q,{observed:null as number|null,known:0,unknown:100}]))};
export const usage={schema_version:1,environment:'development',start:at,end:at,group:'model',items:[group],next_cursor:null};
export const health={schema_version:1,environment:'development',state:'DEGRADED',pending:1,debt:1,conflicts:0,exhausted:0,admissions_failed:0,updated_at:at,last_observation:at,last_persistence:null,oldest_pending_at:at,oldest_debt_at:at,coverage_start:at,retention_months:24,retention_execution:'NOT_ACTIVATED'};
