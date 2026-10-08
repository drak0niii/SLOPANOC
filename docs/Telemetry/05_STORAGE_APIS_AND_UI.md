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
