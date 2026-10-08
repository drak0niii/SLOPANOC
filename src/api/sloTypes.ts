export const SLO_STATES = ['HEALTHY','BREACHED','BURNING','INSUFFICIENT_DATA','DATA_SOURCE_UNAVAILABLE','STALE_DATA','DEFINED_NOT_EVALUATED'] as const;
export interface SLOView {
    schema_version: 1; slo_id: string; name: string;
    objective: number; objective_status: 'PROVISIONAL' | 'ARCHITECTURAL';
    window_seconds: number; window_start: string; window_end: string;
    threshold_seconds: number | null; eligible: number; good: number; bad: number; unknown: number; excluded: number;
    current_value: number | null; remaining_fraction: number | null; coverage: number | null;
    burn_tiers: ('fast' | 'sustained' | 'slow')[]; state: typeof SLO_STATES[number];
    evaluated_at: string; source_last_updated: string | null; freshness_seconds: number;
    source_status: 'AVAILABLE' | 'UNAVAILABLE' | 'STALE' | 'DATA_SOURCE_AVAILABLE_IN_M10';
}
export interface SLOPage { schema_version: 1; environment: string; evaluated_at: string; items: SLOView[]; }
