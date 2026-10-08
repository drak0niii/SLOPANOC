import { vi } from "vitest";
import type { RunView, HealthView, EffectiveConfiguration, DiagnosticEvent } from '../../../../api/observabilityTypes';
export const runId = '11111111-1111-4111-8111-111111111111';
export const at = '2026-10-08T10:00:00Z';
export function run(overrides: Partial<RunView> = {}): RunView { return { run_id: runId, environment: 'development', status: 'STALLED', classification: 'STALLED', outcome_unknown: false, source: 'durable', classification_as_of: at, started_at: at, current_stage: 'teams.read_messages.started', current_agent: 'incident_manager', current_tool: 'teams_get_messages', dependencies: [{ dependency: 'power_automate_gateway', operation: 'teams.getMessages', started_at: at }], elapsed_ms: 42000, last_progress_at: at, heartbeat_at: at, progress_age_ms: 31000, total_deadline_at: '2026-10-08T10:03:00Z', error_code: 'TURN_STALLED', cleanup_status: 'pending', delivery_status: 'pending', timeline_truncated_count: 0, ...overrides }; }
export const event: DiagnosticEvent = { event_seq: 1, state_version: 1, event_type: 'stalled', timestamp: at, elapsed_ms: 42000, stage: 'run.stalled', status: 'STALLED', agent: 'incident_manager', tool: 'teams_get_messages', dependency: 'power_automate_gateway', error_code: 'TURN_STALLED' };
export const health: HealthView = { persistence: 'healthy', writer_scope: 'process_local', durable_scope: 'authorized_environments', as_of: at, pending: 0, admitted: 1, terminal_pending: 0, exporter_state: 'unknown', collector_configured: true, accepted: 1, coalesced: 0, attempts: 1, persisted: 1, failed: 0, retries: 0, rejected: 0, terminal_unpersisted: 0, shutdown_incomplete: 0, invalid: 0 };
export const config: EffectiveConfiguration[] = [['turn_timeout_seconds', 180], ['projection_terminal_hours', 24], ['projection_success_days', 30], ['projection_exceptional_days', 90], ['projection_checkpoint_seconds', 15], ['telemetry_schema_version', 1]].map(([setting, value]) => ({ setting: String(setting), value: Number(value), owner: 'environment_policy', read_only: true, config_version: '1234567890abcdef' }));
export const technical = { trace_id: 'a'.repeat(32), producer_instance_id: runId, state_version: 1, release_id: 'release-1', revision: 'revision-1', telemetry_schema_version: 1 as const };
export function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }); }
export function mockApi(options: {
    configDenied?: boolean;
    healthFailed?: boolean;
    empty?: boolean;
} = {}) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const path = String(input).split('?')[0];
        if (path === '/api/sessions')
            return response({ sessions: [] });
        if (path.endsWith('/config'))
            return response(config, options.configDenied ? 403 : 200);
        if (path.endsWith('/health'))
            return response(health, options.healthFailed ? 503 : 200);
        if (path.endsWith('/timeline'))
            return response({ items: options.empty ? [] : [event], truncated_count: 2 });
        if (path.endsWith('/active') || path.endsWith('/runs'))
            return response({ items: options.empty ? [] : [run()], as_of: at });
        return response(run({ technical }));
    });
}
