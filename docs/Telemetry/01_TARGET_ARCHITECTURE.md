# 01 — Target Architecture

## Overview

The target is a unified Operational Intelligence platform:

```text
                               SLOPANOC
                                  |
             +--------------------+--------------------+
             |                                         |
       TELEMETRY PLANE                           COST PLANE
             |                                         |
       OpenTelemetry                              Usage Ledger
       /     |      \                         /       |       \
   Traces   Logs   Metrics                 Tokens   Runtime   Storage/API
       \      |      /                         \       |       /
        \-----+-----/                           Cost Engine
              |                                      |
        Observability Store                    Allocation/Reconciliation
              |                                      |
       SRE / Operations                         Unit Economics
              \                                      /
               \---------------+--------------------/
                               |
                         CONTROL PLANE
                    /             |             \
                 SRE UI         FinOps UI      Governance
```

## Root turn

`backend/api/chat_service.py` is the canonical turn pipeline and should own the root trace.

```text
UI prompt
  ↓
HTTP/SSE request
  ↓
ROOT TURN SPAN
  ├─ session load
  ├─ attachments
  ├─ TurnPlan
  ├─ thread/pending interaction
  ├─ Context Broker
  ├─ agents
  ├─ model calls
  ├─ tools
  ├─ dependencies
  ├─ final authority
  ├─ provenance
  ├─ command egress
  ├─ persistence
  └─ SSE completion
       ↓
browser receive/render
```

Synchronous and SSE consumers must observe the same execution.

## Span topology

Example:

```text
slopanoc.turn
├── session.load
├── planning
│   └── gen_ai.request
├── thread.resolve
├── context.select
├── agent.team_manager
│   ├── gen_ai.request
│   └── tool.incident_manager
│       └── agent.incident_manager
│           ├── gen_ai.request
│           └── tool.teams_read
│               └── http.microsoft_graph
├── authority.select
├── provenance.build
├── command_egress
├── persistence.final_answer
└── sse.complete
```

Specialist internals must be child spans even when nested ADK AgentTool execution is not visible in the outer event stream.

## Correlation IDs

Mandatory:
- `trace_id` — telemetry identity;
- `span_id` — span identity;
- `run_id` — trusted SLOPANOC run identity;
- `turn_id` — user-turn identity;
- `session_id` — conversation identity.

Conditional:
- `thread_id`,
- `case_id`,
- `fault_id`,
- `approval_id`,
- `execution_id`,
- `knowledge_id`,
- `source_id`.

Rules:
- trusted server boundaries generate/validate correlation IDs;
- user-provided identifiers are not trusted automatically;
- high-cardinality IDs are allowed in traces/ledgers, not metric labels.

## Active-run architecture

Maintain a live operational state for each active run:

```text
run_id
trace_id
turn_id
session_id
status
current_stage
current_agent
current_tool
current_dependency
started_at
stage_started_at
last_progress_at
```

For single-instance dev this may be memory-backed. In production, Cloud SQL is the durable cross-instance source of truth for active-run status; process-local memory is only an execution-local cache. Support must never depend on request stickiness.

## Storage layers

1. **Active-run registry** — current stage/stall diagnosis.
2. **Trace store** — detailed historical execution.
3. **Operational summary DB** — required Cloud SQL summaries for cross-instance run lookup, support and SLOPANOC-specific operational status.
4. **Usage/cost ledger** — durable FinOps truth, unsampled.
5. **Cloud/provider billing store** — normalized financial truth.

Do not stuff detailed trace history into ADK session state.

## Frontend

Normal user:
- safe progress labels,
- optional run ID/details,
- terminal failure/retry state.

Admin/SRE:
- active runs,
- waterfall,
- agent/tool/dependency tree,
- errors/retries/timeouts,
- governance summary,
- token/cost summary.

FinOps:
- spend,
- forecast,
- unit economics,
- allocation,
- anomalies,
- no raw conversation content.

## Existing traces

### `backend/tools/knowledge/diagnostic_trace.py`
Keep it as a domain diagnostic producer. Its outputs remain observability-only and never evidence.

### `backend/api/perf_timing.py`
Audit and converge useful timings into spans/metrics. Avoid two independent timing truths.

### `backend/api/run_trace.py`
Audit and adapt into the canonical active-run/trace design rather than maintaining a second registry.

## Overhead budget

Initial engineering targets:
- instrumentation CPU overhead < 3% typical turn;
- synchronous instrumentation overhead < 10 ms excluding provider/export network;
- exporters batch/asynchronously send;
- trace export failure does not block final answer;
- cost-ledger writes are bounded and coverage failures are observable.


## Production telemetry backend

The production topology is fixed:

```text
Application -> OTLP -> OpenTelemetry Collector
                         |-> Cloud Trace
                         |-> Cloud Monitoring
                         `-> Cloud Logging
```

BigQuery is used for FinOps billing/reconciliation analytics.

See `16_PRODUCTION_TELEMETRY_BACKEND.md`.

## Production active-run state

Cloud SQL is the cross-instance source of truth for active run status.

Process-local memory may mirror the currently executing run for low-latency updates, but support/admin lookup must not depend on instance affinity.

Persist:
- run_id,
- trace_id,
- turn_id,
- session_id,
- status,
- current_stage,
- current_agent/tool/dependency,
- started_at,
- stage_started_at,
- last_progress_at,
- heartbeat_at,
- terminal_at.

Writes occur on material stage transitions, watchdog events, terminal transition and bounded heartbeat interval—not every micro-event.
