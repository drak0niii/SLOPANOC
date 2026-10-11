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

## Exceptional full-trace archive — approved M15 planning adaptation

B01 is RESOLVED BY ARCHITECTURE ADAPTATION, not waived. Google Cloud Trace remains
the Operational Trace Store with 30-day full-span retention. ExceptionalTraceArchive
preserves sanitized full exceptional traces for a 90-day primary archive horizon:
days 0–30 in both stores, days 31–90 in the archive only. Operational summaries,
logs, metrics and financial records are separate and cannot satisfy full-trace retention.

After producer allowlisting, environment checks and Collector redaction, eligible
exceptional traces flow through a stable OTLP exporter to an Exceptional Trace
Archive Bridge and a dedicated environment-isolated GCS archive bucket. Cloud Trace
export stays independently available. This observability component is never evidence,
command authority, business persistence or a second application event store.
The transport-independent contract, safe OTLP schema and local sink are in
`16_PRODUCTION_TELEMETRY_BACKEND.md`. No cloud mutation is required for M15 local
validation; deployment/IAM/GCS lifecycle/live validation remain separately approved.

## Independent operational read runtime

[25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md)
extends this topology: ANOC producers → durable/published observability data →
independent protected GET-only read API → `/resources/observability` within the
existing product shell. Console polling must not consume ANOC workers in production.
Independent compute/pools/health and bounded shared-data access are required;
reader failure never becomes a synchronous business dependency. Telemetry export,
financial workers and governance ownership are preserved. No deployment is implied.

## Hybrid topology precedence

The overview above describes logical capabilities, not synchronous ANOC coupling.
The authoritative service/dataflow Mermaid is in [26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md): critical records/outbox
commit atomically in SQL; operational OTLP fans out after Collector privacy to
Google destinations, exceptional archive and optional telemetry Pub/Sub. Separate
publisher/projector, read API and background FinOps jobs isolate work. SQL canonical
active-run/terminal state remains authority; projected operational views disclose
source version/freshness/coverage. GCP Cloud Audit Logs and pgAudit complement
application audit; none changes conversation/business governance.


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

## Execution journal status — 2026-10-11

Execution journal is authoritative only for recorded lifecycle facts; business owners retain authority. A/B/C/D boundaries and controlled egress are implemented locally; full auditability NO. See [implementation](27_EXECUTION_AUDIT_JOURNAL_AND_CONTROLLED_EGRESS.md), [coverage](AUDIT_COVERAGE_MATRIX.md) and [remaining gaps](AUDIT_OWNERSHIP_GAP_DESIGN.md).
