import { it, expect } from "vitest";
import { decodeRun, decodeRuns, decodeTimeline, decodeConfig, decodeHealth } from './contract';
import { run, technical, at, event, health, config } from './fixtures.test-support';
it('projects explicit safe fields and drops arbitrary content everywhere', () => {
    const sentinel = 'SECRET_PROMPT_TEAMS_COMMAND_SQL_TOKEN_URL';
    const r = decodeRun({ ...run(), prompt: sentinel, technical: { ...technical, sql: sentinel }, dependencies: [{ ...run().dependencies[0], url: sentinel }] });
    expect(JSON.stringify(r)).not.toContain(sentinel);
    expect(decodeTimeline({ items: [{ ...event, body: sentinel }], truncated_count: 1 })).toEqual({ items: [event], next_cursor: undefined, truncated_count: 1 });
    expect(JSON.stringify(decodeHealth({ ...health, secret: sentinel }))).not.toContain(sentinel);
    expect(decodeConfig([...config, { setting: 'unknown_secret', value: sentinel }])).toEqual(config);
});
it('handles unknown enums with fixed labels instead of echoing values', () => {
    const r = decodeRun({ ...run(), status: 'SECRET', current_stage: 'SECRET', current_tool: 'SECRET', error_code: 'SECRET', dependencies: [{ ...run().dependencies[0], dependency: 'SECRET' }] });
    expect(r.status).toBe('Unknown');
    expect(r.current_stage).toBe('Unknown');
    expect(r.current_tool).toBe('Unknown');
    expect(r.error_code).toBe('Unknown');
    expect(r.dependencies[0].dependency).toBe('Unknown');
    expect(JSON.stringify(r)).not.toContain('SECRET');
    expect(decodeHealth({ ...health, persistence: 'SECRET' }).persistence).toBe('Unknown');
});
it('validates version where supplied, including config and technical metadata', () => {
    expect(decodeRun({ ...run(), technical }).technical?.telemetry_schema_version).toBe(1);
    expect(() => decodeRun({ ...run(), technical: { ...technical, telemetry_schema_version: 2 } })).toThrow();
    expect(() => decodeConfig([{ ...config[5], value: 2 }])).toThrow();
    expect(() => decodeRuns({ items: [], as_of: at, telemetry_schema_version: 2 })).toThrow();
});
it.each([{ elapsed_ms: NaN }, { started_at: '2026-10-08' }, { status: 12 }, { outcome_unknown: 'true' }, { dependencies: Array(129).fill({}) }, { run_id: 'SQL SELECT' }, { cleanup_status: null }])('rejects malformed important fields %j', extra => expect(() => decodeRun({ ...run(), ...extra })).toThrow());
it('bounds pagination and ordering and rejects editable config', () => {
    expect(() => decodeRuns({ items: Array(101).fill(run()), as_of: at })).toThrow();
    expect(() => decodeRuns({ items: [], as_of: at, next_cursor: 'https://secret' })).toThrow();
    expect(() => decodeTimeline({ items: [event, event], truncated_count: 0 })).toThrow();
    expect(() => decodeConfig([{ ...config[0], read_only: false }])).toThrow();
});
