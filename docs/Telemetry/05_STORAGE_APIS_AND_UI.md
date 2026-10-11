# 05 — Storage, APIs and UI

## Storage strategy

Do not persist complete telemetry in ADK session state.

Use separate logical stores:

1. **OTel trace backend** — detailed spans/events.
2. **Operational summary DB** — SLOPANOC-specific fast lookup.
3. **Active-run state** — current status/stage with TTL.
4. **FinOps usage ledger** — durable, unsampled.
5. **Billing/FinOps warehouse** — normalized provider cost data.

## Suggested turn summary

`observability_turn`:

```text
run_id
trace_id
turn_id
session_id
environment
status
started_at
completed_at
duration_ms
domain
dialogue_act
primary_agent
authority_mode
error_code
error_stage
input_tokens
output_tokens
model_calls
tool_calls
estimated_cost
currency
created_at
```

Do not store raw high-volume spans in this table if a trace backend exists.

## Active run persistence

For production, `observability_run_status` is required as the durable cross-instance run-status table:

```text
run_id
trace_id
status
current_stage
current_agent
current_tool
current_dependency
stage_started_at
last_progress_at
heartbeat_at
updated_at
```

Use TTL cleanup.

## Required APIs

Admin/SRE:

```text
GET /api/observability/runs/{run_id}
GET /api/observability/runs/{run_id}/timeline
GET /api/observability/sessions/{session_id}/runs
GET /api/observability/active
GET /api/observability/health
```

FinOps APIs should be separate and RBAC-protected.

## Run summary response

Safe example:

```json
{
  "run_id": "...",
  "trace_id": "...",
  "status": "running",
  "elapsed_ms": 18440,
  "current_stage": "teams.read_messages",
  "current_agent": "incident_manager",
  "current_tool": "teams_read_messages",
  "last_progress_at": "...",
  "turn_plan": {
    "domain": "teams",
    "primary_agent": "incident_manager"
  },
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 410,
    "estimated_cost": 0.0043,
    "currency": "EUR"
  }
}
```

No prompt/Teams bodies.

## Timeline

Return events sorted by time with:
- stage,
- status,
- offset from root start,
- duration,
- agent/tool/dependency,
- safe metadata,
- error code.

## Normal-user frontend

Replace indefinite generic spinner with server-mapped progress labels:
- Understanding request...
- Selecting context...
- Reading Teams conversation...
- Searching governed knowledge...
- Analyzing evidence...
- Preparing response...

Labels are static mappings from safe stage enums, never model-generated.

Optionally show run ID/details.

## Admin Observability Console

### Active Runs
Columns:
- run,
- status,
- session,
- stage,
- agent,
- tool/dependency,
- elapsed,
- last progress,
- model,
- estimated cost.

### Timeline
Nested waterfall.

### Governance
- TurnPlan summary,
- source requirements,
- final authority,
- provenance class counts,
- command/approval/egress decision summaries.

### Performance
- total latency,
- critical path,
- slowest spans.

### Cost
- model calls,
- token usage,
- estimated cost by agent/span.

## Frontend telemetry

Capture safe events:
- `ui.prompt_submitted`,
- `ui.sse_connected`,
- `ui.first_event`,
- `ui.first_token`,
- `ui.message_completed_received`,
- `ui.message_rendered`,
- `ui.sse_disconnected`.

Correlate by run ID.

## RBAC

Normal users do not automatically receive admin traces. Authorization must be server-enforced.


## Production active-run persistence decision

Cloud SQL is the production source of truth for cross-instance active-run status.

Use process-local state only as a fast cache for the executing request.

Persist on:
- registration,
- material stage change,
- watchdog stall,
- terminal transition,
- bounded heartbeat interval.

Do not write every span event to the active-run table.

Terminal rows are retained for a short support TTL, then removed/archived according to policy.

## Exceptional archive storage and read boundary

The Operational Trace Store (Google Cloud Trace) retains supported full traces for
30 days. ExceptionalTraceArchive is a distinct sanitized full-trace object archive
with a 90-day primary horizon; summaries, timelines, logs and SLO aggregates are
not substitutes. See `16_PRODUCTION_TELEMETRY_BACKEND.md` for the OTLP envelope,
local sink, GCS bridge, completeness and retrieval contract.

Archive retrieval requires verified `technical_diagnostics` capability and explicit
server-enforced environment scope; canonical role ceilings remain unchanged. Do not
grant User, FinOps, Admin or Auditor archive access by implication. Queries/reconstruction
are bounded and read-only. No arbitrary object path, signed public URL, bulk export,
manual modification, deletion or reclassification endpoint/UI. Existing Diagnostics
may show independently confirmed Cloud Trace and archive states, or unavailable;
absence of retained trace is not execution failure. No large archive browser is required.
Archive expiry never deletes operational or financial derived records.

## Console route and read API composition — updated placement

Doc [25](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md) is authoritative for operational
UI placement: `/resources/observability` in the existing shell, with Overview, Traces,
Agents, Reliability, SLOs, FinOps, Alerts and Diagnostics. Settings contains only
effective configuration/policy and an Open Observability Console link. Earlier
Settings-first placement is superseded; normal chat progress remains unchanged.

Existing protected GET contracts are reused by an independent FastAPI read app.
The current router includes POST SSE receipts; split read composition and retain
that write exclusively on ANOC. Preserve capabilities/environment/cursors/privacy
and archive bounds. Planned independent read API/DB/origin settings are specified
in doc 25; no code/config is implemented by this architecture pass. Financial GETs
read published truth, never start calculations/jobs. Writer-local health must not
be relabeled as remote producer health; missing observations remain unavailable.

## Hybrid storage and read contracts

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) distinguishes authoritative SQL audit/accounting from derived projection
tables. Future outbox/projection migrations must follow c16f4a8b2d90; none is authored
or applied here. Reader GETs query authorized durable records/published financial
generations/projections/archives with bounded scans. No projector/FinOps jobs on GET,
ANOC polling fallback or duplicate business truth. Future DTOs need source/capture
classification/watermark/freshness/coverage/debt; UI work is deferred. UNKNOWN is not
zero. Current canonical active-run SQL persistence remains required.


## Local Phase-1 hybrid refinement — 2026-10-11

[Doc 26](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) defines the locally implemented
authoritative owner transaction → audit/outbox → bounded publisher → fake transport
→ projector → durable projection → independent GET reader. Additive revision
`d26a1b7c9e40` descends from `c16f4a8b2d90`; no shared schema was changed.
`GET /api/observability/hybrid/activity` exposes bounded safe lifecycle/freshness/debt
DTOs with server RBAC/environment scope; no worker runs on reads. M15 policy applies
to typed audit fields; projections do not duplicate M10–M14 financial truth.
Operational Telemetry Pub/Sub is **OPTIONAL / SCALE-TRIGGERED**, disabled, limited
to a future curated measured subset. Full unsampled OTLP replication is not Phase-1
minimum. Existing OTLP/cloud/archive pathways remain unchanged. Local SQLite/fake
validation is not PostgreSQL/Pub/Sub qualification. M16 remains INCOMPLETE; C41/C42
are not PASS; M17 remains UNSTARTED. No cloud mutation or deployment authorization.
