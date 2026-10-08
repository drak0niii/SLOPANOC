# SLOPANOC OBSERVABILITY + SRE + FINOPS BUILD PACK — ALL IN ONE


---

# FILE: README.md

# SLOPANOC Production Observability + SRE + FinOps Build Pack

## Purpose

This `.docs/` directory is the authoritative implementation contract for building a production-grade **Observability + Reliability + FinOps Control Plane** for SLOPANOC.

The goal is not "more logging". The finished system must answer, for every prompt and for the platform as a whole:

1. What is happening right now?
2. Where is a turn blocked, slow, failed, waiting, retrying or stalled?
3. Which agent, model, tool or dependency is responsible?
4. What governed decisions were made and which server-owned boundary made them?
5. What did the turn cost, what generated that cost, and how does estimated cost reconcile to provider billing?
6. Is the service meeting SLOs and consuming error budget?
7. Can support reconstruct a turn end-to-end by `run_id` without reading raw server logs?
8. Can FinOps allocate and optimize spend without gaining access to sensitive conversation content?

## Read order

1. `00_MASTER_BUILD_CONTRACT.md`
2. `01_TARGET_ARCHITECTURE.md`
3. `02_TELEMETRY_DATA_CONTRACT.md`
4. `03_INSTRUMENTATION_AND_RUNTIME.md`
5. `04_RELIABILITY_TIMEOUTS_ERRORS.md`
6. `05_STORAGE_APIS_AND_UI.md`
7. `06_SRE_SLOS_METRICS_ALERTS.md`
8. `07_FINOPS_ARCHITECTURE.md`
9. `08_FINOPS_DATA_MODEL_AND_UNIT_ECONOMICS.md`
10. `09_SECURITY_PRIVACY_RETENTION_SAMPLING.md`
11. `10_DASHBOARDS_RUNBOOKS_OPERATING_MODEL.md`
12. `11_SLOPANOC_INTEGRATION_MAP.md`
13. `12_IMPLEMENTATION_ROADMAP.md`
14. `13_TEST_VALIDATION_ACCEPTANCE.md`
15. `14_CODEX_EXECUTION_GUIDE.md`
16. `15_DEFINITION_OF_DONE.md`
17. `16_PRODUCTION_TELEMETRY_BACKEND.md`
18. `17_INFRASTRUCTURE_OBSERVABILITY.md`
19. `18_STRUCTURED_LOGGING_STANDARD.md`
20. `19_AI_QUALITY_OUTCOMES.md`
21. `20_FINOPS_ACCOUNTING_DURABILITY.md`
22. `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md`
23. `22_RELEASE_CONFIG_CORRELATION_CI.md`
24. `23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md`
25. `24_CONTINUOUS_PROFILING.md`

`ALL_IN_ONE.md` is generated from all files for agents that prefer a single context document.

## Existing SLOPANOC assets to preserve and extend

Always inspect the **current working tree** before coding. Local uncommitted conversational-orchestration changes may be newer than the remote branch.

Known integration points include:

- `backend/api/chat_service.py` — canonical turn pipeline and SSE execution.
- `backend/api/app.py` — FastAPI endpoints/lifespan.
- `backend/api/perf_timing.py` — existing timing instrumentation.
- `backend/api/run_trace.py` — existing run-scoped tracing.
- `backend/tools/knowledge/diagnostic_trace.py` — deep knowledge/governance diagnostics.
- `backend/context/assembly.py` — Context Engineering Broker.
- `backend/conversation/*` — TurnPlan, threads, authority, provenance and pending interaction where present.
- `backend/agents/*` — Team Manager and specialists.
- `backend/tools/*` — knowledge, Teams and other tools.
- `src/` — React/Vite frontend and SSE handling.

These are load-bearing. Do not replace them casually.

## Non-negotiable decisions

1. **OpenTelemetry is the telemetry foundation.** Use standard trace/metric/log concepts and extend with `slopanoc.*` attributes only where needed.
2. **`chat_service` owns the root turn trace.** Do not create a parallel turn executor.
3. **Existing diagnostic traces become specialized telemetry producers, not a second observability platform.**
4. **No hidden chain-of-thought is logged or persisted.** Record structured decisions, safe identifiers, timings and bounded metadata only.
5. **FinOps is first-class.** Runtime usage/cost accounting is part of the platform, not a later dashboard.
6. **Usage/cost ledger coverage is 100%.** Traces may eventually be sampled; cost usage may not.
7. **Every external wait has a timeout and a span.** No indefinite `Processing your request...` state.
8. **High-cardinality identifiers belong in traces/ledgers, not metric labels.**
9. **Observability must observe itself.** Track exporter failures, dropped telemetry, overhead and observability cost.
10. **Telemetry never becomes evidence or authority.** It can explain execution but cannot grant command authority or select governed evidence.
11. **Production telemetry topology is fixed.** SLOPANOC exports OTLP to an OpenTelemetry Collector; the Collector exports traces to Cloud Trace, metrics to Cloud Monitoring and logs to Cloud Logging.
12. **Production active-run truth is durable.** Cloud SQL stores cross-instance run status; process-local memory is only a fast execution-local cache.
13. **Production logs are structured JSON.**
14. **Infrastructure observability includes Cloud Run, Cloud SQL, Gemini/Vertex, Graph, BigQuery, GCS, knowledge and Collector health.**
15. **Primary FinOps reporting currency is EUR while retaining native provider currency.**
16. **Detailed Billing + Pricing exports are primary accounting inputs; FOCUS is supplemental.**
17. **Observability infrastructure is managed as code with Terraform.**
18. **Browser/backend correlation uses W3C Trace Context.**
19. **CI enforces telemetry vocabulary, redaction, cardinality, span lifecycle and cost-ledger coverage.**
20. **Continuous profiling is part of the production build.**


## Quality target

A successful turn must be reconstructable as a timeline similar to:

```text
RUN 7bd...
00.000  UI prompt submitted
00.018  API received
00.032  Session loaded
00.044  Turn planning started
01.313  Planning model completed
01.320  TurnPlan accepted
01.327  Thread resolved
01.349  Context selected
01.358  Incident Manager started
01.361  Teams tool started
05.822  Microsoft Graph completed
05.839  Incident Manager completed
05.850  Synthesis started
07.013  Synthesis completed
07.019  Final authority selected
07.023  Provenance completed
07.026  Command egress completed
07.051  Persistence completed
07.055  message.completed emitted
07.069  Browser received completion
07.078  UI rendered
TOTAL 7.078 sec
```

A stuck turn must identify the blocking point:

```text
status: STALLED
current_stage: teams.graph.read_messages
elapsed: 42.8 sec
last_progress: 31.6 sec ago
agent: incident_manager
dependency: microsoft_graph
run_id: 7bd...
```

FinOps must be able to show per-turn and aggregate cost, including estimated and reconciled values.


---

# FILE: 00_MASTER_BUILD_CONTRACT.md

# 00 — Master Build Contract

## Mission

Build a production-grade **Observability + Reliability + FinOps Control Plane** for SLOPANOC.

The finished platform must include:

- distributed traces,
- correlated structured logs,
- metrics,
- active-run visibility,
- model/agent/tool/dependency instrumentation,
- timeouts, watchdogs and structured failures,
- admin observability APIs,
- frontend progress and trace views,
- SRE SLOs and alerts,
- 100% AI usage ledger,
- runtime cost estimation,
- provider billing ingestion and reconciliation,
- direct/shared/unallocated cost allocation,
- unit economics,
- budgets, forecasting and anomaly detection,
- RBAC, redaction, retention and sampling,
- dashboards and runbooks.

This work must not change SLOPANOC's application governance semantics.

## Architectural planes

### Telemetry plane
Records what happened:
- traces,
- spans,
- events,
- logs,
- metrics,
- current stage,
- timing,
- bounded safe metadata.

### Reliability plane
Controls approved runtime protection:
- deadlines/timeouts,
- safe retries,
- cancellation,
- watchdog/stall detection,
- circuit breaking where justified,
- structured error taxonomy.

### FinOps plane
Records and reconciles:
- model tokens,
- request counts,
- retried usage,
- infrastructure/service usage,
- estimated cost,
- billed cost,
- allocation,
- unit economics,
- budgets/forecast.

### Governance plane
Existing application governance remains authoritative:
- TurnPlan,
- Context Broker,
- knowledge selection,
- applicability,
- ProcedureAction,
- Command Authority,
- approval,
- command egress,
- TurnAuthority,
- claim provenance.

Telemetry may mirror these decisions but never replace or influence them.

## Fixed production decisions

These decisions are approved and must not be re-opened by the coding agent:

- OTel Collector -> Cloud Trace / Cloud Monitoring / Cloud Logging.
- Cloud SQL is the durable cross-instance active-run status store; in-memory state is a cache.
- Production logs are structured JSON.
- Infrastructure telemetry covers Cloud Run, Cloud SQL, Gemini/Vertex, Graph, BigQuery, GCS, knowledge, network dependencies and the Collector.
- Initial SLO targets are defined in `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md` and later recalibrated from baseline evidence.
- Trace retention: normal successful traces 30 days; errors/timeouts/stalled/governed operational traces 90 days.
- Aggregate metrics retention target: 13 months.
- FinOps ledger/billing analytical retention target: 24 months, subject to enterprise policy.
- Sampling starts at 100%; later healthy general-chat traces may be reduced to 10–20%, while failed/timeout/stalled/safety/governed turns remain 100%.
- FinOps ledger is immutable/idempotent/accounting-grade with durable recovery.
- Primary billing source: Detailed Billing Export + Pricing; FOCUS is supplemental.
- Reporting currency: EUR, retaining native provider currency.
- Quality/value telemetry covers governed-answer rate, operator acceptance, FTC where measurable, incident resolution, escalation, troubleshooting completion, action success and automation-artifact success.
- Every trace correlates Git SHA, release/revision, model, prompt/agent/tool/schema/config versions.
- Browser/backend propagation uses W3C Trace Context.
- Terraform is the observability-as-code standard.
- CI/CD contains observability contract gates.
- Continuous profiling is included.
- RBAC roles: User, Operator, Developer/SRE, FinOps, Admin, Auditor; FinOps cannot read conversation/Teams content by default.
- Observability cost is budgeted and alerted as part of total SLOPANOC cost.

## Production invariants

### Trace invariants
- one user turn -> one canonical root trace;
- stable `trace_id`, `run_id`, `turn_id`, `session_id`;
- child work uses parent/child spans;
- every terminal turn ends exactly once as `COMPLETED`, `FAILED`, `TIMEOUT` or `CANCELLED`;
- `STALLED` is a watchdog state/event and may later transition to a terminal state;
- all spans are closed in success and failure paths.

### Safety invariants
Never store by default:
- passwords,
- access tokens,
- API keys,
- authorization headers,
- full Teams message bodies,
- full knowledge document text,
- attachment contents,
- full prompts/responses,
- hidden chain-of-thought.

### FinOps invariants
- every billable model call creates a durable usage record;
- every turn can roll up child usage;
- `estimated_cost` and `billed_cost` are separate;
- pricing is versioned;
- cost estimates are reproducible;
- usage/cost ledgers are never trace-sampled;
- shared allocation stores method, driver, version and confidence;
- unallocated cost is visible.

### SRE invariants
- all blocking external calls have configured deadlines;
- no request remains indefinitely ambiguous;
- active-run current stage is queryable;
- observability exporter failure is itself measurable;
- safety violations such as unauthorized command exposure have zero tolerance.

## Target package

Prefer a modular package resembling:

```text
backend/observability/
├── __init__.py
├── config.py
├── schemas.py
├── attributes.py
├── stages.py
├── tracing.py
├── metrics.py
├── logging.py
├── instrumentation.py
├── model_instrumentation.py
├── tool_instrumentation.py
├── dependency_instrumentation.py
├── watchdog.py
├── errors.py
├── redaction.py
├── persistence.py
├── repositories.py
├── api_models.py
├── finops/
│   ├── usage_ledger.py
│   ├── pricing.py
│   ├── estimator.py
│   ├── reconciliation.py
│   ├── allocation.py
│   ├── unit_economics.py
│   ├── budgets.py
│   └── forecasting.py
└── exporters/
    ├── otlp.py
    └── local.py
```

Adapt names to repository conventions, but do not collapse all responsibilities into a monolith.

## Configuration

Add explicit environment-driven settings, including at minimum:

```text
SLOPANOC_OBSERVABILITY_ENABLED
SLOPANOC_OTEL_ENABLED
SLOPANOC_OTEL_EXPORTER_OTLP_ENDPOINT
SLOPANOC_OTEL_SERVICE_NAME
SLOPANOC_OTEL_ENVIRONMENT
SLOPANOC_TRACE_SAMPLE_RATE
SLOPANOC_TRACE_ERRORS_ALWAYS
SLOPANOC_TRACE_GOVERNED_TURNS_ALWAYS

SLOPANOC_MODEL_TIMEOUT_SECONDS
SLOPANOC_GRAPH_TIMEOUT_SECONDS
SLOPANOC_KNOWLEDGE_TIMEOUT_SECONDS
SLOPANOC_DATABASE_TIMEOUT_SECONDS
SLOPANOC_STORAGE_TIMEOUT_SECONDS
SLOPANOC_WATCHDOG_STALL_SECONDS
SLOPANOC_HEARTBEAT_SECONDS

SLOPANOC_FINOPS_ENABLED
SLOPANOC_FINOPS_CURRENCY
SLOPANOC_FINOPS_PRICE_SOURCE
SLOPANOC_FINOPS_BILLING_PROJECT
SLOPANOC_FINOPS_BILLING_DATASET
```

Critical policy must not be hidden in magic constants.

## Delivery rule

Implement milestone by milestone. Each milestone must leave the repository importable, migration-safe and compatible with existing safety controls.


---

# FILE: 01_TARGET_ARCHITECTURE.md

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


---

# FILE: 02_TELEMETRY_DATA_CONTRACT.md

# 02 — Telemetry Data Contract

## Purpose

Define one canonical vocabulary so traces, logs, metrics, UI and FinOps describe the same runtime.

No production code may invent arbitrary stage/status/error names without updating this contract.

## Stage vocabulary

### Request/session
```text
request.received
request.validated
session.load.started
session.load.completed
attachments.started
attachments.completed
```

### Orchestration
```text
planning.started
planning.model.started
planning.model.completed
planning.completed
planning.failed
thread.resolve.started
thread.resolve.completed
pending_interaction.resolve.started
pending_interaction.resolve.completed
source_requirements.completed
```

### Context
```text
context.selection.started
context.selection.completed
context.item.selected
context.item.excluded
```

### Agents
```text
agent.started
agent.completed
agent.failed
agent.team_manager
agent.technical_authority
agent.incident_manager
agent.problem_manager
agent.automated_operations
```

### Models
```text
model.request.started
model.first_token
model.request.completed
model.request.failed
model.request.timeout
```

### Tools/dependencies
```text
tool.started
tool.completed
tool.failed
tool.timeout
knowledge.search.started
knowledge.search.completed
teams.list_chats.started
teams.list_chats.completed
teams.read_messages.started
teams.read_messages.completed
database.query.started
database.query.completed
storage.operation.started
storage.operation.completed
```

### Governance/finalization
```text
knowledge.selection.completed
procedure_action.resolved
command_authority.completed
approval.requested
approval.completed
authority.selected
provenance.completed
command_egress.completed
synthesis.started
synthesis.completed
persistence.started
persistence.completed
sse.started
sse.completed
turn.completed
turn.failed
turn.timeout
turn.cancelled
run.stalled
heartbeat
```

## Status vocabulary

```text
PENDING
RUNNING
COMPLETED
FAILED
TIMEOUT
CANCELLED
STALLED
```

`STALLED` is a watchdog state/event; it does not have to be terminal.

## Trace event schema

Conceptually:

```python
class TraceEvent:
    event_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    run_id: str
    turn_id: str
    session_id: str
    timestamp: datetime
    stage: str
    status: str
    agent: str | None
    tool: str | None
    dependency: str | None
    thread_id: str | None
    duration_ms: float | None
    metadata: dict[str, SafeValue]
    error_code: str | None
```

All metadata passes central redaction/allow-list validation.

## OTel resource attributes

Set once per process:
- `service.name=slopanoc`,
- service version/build commit,
- deployment environment,
- cloud provider/platform/region.

Never put session/run IDs in resource attributes.

## Span attributes

Low/medium cardinality:
- `slopanoc.stage`,
- `slopanoc.agent`,
- `slopanoc.tool`,
- `slopanoc.dependency`,
- `slopanoc.domain`,
- `slopanoc.dialogue_act`,
- `slopanoc.authority_mode`,
- `slopanoc.source_mode`,
- `slopanoc.outcome`,
- `slopanoc.retry_count`,
- `slopanoc.error_code`.

Trace-only high cardinality:
- run/turn/session/thread/case/fault IDs.

Use standard OpenTelemetry HTTP/DB/GenAI conventions where available.

## Model telemetry

Required:
- provider,
- model,
- operation role,
- agent,
- input/output tokens,
- cached tokens where available,
- total latency,
- first-token latency where available,
- retries,
- finish status/reason,
- tool/function-calling mode,
- error class.

Do not store hidden reasoning or full prompts/responses by default.

## Tool telemetry

Required:
- tool,
- owning agent,
- latency,
- attempt,
- outcome,
- timeout/retry,
- safe result count/category.

## Context Broker telemetry

Capture:
- candidates,
- selected/excluded counts,
- selected domains,
- token footprint estimate,
- exclusion reason counts,
- policy/version,
- target thread ID in traces.

Never dump full context payloads.

## Governance telemetry

Capture structured summaries only:
- TurnPlan,
- primary/supporting agents,
- source requirements,
- final authority,
- provenance counts by source class,
- command authority decision,
- approval decision,
- egress kept/removed counts.

## Metric cardinality

Allowed labels:
- environment,
- agent,
- tool,
- dependency,
- model/provider,
- domain,
- status,
- error code,
- operation category.

Never labels:
- run/turn/session/user/chat/case/fault IDs,
- raw error strings,
- prompt text.

## FinOps usage event

Each billable usage event contains:

```text
usage_event_id
timestamp
run_id
turn_id
trace_id
span_id
provider
service
sku/model
operation
agent
domain
quantity
unit
input_tokens
output_tokens
cached_tokens
request_count
retry_count
estimated_cost
currency
price_version
pricing_source
usage_estimated
environment
use_case_key
```

No sampling.

## Versioning

Store:
- telemetry schema version,
- cost schema version,
- pricing schema version.

Breaking changes require migrations or compatibility views.


## Release/configuration correlation attributes

Resource/process:
- `service.version`
- `slopanoc.git_sha`
- `slopanoc.release_id`
- `slopanoc.cloud_run_revision`

Turn/model:
- `slopanoc.model_version`
- `slopanoc.agent_contract_version`
- `slopanoc.prompt_version`
- `slopanoc.tool_schema_version`
- `slopanoc.turn_plan_schema_version`
- `slopanoc.telemetry_schema_version`
- `slopanoc.cost_schema_version`
- `slopanoc.pricing_version`
- `slopanoc.config_version`

High-cardinality values remain trace-only, never metric labels.


---

# FILE: 03_INSTRUMENTATION_AND_RUNTIME.md

# 03 — Instrumentation and Runtime

## Decision

OpenTelemetry is the canonical runtime instrumentation layer for traces, metrics and log correlation. SLOPANOC may wrap OTel for safe naming/redaction, but must not create a proprietary parallel tracing system.

## Initialization

Initialize once in FastAPI lifespan:
- tracer provider,
- meter provider,
- propagators,
- batch processors/exporters,
- resource attributes,
- log correlation,
- local/no-op mode.

Shutdown must flush with a bounded deadline.

## Application wrapper API

Provide helpers similar to:

```python
start_turn_trace(...)
span(name, **safe_attrs)
async_span(name, **safe_attrs)
event(name, **safe_attrs)
record_exception(...)
current_trace_ids()
metric_counter(...)
metric_histogram(...)
```

Wrappers must enforce naming, attach correlation IDs, pass metadata through redaction and update the active-run registry.

## Root trace

Instrument `backend/api/chat_service.py` first. The root span should be stable, e.g. `slopanoc.turn`.

Mandatory root phases:
1. request accepted,
2. session load,
3. attachments,
4. TurnPlan planning/server plan,
5. thread/pending interaction,
6. Context Broker,
7. agent execution,
8. tools/dependencies,
9. authority/provenance,
10. command egress,
11. final persistence,
12. SSE completion,
13. terminal cleanup.

Root span closes in `finally` even on failure.

## Active run registry

Conceptual model:

```python
class ActiveRun:
    run_id: str
    trace_id: str
    turn_id: str
    session_id: str
    status: str
    current_stage: str
    current_span_id: str | None
    current_agent: str | None
    current_tool: str | None
    current_dependency: str | None
    started_at: datetime
    stage_started_at: datetime
    last_progress_at: datetime
```

Operations:
- register,
- update stage,
- heartbeat,
- mark terminal,
- cleanup/TTL.

No raw prompt storage.

## GenAI/model spans

Every provider model invocation is one child span.

Record:
- provider/model,
- agent,
- role (`planning`, `specialist`, `synthesis`, etc.),
- input/output/cached tokens,
- TTFT,
- total latency,
- retries,
- finish reason,
- tool-calling mode,
- success/error.

Every model call also writes one usage-ledger event.

If token counts are estimated rather than provider-reported, mark `usage_estimated=true`.

## Agents

Canonical agents:
- `team_manager`,
- `technical_authority_engineer`,
- `incident_manager`,
- `problem_manager`,
- `automated_operations_engineer`.

Each invocation records:
- TurnPlan role (`primary`/`supporting`),
- start/end,
- outcome,
- child model/tool spans,
- contribution category,
- error code.

Never record chain-of-thought.

## Tools

Use centralized ADK tool callbacks/wrappers wherever possible.

Record:
- tool name,
- owning agent,
- attempt,
- duration,
- outcome,
- safe result category/count,
- timeout/retry.

Retries must not be invisible because they affect both latency and cost.

## Dependencies

### Microsoft Graph / Teams
Child spans for:
- list/resolve chats,
- pagination,
- read messages,
- attachment metadata where relevant.

Metrics:
- latency,
- HTTP/provider error,
- auth/rate limit,
- pages,
- counts,
- timeout.

Never store message bodies or bearer tokens.

### Knowledge
Keep `backend/tools/knowledge/diagnostic_trace.py` and bridge into OTel.

Trace:
- query preparation,
- sparse retrieval,
- dense/vector retrieval,
- fusion/ranking,
- applicability,
- selection.

Capture counts/scores/identities permitted by existing policy, not document bodies.

### Database
Instrument:
- connection acquisition/pool wait,
- query duration,
- transaction errors/timeouts.

Do not capture sensitive SQL parameters.

### Storage
Instrument object operations, bytes, latency and errors. Do not record signed URLs or credentials.

## Context Broker

One `context.selection` span with:
- candidates,
- selected/excluded counts,
- domains,
- token estimate,
- exclusion reason counts,
- policy version.

## Governance

Instrument, but never influence:
- TurnPlan,
- source requirements,
- knowledge selection,
- ProcedureAction,
- Command Authority,
- approval,
- TurnAuthority,
- provenance,
- command egress.

## Browser correlation

SSE progress messages include safe `run_id`, stage and elapsed time. Browser telemetry should record:
- prompt submitted,
- SSE connected,
- first event,
- first token,
- message completed received,
- rendered,
- SSE disconnected.

This distinguishes backend completion from frontend spinner failures.

## Sampling

Initial launch may retain 100% traces while volume permits.

Always retain errors/timeouts/stalled and governed operational turns initially.

Never sample cost usage records or aggregate metrics.


## Browser/backend trace propagation

Use W3C Trace Context:
- `traceparent`,
- optional `tracestate`.

Do not put user/session/customer data in OpenTelemetry baggage.

The backend generates/validates trusted `run_id` independently.

## Semantic-convention versioning

Pin OpenTelemetry SDK and semantic-convention versions.
No silent upgrade is permitted.


---

# FILE: 04_RELIABILITY_TIMEOUTS_ERRORS.md

# 04 — Reliability: Timeouts, Watchdogs and Errors

## Principle

A request may be slow or fail; it must never be operationally unknowable.

## Deadlines

All potentially blocking boundaries use configurable deadlines:
- model provider,
- Microsoft Graph,
- knowledge retrieval,
- database,
- object storage,
- generic external HTTP,
- telemetry/cost persistence.

Use a turn-level remaining budget plus child caps so sequential child calls cannot each consume a full global timeout.

## Watchdog

Track:
- current stage,
- stage start,
- last progress,
- elapsed.

If no progress exceeds configured threshold, emit `run.stalled` and increment stalled metrics.

A stall is observable before timeout and may later complete.

## Heartbeats

During long SSE turns emit a safe heartbeat periodically:

```json
{
  "type": "processing.heartbeat",
  "run_id": "...",
  "stage": "teams.read_messages",
  "elapsed_ms": 20014
}
```

This differentiates backend-alive from connection-broken.

## Error taxonomy

Stable codes, not raw exception strings.

### Planning
- `PLANNING_TIMEOUT`
- `PLANNING_INVALID`
- `PLANNING_FAILED`

### Model
- `MODEL_TIMEOUT`
- `MODEL_RATE_LIMIT`
- `MODEL_AUTH_ERROR`
- `MODEL_PROVIDER_ERROR`
- `MODEL_INVALID_RESPONSE`

### Graph
- `GRAPH_TIMEOUT`
- `GRAPH_AUTH_ERROR`
- `GRAPH_RATE_LIMIT`
- `GRAPH_NOT_FOUND`
- `GRAPH_PROVIDER_ERROR`

### Knowledge
- `KNOWLEDGE_TIMEOUT`
- `KNOWLEDGE_NO_SOURCE`
- `KNOWLEDGE_PROVIDER_ERROR`

### Database
- `DATABASE_TIMEOUT`
- `DATABASE_CONNECTION_ERROR`
- `DATABASE_QUERY_ERROR`
- `DATABASE_PERSISTENCE_ERROR`

### Storage
- `STORAGE_TIMEOUT`
- `STORAGE_ERROR`

### Agent/tool
- `SPECIALIST_FAILED`
- `TOOL_TIMEOUT`
- `TOOL_ERROR`

### Governance
- `SOURCE_GAP`
- `COMMAND_AUTHORITY_REJECTED`
- `APPROVAL_REJECTED`

### Streaming
- `SSE_DISCONNECTED`
- `SSE_COMPLETION_MISMATCH`

### Observability/FinOps
- `TRACE_EXPORT_FAILED`
- `METRIC_EXPORT_FAILED`
- `COST_LEDGER_PERSIST_FAILED`
- `TELEMETRY_DROPPED`

### Runtime
- `TURN_STALLED`
- `TURN_TIMEOUT`
- `TURN_CANCELLED`

## Retry policy

Retries only for retry-safe operations.

Per dependency define:
- retryable codes,
- max attempts,
- exponential backoff/jitter,
- idempotency guarantee.

Never automatically retry non-idempotent operational actions, approvals or command execution unless existing semantics explicitly make the retry safe.

## Circuit breakers

Consider for broad provider outages such as Graph/model provider. Circuit state must itself be observable.

Do not insert a circuit breaker into governed command execution without explicit safety review.

## User vs operator failure data

User receives:
- safe concise failure,
- retry/escalation guidance where appropriate.

Operator trace receives:
- error code,
- owning boundary,
- dependency,
- duration,
- retry count,
- correlation IDs.

No secrets or raw stack traces in user responses.


## SLO interaction

Timeout and terminal semantics must match `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md` so reliability controls and SLO denominators cannot diverge.


---

# FILE: 05_STORAGE_APIS_AND_UI.md

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


---

# FILE: 06_SRE_SLOS_METRICS_ALERTS.md

# 06 — SRE, SLOs, Metrics and Alerts

## Objective

Move SLOPANOC from subjective health assessment to measurable reliability.

## Golden signals

### Traffic
- turns/minute,
- active turns,
- model calls,
- tool calls.

### Latency
- turn p50/p95/p99,
- time to first token,
- planning latency,
- agent latency,
- Graph/knowledge/DB latency.

### Errors
- terminal failures,
- timeouts,
- planning failures,
- source gaps,
- persistence/export failures.

### Saturation
- concurrency,
- DB pool saturation,
- provider rate limits,
- exporter queue,
- active-run age.

## Core metric catalog

Counters:

```text
slopanoc_turn_total
slopanoc_turn_failed_total
slopanoc_turn_timeout_total
slopanoc_turn_stalled_total
slopanoc_planning_failed_total
slopanoc_source_gap_total
slopanoc_model_request_total
slopanoc_tool_request_total
slopanoc_model_reasoning_disclosure_total
slopanoc_command_authorized_total
slopanoc_command_rejected_total
```

Histograms:

```text
slopanoc_turn_duration_ms
slopanoc_model_duration_ms
slopanoc_model_ttft_ms
slopanoc_tool_duration_ms
slopanoc_context_tokens
slopanoc_input_tokens
slopanoc_output_tokens
```

Gauges:
- active turns,
- stalled turns,
- exporter queue/degraded state.

Labels must remain bounded: environment, domain, agent, tool, dependency, model, provider, status, error code.

## SLOs

Use the exact SLI formulas and provisional targets in `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md`. Recalibrate numeric targets only after baseline evidence; do not change numerator/denominator semantics without design review.

Implement definitions for:

### Availability
Valid terminal outcomes / accepted valid requests.
Clarification and safe source-gap outcomes are not necessarily infrastructure failures.

### Completion
Turns that reach a terminal state and are not orphaned.

### Latency
Maintain separate SLO classes for:
- general,
- Teams,
- troubleshooting,
- complex/multi-agent.

### Model/tool reliability
Success rate per provider/tool.

### SSE delivery
Backend completion emitted vs browser completion received.

### Trace completeness
Turns with required trace stages / turns.

### Cost-ledger completeness
Billable calls with usage record / billable calls. Target effectively 100%.

## Error budgets

Use error budgets for reliability SLOs.

Do not use an error budget for safety events such as unauthorized command exposure or secret leakage. These are zero-tolerance incidents.

## Alerts

### Critical
- unauthorized command exposure,
- credential/secret in telemetry,
- sustained application outage,
- severe DB persistence outage,
- severe cost-ledger coverage loss.

### High
- turn failure spike,
- p95 latency violation,
- model/Graph timeout spike,
- stalled active runs,
- SSE mismatch,
- sustained trace exporter failure.

### Medium
- source-gap spike,
- planning failure increase,
- token/cost per turn anomaly,
- observability cost spike.

For mature SLOs use multi-window burn-rate alerts.

## Synthetic monitoring

Implement safe synthetic checks for:
- general prompt,
- known Teams lookup if an approved synthetic tenant/workspace exists,
- governed knowledge query,
- DB/session path,
- SSE path.

Tag synthetic usage so it is excluded from business unit economics or shown separately.


## Infrastructure observability

Infrastructure metrics and alerts in `17_INFRASTRUCTURE_OBSERVABILITY.md` are mandatory parts of the SRE dashboard.


---

# FILE: 07_FINOPS_ARCHITECTURE.md

# 07 — FinOps Architecture

## Mission

FinOps must answer:
- what SLOPANOC costs,
- what each turn/agent/model/tool costs,
- who or what should receive allocation,
- how runtime estimated cost reconciles to provider billing,
- which workloads produce value efficiently,
- where cost anomalies/optimization opportunities exist.

FinOps is a first-class production subsystem.

## Two cost truths

### Runtime estimated cost
Available near turn completion.

Calculated from:
- model input/output/cached token usage,
- embedding/model usage,
- priceable API calls,
- measurable direct resource usage,
- optional allocated infrastructure estimate.

Store amount, currency, price version and method.

### Provider billed cost
Financial truth from provider/cloud billing export.

Store separately:
- gross cost,
- credits/discounts,
- net/effective cost,
- billing period,
- resource/SKU dimensions.

Never overwrite estimated cost with billed cost.

## FinOps pipeline

```text
Runtime usage events
       |
       +--> usage ledger --> price engine --> real-time estimated cost
       |
Provider billing export
       |
Pricing catalog
       |
Resource labels/metadata
       |
       +--> normalization --> reconciliation --> allocation --> unit economics
```

## GCP billing integration

Support:
- detailed Cloud Billing export to BigQuery,
- FOCUS-compatible export where available,
- pricing export/catalog,
- normalized SLOPANOC-owned views.

Do not wire dashboards directly to raw evolving billing tables.

## Allocation dimensions

Use bounded taxonomy:
- environment,
- application=`slopanoc`,
- service,
- agent,
- model/provider,
- operation,
- domain,
- use case,
- SDU/team,
- cost center,
- project,
- region,
- billing SKU/resource class.

Run/turn/session IDs are allowed in ledger drilldowns but not metrics labels.

## Direct/shared/unallocated

### Direct
Examples:
- model token charges,
- billable embedding request,
- per-request paid API where attributable.

### Shared
Examples:
- Cloud SQL baseline,
- Cloud Run minimum instances,
- shared monitoring,
- shared storage/network.

### Unallocated
Costs not safely attributable.

Unallocated spend is a first-class visible category. Do not force fake precision.

## Allocation

Every allocation records:
- cost pool,
- target,
- driver,
- method,
- version,
- confidence,
- period.

Possible drivers:
- turn volume,
- compute duration,
- token share where causal,
- query load,
- storage byte-days.

## FinOps health metrics

- usage-ledger coverage,
- estimated-cost coverage,
- billing-ingestion coverage,
- allocation coverage,
- unallocated percentage,
- reconciliation variance,
- price-catalog age.

## Observability's own cost

Track:
- trace ingestion,
- logging,
- metrics,
- BigQuery storage/query,
- retention,
- dashboards/exporters.

Expose:

```text
observability_cost / total_slopanoc_cost
```

and budget it.

## Financial access

Separate engineering/SRE/FinOps/finance access from conversation-content access.

A FinOps user should be able to see model/agent/team/use-case spend without reading Teams messages or prompts.

## Currency

Store provider/native currency and normalized reporting currency only when conversion has a versioned exchange-rate source and effective date.


## Fixed billing-source decision

Primary production accounting inputs:
1. GCP Detailed Billing Export.
2. GCP Pricing Export/catalog.

FOCUS is a supplementary normalized source, not the sole authoritative source until explicitly approved by lifecycle/governance review.

Primary SLOPANOC reporting currency: EUR.

Retain native provider currency and conversion metadata where conversion is performed.

Accounting durability is defined in `20_FINOPS_ACCOUNTING_DURABILITY.md`.


---

# FILE: 08_FINOPS_DATA_MODEL_AND_UNIT_ECONOMICS.md

# 08 — FinOps Data Model and Unit Economics

## `ai_usage_ledger`

Suggested fields:

```text
usage_event_id
timestamp
run_id
turn_id
trace_id
span_id
provider
service
model
sku
operation
agent
domain
input_tokens
output_tokens
cached_input_tokens
cached_output_tokens
request_count
retry_count
quantity
unit
estimated_cost
currency
price_version
pricing_source
usage_estimated
environment
use_case_key
team_key
cost_center_key
created_at
```

Coverage target: 100%. No sampling.

## Price catalog

`finops_price_catalog`:

```text
price_version
provider
service
sku
model
region
effective_from
effective_to
input_unit_price
output_unit_price
cached_input_unit_price
request_unit_price
other_unit_price
billing_unit
currency
source
loaded_at
```

Historical prices used by ledger records must remain reproducible.

## Turn cost summary

`finops_turn_cost`:

```text
run_id
turn_id
session_id
estimated_direct_cost
estimated_shared_cost
estimated_total_cost
billed_direct_cost
allocated_shared_cost
reconciled_total_cost
currency
model_cost
graph_cost
database_cost
storage_cost
observability_cost
input_tokens
output_tokens
allocation_version
reconciliation_status
```

## Billing normalization

Stable views:
- `finops_billing_normalized`,
- `finops_pricing_normalized`,
- `finops_resource_cost_daily`.

Include project/service/SKU/resource/labels/credits/gross/net cost/usage quantity/unit/date.

## Reconciliation

Compare runtime estimates and provider billing at realistic levels:
1. model/provider direct usage,
2. service/day,
3. project/day,
4. billing period.

Shared infrastructure cannot be expected to reconcile exactly per turn.

Store:
- estimated,
- billed,
- variance,
- variance percentage,
- reason/status.

## Cost allocation

`finops_cost_allocation`:

```text
period_start
period_end
source_cost_pool
environment
service
target_type
target_key
direct_cost
shared_cost
allocated_cost
allocation_driver
allocation_method
allocation_confidence
allocation_version
currency
```

## Unit economics

### Technical units
- cost / turn,
- cost / completed turn,
- cost / model call,
- cost / specialist call,
- cost / governed answer,
- cost / Teams lookup,
- cost / knowledge retrieval,
- cost / authorized action.

### Operational/business units
- cost / troubleshooting case,
- cost / resolved incident,
- cost / first-time-correct outcome,
- cost / automation artifact,
- cost / use case,
- cost / SDU/team.

If outcome data does not yet exist, define the join/schema but mark the metric unavailable. Never fabricate business value.

## Quality + latency + cost

The data model must support comparison by model/agent/version:

```text
model A
  cost/turn
  p95 latency
  quality / first-time-correct
  governed-answer rate

model B
  ...
```

This enables optimization without optimizing cost at the expense of quality or safety.

## Budgets

`finops_budget`:

```text
budget_id
scope_type
scope_key
period
currency
budget_amount
warning_threshold_pct
critical_threshold_pct
forecast_amount
actual_amount
owner
```

Scopes:
- SLOPANOC total,
- environment,
- team/SDU,
- model,
- agent,
- use case.

## Forecasting

Start with transparent methods:
- daily run-rate,
- MTD trend,
- month-end projection,
- seasonality after sufficient history.

Do not begin with opaque ML forecasting.

## Anomaly detection

Detect:
- cost/turn jump,
- tokens/turn jump,
- calls/turn jump,
- retry-driven spend,
- failed-turn spend,
- observability cost spike,
- reconciliation variance spike.

Every anomaly includes baseline, observed value, variance, dimensions and sample run references where access policy permits.


## Accounting-grade ledger semantics

The usage ledger is immutable/idempotent and follows `20_FINOPS_ACCOUNTING_DURABILITY.md`.

Do not implement pricing solely as fixed input/output columns. Use a component/tariff model capable of:
- token type,
- modality,
- tier,
- batch mode,
- request charge,
- cache charge,
- embeddings,
- storage/compute.

Quality/value joins use `19_AI_QUALITY_OUTCOMES.md`.


---

# FILE: 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md

# 09 — Security, Privacy, RBAC, Retention and Sampling

## Core rule

Observability data is production operational data and must be governed like production data.

## Data classification

### Safe by default
- timing,
- stage/status,
- agent/tool/model names,
- token/count metrics,
- stable error codes,
- approved source identities.

### Restricted
- session/run/thread IDs,
- user identifiers,
- case/fault IDs,
- resource/chat IDs.

### Prohibited by default
- credentials/tokens,
- auth headers,
- full prompts,
- full assistant responses,
- Teams message bodies,
- attachment contents,
- knowledge document bodies,
- hidden chain-of-thought.

## Central redaction

All custom telemetry metadata passes through a single allow-list/redactor.

Responsibilities:
- permit known fields,
- remove secret-looking fields,
- truncate strings,
- sanitize error messages,
- optionally hash/opaque selected identifiers,
- reject unexpected nested payloads.

Do not rely on each caller remembering to redact.

## RBAC

Suggested roles:

### User
Own chat progress only.

### Operator
Safe active-run/timeline metadata.

### Developer/SRE
Technical trace/error details.

### FinOps
Cost/usage/allocation, not conversation content.

### Admin
Configuration/retention.

### Auditor
Read-only governed summaries according to policy.

Enforce authorization server-side.

## Retention tiers

Define independent configurable retention for:
- cost ledger — long,
- aggregate metrics — long,
- error/timeout traces — medium/long,
- governed operational traces — medium/long initially,
- healthy generic traces — shorter/sampled later,
- active-run state — TTL,
- domain diagnostics — bounded.

Final day counts depend on enterprise policy and compliance requirements.

## Sampling

Never sample:
- cost ledger,
- aggregate metrics,
- security/safety events.

Always retain initially:
- errors,
- timeouts,
- stalled runs,
- command authority/approval paths,
- governed operational turns if policy permits.

Healthy general traces can be sampled later after baselines exist.

## Deletion interaction

If sessions/conversations can be deleted, define whether related trace metadata is deleted, anonymized or retained under audit exception. Do not create a hidden shadow copy of deleted content.

## Encryption

Use platform encryption in transit/at rest. Apply production metadata access controls to telemetry databases and billing datasets.

## Secret leakage testing

Use fake secrets in automated tests and verify they never appear in:
- traces,
- logs,
- metrics,
- cost ledger,
- API responses.

If sanitizer detects a prohibited field, increment a safe counter without exporting the value.


## Fixed production retention

Initial targets:

| Data | Retention |
|---|---:|
| Normal successful traces | 30 days |
| Error/timeout/stalled traces | 90 days |
| Governed operational traces | 90 days |
| Structured application logs | 30 days unless policy requires longer |
| Aggregate metrics | 13 months |
| FinOps usage ledger / billing analytics | 24 months |
| Active-run status | short TTL after terminal state |

Enterprise/compliance policy may require longer retention.

## Fixed sampling policy

Initial stabilization: 100% trace capture.

After stabilization:
- retain 100% errors/timeouts/stalled/safety/governed operational turns;
- retain 100% cost ledger and aggregate metrics;
- healthy general chat may be sampled to 10–20%.

Sampling changes are versioned and observable.


---

# FILE: 10_DASHBOARDS_RUNBOOKS_OPERATING_MODEL.md

# 10 — Dashboards, Runbooks and Operating Model

## Dashboard: SLOPANOC Health

Show:
- request rate,
- active/stalled,
- completed/failed/timeout,
- p50/p95/p99,
- SLO/error budget,
- error codes,
- exporter health.

## Dashboard: AI / Model

Show:
- calls by model/agent,
- TTFT,
- total model latency,
- input/output tokens,
- retries,
- planning failures,
- provider errors,
- model cost.

## Dashboard: Agents

For Team Manager, TAE, Incident Manager, Problem Manager and AOE:
- volume,
- latency,
- failures,
- token usage,
- cost,
- role if useful.

## Dashboard: Dependencies

Microsoft Graph, Cloud SQL, knowledge, storage, model provider:
- success,
- p95 latency,
- timeout,
- retry,
- rate limit,
- saturation.

## Dashboard: Governance / Safety

- governed answer volume,
- source gaps,
- model-reasoning disclosures,
- command authority authorized/rejected,
- approvals,
- egress removals,
- provenance coverage,
- safety events.

## Dashboard: FinOps Executive

- MTD spend,
- forecast,
- budget variance,
- spend by environment/team/use case/model/agent,
- estimated vs reconciled variance,
- unallocated cost,
- observability cost ratio.

## Dashboard: Unit Economics

- cost/turn,
- cost/completed turn,
- cost/governed answer,
- cost/troubleshooting case,
- cost/resolved incident where available,
- quality/latency/cost comparisons.

## Mandatory runbooks

### Stuck request
1. obtain `run_id`;
2. inspect active current stage;
3. inspect current/critical span;
4. identify agent/tool/dependency;
5. check timeout/watchdog;
6. check provider health;
7. safely retry/terminate only if policy allows;
8. attach run to incident record.

### Model timeout spike
Check provider, rate limits, retries, model-specific pattern and approved fallback policy.

### Graph failure
Check auth, permissions, throttling, provider status, latency/timeouts.

### Database degradation
Check pool saturation, connection failures, slow queries and Cloud SQL health.

### Planning failures
Check invalid plans, model/tool compliance, prompt/tool declaration changes.

### Cost anomaly
Identify model/agent/use case, token/call/retry change, quality impact and rollback/optimization option.

### Telemetry outage
Confirm product availability, exporter health, dropped telemetry, FinOps coverage risk.

### Safety event
Immediate escalation for command/provenance/secret failures; zero-tolerance process.

## Ownership

Assign named owners for:
- observability platform,
- SRE/SLO,
- FinOps,
- agents/tools,
- security/privacy.

Dashboards without owners become stale.

## Cadence

Recommended:
- daily automated health/anomaly check,
- weekly SLO/incident review,
- weekly/biweekly AI quality-cost review,
- monthly FinOps reconciliation/budget review,
- quarterly retention/cardinality review.


## Observability cost guardrail

Track and budget:
- trace ingestion,
- log ingestion,
- metric ingestion,
- Collector compute,
- BigQuery storage/query,
- retention.

Executive FinOps dashboard shows:

```text
observability_cost / total_slopanoc_cost
```

Alert thresholds are configured during production baseline and reviewed monthly.

## Infrastructure dashboard

Mandatory infrastructure panels are defined in `17_INFRASTRUCTURE_OBSERVABILITY.md`.

## Continuous profiling dashboard

Include release-correlated CPU/memory profile comparisons from `24_CONTINUOUS_PROFILING.md`.


---

# FILE: 11_SLOPANOC_INTEGRATION_MAP.md

# 11 — SLOPANOC Integration Map

## Rule

Before editing any path, inspect the current working tree. Local code may contain uncommitted architecture newer than remote GitHub.

## `backend/api/chat_service.py`

Responsibilities to add/integrate:
- root turn trace,
- active-run lifecycle,
- request/session/planning/context/finalization stages,
- terminal status,
- usage/cost rollup,
- SSE progress/heartbeat,
- telemetry finalization.

Do not create a second turn executor.

## `backend/api/app.py`

Integrate:
- OTel startup/shutdown through lifespan,
- observability APIs,
- FinOps APIs,
- RBAC wiring.

## `backend/api/perf_timing.py`

Audit and bridge useful timings into OTel. Remove duplication only after compatibility is proven.

## `backend/api/run_trace.py`

Audit existing run-scoped mechanisms and converge with canonical active-run registry. Avoid two registries.

## `backend/tools/knowledge/diagnostic_trace.py`

Keep deep governance diagnostics. Bridge to spans/events while preserving `OBSERVABILITY ONLY — NEVER EVIDENCE` behavior.

## `backend/context/assembly.py`

Instrument candidate/selected/excluded counts, domains, token footprint and policy. Never log context body.

## `backend/conversation/*`

If present in the current tree:
- TurnPlan,
- thread registry,
- pending interaction,
- authority,
- provenance.

Instrument transitions and decision summaries only. Telemetry never feeds back as authority.

## Team Manager

Instrument:
- planning model call,
- specialist delegation,
- synthesis model call,
- synthesis provenance declaration,
- callback enforcement failures.

## Specialist agents

TAE / Incident Manager / Problem Manager / AOE:
- agent span,
- model spans,
- tool spans,
- contribution outcome.

## `backend/tools/*`

Use centralized instrumentation where possible.

### Knowledge
Retrieval stages and counts.

### Teams
Graph child spans, pages/counts, timeouts/rate limit/auth errors.

## `backend/config/settings.py`

Add validated observability/timeout/FinOps settings. Do not silently enable production exporters without config.

## Alembic

Use migrations for:
- turn summary,
- run status if persistent,
- usage ledger,
- price catalog,
- allocation,
- budgets,
- reconciliation.

## Frontend `src/`

Inspect current API/SSE/chat state first.

Add:
- run ID in processing state,
- public progress stage,
- heartbeat freshness,
- admin observability console,
- timeline,
- FinOps views under RBAC.

Normal users receive only safe progress labels.

## Existing docs

After implementation, update architecture/build/env/runbook docs. Do not overwrite governance contracts with telemetry design.


## Infrastructure-as-code

Implement Terraform/source-controlled observability infrastructure per `23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md`.

## Browser trace context

Update frontend API/SSE client to propagate W3C `traceparent` while retaining server-trusted `run_id`.

## Quality/outcome store

Add outcome persistence/repository integration according to `19_AI_QUALITY_OUTCOMES.md`.


---

# FILE: 12_IMPLEMENTATION_ROADMAP.md

# 12 — Implementation Roadmap

## Delivery strategy

Build sequentially. Do not jump to dashboards before telemetry contracts are stable. Do not build FinOps dashboards before usage accounting is trustworthy.

---

## M0 — Architecture Contract

### Build
- create `backend/observability` skeleton;
- define stage/status/error enums;
- define attribute names;
- define redaction and metric-cardinality rules;
- define settings;
- define telemetry/cost schema versions.

### Exit
- no production behavior changed;
- one canonical naming contract exists;
- docs and code skeleton agree.

---

## M1 — OpenTelemetry Foundation + Production Collector

### Build
- OTel SDK initialization in FastAPI lifespan;
- production OTel Collector topology from `16_PRODUCTION_TELEMETRY_BACKEND.md`;
- structured logging foundation from `18_STRUCTURED_LOGGING_STANDARD.md`;
- trace/metric/log correlation;
- OTLP/local/no-op exporters;
- resource metadata;
- exporter-health metrics;
- bounded shutdown flush.

### Exit
- app boots with OTel on/off;
- sample span exports;
- exporter outage does not crash a user turn.

---

## M2 — Root Turn Trace

### Build
- root span in canonical `chat_service` event generator;
- active-run registry;
- session/planning/thread/context/finalization stages;
- terminal cleanup;
- safe run ID in SSE/API.

### Exit
- one prompt is reconstructable phase-by-phase;
- active run exposes current stage.

---

## M3 — Model Instrumentation + Usage Ledger

### Build
- every model call;
- TTFT where available;
- input/output/cached tokens;
- retry/finish/error;
- agent/role;
- one usage ledger row per call.

### Exit
- every synthetic model call attributable to run + agent + role;
- usage-ledger coverage = 100% in validation scenarios.

---

## M4 — Agent and Tool Instrumentation

### Build
- spans for Team Manager, TAE, IM, PM, AOE;
- centralized tool callbacks/wrappers;
- primary/supporting role;
- contribution/outcome classification.

### Exit
- no specialist/tool execution is invisible.

---

## M5 — Dependency + Infrastructure Instrumentation

### Build
- Microsoft Graph;
- knowledge internals;
- DB/pool;
- storage;
- other HTTP/provider calls.

### Exit
- a blocked external dependency is identifiable as current child span.

---

## M6 — Reliability Controls

### Build
- configurable deadlines;
- retry policy;
- watchdog;
- heartbeats;
- structured error mapping;
- safe cancellation;
- circuit breakers only where justified.

### Exit
- no indefinite Processing state;
- stalled and timeout states are visible;
- non-idempotent operations are not blindly retried.

---

## M7 — Persistence and Observability APIs

### Build
- turn summaries;
- active-run query;
- timeline query;
- retention/TTL;
- RBAC;
- multi-instance strategy.

### Exit
- a run can be retrieved by `run_id`;
- active-run support does not depend on local shell logs.

---

## M8 — UI Operational Visibility

### Build
- safe progress labels;
- run details;
- active-run admin screen;
- timeline/waterfall;
- governance/performance/cost views.

### Exit
- operator can diagnose a deliberately stalled dependency through UI/API alone.

---

## M9 — SRE Metrics, SLOs and Alerts

### Build
- golden signals;
- AI-specific metrics;
- initial SLO definitions;
- error budgets;
- burn-rate alerts;
- synthetic checks.

### Exit
- health is objectively measurable;
- alert dry-run validated.

---

## M10 — Runtime FinOps Cost Engine

### Build
- versioned pricing catalog;
- per-call estimator;
- per-turn rollup;
- per-agent/model/domain aggregation;
- usage-ledger coverage metric.

### Exit
- every model call has estimated cost;
- every turn has estimated direct cost;
- calculation can be replayed from usage + price version.

---

## M11 — Provider Billing Integration

### Build
- GCP detailed billing export;
- FOCUS export where applicable;
- pricing export/catalog;
- normalized BigQuery views;
- ingestion health.

### Exit
- provider billed costs load automatically;
- dashboards use normalized views, not raw evolving schemas.

---

## M12 — Reconciliation and Allocation

### Build
- estimated vs billed reconciliation;
- direct/shared/unallocated pools;
- allocation drivers;
- method/version/confidence;
- allocation coverage metrics.

### Exit
- unallocated spend is visible;
- reconciliation variance visible;
- shared allocation reproducible.

---

## M13 — Unit Economics

### Build
- cost/turn;
- cost/completed turn;
- cost/agent/model;
- cost/governed answer;
- cost/Teams lookup;
- cost/knowledge retrieval;
- cost/case/resolution where outcome data exists;
- quality-latency-cost joins.

### Exit
- engineering and FinOps can compare architecture/model choices with cost and quality.

---

## M14 — Budgets, Forecasting and Anomalies

### Build
- budget scopes;
- MTD actual;
- month-end forecast;
- threshold alerts;
- token/call/cost anomaly detection;
- observability-cost budget.

### Exit
- proactive anomaly/budget alert demonstrated end-to-end.

---

## M15 — Security, Retention and Sampling Hardening

### Build
- RBAC review;
- redaction adversarial tests;
- retention jobs;
- sampling;
- access audit if required;
- deletion/anonymization interaction.

### Exit
- fake secrets cannot escape telemetry;
- retention and sampling behavior proven.

---

## M16 — Failure Injection and Load Validation

### Build
- model timeout;
- Graph failure/rate limit;
- DB failure;
- knowledge failure;
- telemetry exporter outage;
- cost-ledger failure;
- SSE disconnect;
- multi-instance/concurrency;
- load/overhead measurement.

### Exit
- observability still explains the system when dependencies fail;
- instrumentation overhead is within agreed budget.

---

## M17 — Production Operationalization

### Build
- final dashboards;
- final alerts;
- runbooks;
- owners;
- SRE and FinOps review cadence;
- production release checklist.

### Exit
All applicable checks in `15_DEFINITION_OF_DONE.md` pass.

## Dependency rules

- M8 depends on stable M2–M7 data contracts.
- M10 depends on M3 usage accuracy.
- M11–M14 depend on durable M10 ledger.
- aggressive trace sampling must wait until M9 baselines and M15 policy exist.


---

## M18 — Release/CI/IaC Hardening

### Build
- W3C Trace Context;
- release/config/model/prompt/tool schema correlation;
- Terraform observability-as-code;
- CI observability contract gates;
- deployment annotations.

### Exit
- production regressions correlate to exact release/config;
- manual observability drift is detectable;
- CI blocks telemetry contract violations.

---

## M19 — Quality/Outcome + Continuous Profiling

### Build
- outcome model from `19_AI_QUALITY_OUTCOMES.md`;
- cost-quality joins;
- CPU/memory continuous profiling.

### Exit
- FinOps can compare cost/latency against available quality outcomes;
- profile data correlates with release/revision;
- profiling overhead measured.


---

# FILE: 13_TEST_VALIDATION_ACCEPTANCE.md

# 13 — Test, Validation and Acceptance

## Principle

Observability is not proven by a happy-path log line. It is proven when it correctly explains success, failure, timeout, retry, cancellation, safety decisions and cost.

## Unit tests

### Tracing
- root creation/finalization;
- nested spans;
- context propagation;
- terminal cleanup;
- exception recording.

### Active registry
- lifecycle;
- concurrent runs;
- current-stage updates;
- heartbeat;
- TTL/cleanup.

### Redaction
Inject fake:
- bearer token,
- API key,
- password,
- prompt content,
- Teams message.

Assert prohibited content never appears in exported trace/log/ledger/API data.

### Error taxonomy
Known exceptions map to stable codes.

### Cost estimator
- token -> cost;
- price version;
- cached-token pricing;
- currency;
- provider-missing usage estimates marked estimated.

### Allocation
- direct/shared/unallocated;
- deterministic sums;
- rounding;
- confidence/version.

## Integration traces

Validate expected topology for:
- general prompt,
- Teams task,
- troubleshooting,
- multi-agent support,
- governed command path,
- planning failure,
- source gap,
- pending interaction suspend/resume.

## Failure injection

Inject:
- model timeout,
- Graph timeout,
- Graph rate limit,
- DB timeout,
- knowledge failure,
- storage failure,
- trace exporter failure,
- cost-ledger persistence failure,
- SSE disconnect.

Assert:
- owning span identified,
- stable error code,
- terminal behavior,
- user-safe response,
- metric/alert emitted where appropriate.

## Watchdog

Block a dependency beyond stall threshold but below timeout.

Expected:
- `run.stalled`,
- current stage remains queryable,
- heartbeat/admin UI shows blocked stage,
- later completion/timeout transitions correctly.

## FinOps validation

### Coverage
Every synthetic billable model call creates exactly one ledger event.

### Retry cost
Retries increase usage/cost correctly.

### Pricing replay
Old usage recalculates exactly from its stored price version.

### Reconciliation
Synthetic billing rows produce expected variance.

### Allocation
Allocated + unallocated equals source cost pool within rounding tolerance.

### Unit economics
Aggregations reconcile to turn and period totals.

## SLO tests

Replay metric fixtures and verify:
- availability,
- latency percentiles,
- error budget,
- burn-rate thresholds.

## Frontend

Test:
- progress labels,
- run ID,
- heartbeat freshness,
- terminal completion,
- SSE disconnect,
- admin timeline,
- RBAC denial,
- FinOps pages exclude message bodies.

## Load tests

Measure:
- instrumentation overhead,
- exporter queue behavior,
- active registry concurrency,
- ledger write overhead,
- dashboard query performance.

## Multi-instance

If Cloud Run scales horizontally, verify:
- complete correlation,
- active status accessibility,
- no invalid process-local support assumptions.

## Acceptance evidence

Produce and retain:
1. successful end-to-end turn trace;
2. stalled/timeout trace;
3. governed troubleshooting trace;
4. per-turn cost breakdown;
5. billing reconciliation sample;
6. SLO dashboard;
7. alert evidence;
8. redaction evidence;
9. load/overhead report.


## CI contract tests

Validate:
- stage/status/error vocabulary,
- metric-cardinality policy,
- prohibited telemetry fields,
- span closure,
- model-call usage accounting,
- telemetry schema versioning,
- pricing schema compatibility.

## Release/config correlation tests

Assert representative traces include:
- Git SHA,
- release/revision,
- model,
- required schema/config versions.

## Infrastructure tests

Validate dashboards/alerts receive:
- Cloud Run,
- Cloud SQL,
- Collector,
- BigQuery,
- Graph dependency telemetry.

## Quality/outcome tests

Validate:
- unknown outcomes remain unknown,
- explicit/server-owned outcomes join correctly to turn cost,
- FTC is not fabricated.

## Profiling overhead tests

Compare profiling on/off:
- CPU,
- memory,
- p95 latency,
- throughput.


---

# FILE: 14_CODEX_EXECUTION_GUIDE.md

# 14 — Codex Execution Guide

## Purpose

This file tells a coding agent how to execute the roadmap without architectural drift.

## Before every milestone

1. Read `README.md`.
2. Read `00_MASTER_BUILD_CONTRACT.md`.
3. Read the milestone in `12_IMPLEMENTATION_ROADMAP.md`.
4. Read all directly relevant design files.
5. Inspect the **current working tree** and `git diff`.
6. Inspect existing implementations before creating abstractions.
7. Produce a brief implementation map:
   - files to add,
   - files to modify,
   - existing code to reuse,
   - invariants at risk.

Do not code first and rationalize architecture later.

## Local working tree wins

The developer may have uncommitted changes. Never reset, checkout, overwrite or revert unrelated work.

Remote GitHub may be stale compared with local code.

## Coding rules

### Reuse before replace
Prefer adapters/wrappers/extensions over duplicate subsystems.

Do not create:
- a second turn pipeline,
- a second run registry,
- a second cost calculation truth,
- a second telemetry naming scheme.

### Keep orchestration thin
`chat_service.py` coordinates; `backend/observability` implements reusable observability behavior.

### Server-owned semantics
Stage/status/error/cost categories are server code and stable enums/codes, not model-generated free text.

### No hidden reasoning
Never enable full prompt/model reasoning logging as a shortcut.

### Cardinality discipline
Never add run/session/case IDs as metric labels.

### Financial discipline
Never silently substitute estimated cost for billed cost or spread unknown cost to make allocation look complete.

## Milestone completion report

After each milestone report:

### Implemented
Exact functionality.

### Files changed
Path and purpose.

### Invariants preserved
Especially command/governance safety.

### Configuration added

### Migrations added

### Telemetry/FinOps output
Example spans/metrics/ledger rows.

### Known limitations
Only intentional deferrals.

### Exit criteria
Pass/fail for every milestone gate.

## Stop conditions

Stop and report a blocker rather than invent unsafe behavior if:
- implementation requires weakening command authority;
- security/compliance policy is unknown but raw data exposure is required;
- financial allocation key does not exist;
- correct implementation would require logging prohibited content.

## Review checklist

Before milestone commit:
- no duplicate telemetry systems;
- imports clean;
- async spans close;
- exporter failure bounded;
- secrets redacted;
- metric labels bounded;
- usage ledger unsampled;
- pricing version persisted;
- migrations reversible/safe;
- tests updated/added;
- docs updated.

## Commit discipline

Prefer logical milestone/submilestone commits.

Suggested prefixes:
- `obs:`
- `sre:`
- `finops:`
- `ui:`
- `docs:`

Do not mix unrelated conversational architecture changes into observability work.


## Approved production decisions

Codex must not ask to redesign or choose alternatives for:
- OTel Collector -> Cloud Trace/Monitoring/Logging.
- Cloud SQL durable active-run status.
- JSON structured logs.
- Terraform observability-as-code.
- W3C Trace Context.
- Detailed Billing + Pricing primary; FOCUS supplemental.
- EUR reporting.
- Accounting-grade unsampled cost ledger.
- fixed initial retention/sampling.
- quality/outcome telemetry.
- CI contract gates.
- continuous profiling.

If local repository constraints make one technically impossible, report the conflict and smallest compatible adaptation; do not silently choose another architecture.


---

# FILE: 15_DEFINITION_OF_DONE.md

# 15 — Production Definition of Done

The build is complete only when every applicable item is demonstrably true.

## Correlation

- [ ] Every turn has `trace_id`.
- [ ] Every turn has trusted `run_id`, `turn_id`, `session_id`.
- [ ] Nested agent/tool/dependency spans preserve context.
- [ ] Browser and backend correlate by run.

## Runtime observability

- [ ] Request acceptance visible.
- [ ] Session load visible.
- [ ] Turn planning visible.
- [ ] Thread/pending interaction visible.
- [ ] Context Broker visible.
- [ ] Every agent visible.
- [ ] Every model call visible.
- [ ] Every tool call visible.
- [ ] Every external dependency wait visible.
- [ ] Final authority visible.
- [ ] Provenance summary visible.
- [ ] Command egress visible.
- [ ] Persistence visible.
- [ ] SSE completion visible.
- [ ] UI render visible.

## Stuck request diagnosis

- [ ] Active runs queryable.
- [ ] Current stage/agent/tool/dependency queryable.
- [ ] Stage elapsed time queryable.
- [ ] Last progress queryable.
- [ ] Watchdog emits stalled state.
- [ ] Heartbeat differentiates alive/disconnected.
- [ ] All external waits have deadlines.
- [ ] No indefinite processing state.

## Reliability/SRE

- [ ] Structured error taxonomy.
- [ ] Retry accounting.
- [ ] Timeout/stall metrics.
- [ ] p50/p95/p99.
- [ ] Availability/completion SLOs.
- [ ] Model/tool/dependency reliability metrics.
- [ ] SSE delivery metric.
- [ ] Trace completeness metric.
- [ ] Error budgets where applicable.
- [ ] Zero-tolerance safety alerts.
- [ ] Synthetic monitoring.
- [ ] Runbooks.

## FinOps runtime usage

- [ ] Every billable model call creates ledger record.
- [ ] Input/output token usage recorded.
- [ ] Cached tokens recorded where available.
- [ ] Retries included.
- [ ] Estimated usage explicitly marked.
- [ ] Every turn has estimated direct cost.
- [ ] Price version stored.
- [ ] Cost ledger coverage measured and effectively 100%.
- [ ] Ledger is not sampled.

## Billing/reconciliation

- [ ] Billing export enabled.
- [ ] Pricing data available.
- [ ] Stable normalized billing views.
- [ ] Estimated vs billed reconciliation.
- [ ] Variance visible.
- [ ] Direct/shared/unallocated classification.
- [ ] Allocation method/version/confidence.
- [ ] Unallocated cost visible.

## Unit economics

- [ ] Cost/turn.
- [ ] Cost/completed turn.
- [ ] Cost/model and agent.
- [ ] Cost/governed answer.
- [ ] Cost/Teams investigation.
- [ ] Cost/knowledge retrieval.
- [ ] Cost/troubleshooting case where outcomes exist.
- [ ] Cost/resolved incident where outcomes exist.
- [ ] Quality/latency/cost comparison available.

## Budget/forecast

- [ ] Total SLOPANOC budget.
- [ ] Environment budgets.
- [ ] Team/use-case/model budgets are implemented when those allocation dimensions are available; otherwise the dimension is explicitly marked unavailable rather than silently omitted.
- [ ] MTD actual.
- [ ] Month-end forecast.
- [ ] Cost anomalies.
- [ ] Token/model-call anomalies.
- [ ] Observability cost tracked and budgeted.

## Security/privacy

- [ ] Central redaction.
- [ ] No credentials in telemetry.
- [ ] No hidden chain-of-thought.
- [ ] No raw Teams bodies by default.
- [ ] No full prompts/responses by default.
- [ ] RBAC server-enforced.
- [ ] FinOps/content access separated.
- [ ] Retention implemented.
- [ ] Sampling implemented.
- [ ] Deletion/anonymization interaction documented.
- [ ] Secret-leakage tests.

## Platform health

- [ ] Exporter health visible.
- [ ] Dropped telemetry visible.
- [ ] Cost-ledger failures visible.
- [ ] Instrumentation overhead measured.
- [ ] Observability cost visible.
- [ ] Multi-instance behavior validated if applicable.

## Acceptance scenarios

### A — Successful Teams lookup
Trace shows planning -> IM -> Graph -> synthesis -> authority/provenance -> cost -> UI completion.

### B — Stuck Graph call
Current dependency and elapsed time are visible, stall emitted, timeout eventually terminal.

### C — Governed troubleshooting
Trace shows TAE -> knowledge search/selection -> ProcedureAction -> Command Authority -> provenance -> egress -> cost.

### D — Model reasoning
Non-authoritative reasoning provenance/disclosure visible; it grants no command authority.

### E — Cost anomaly
FinOps identifies affected model/agent/use-case, token/call change, estimated/billed impact and safe run references.

### F — Telemetry outage
SLOPANOC continues safely while observability degradation is itself visible and alerted.

## Final quality statement

The design qualifies as a 9.5+ production implementation only when it provides:

> **End-to-end traceability + measurable reliability + safe operational diagnosis + 100% runtime usage accounting + provider billing reconciliation + allocation + unit economics + budget/anomaly control + security/privacy + operational ownership.**


## Production backend / infrastructure

- [ ] OTel Collector deployed/configured as code.
- [ ] Traces exported to Cloud Trace.
- [ ] Metrics exported to Cloud Monitoring.
- [ ] Structured logs exported to Cloud Logging.
- [ ] Collector health monitored.
- [ ] Cloud Run infrastructure monitored.
- [ ] Cloud SQL infrastructure monitored.
- [ ] BigQuery FinOps workload monitored.
- [ ] Graph and GCS dependency telemetry present.
- [ ] Cross-instance active-run status works through Cloud SQL.

## Release/config correlation

- [ ] W3C Trace Context from browser to backend.
- [ ] Git SHA on traces.
- [ ] deployment/revision ID on traces.
- [ ] model/version recorded.
- [ ] prompt/agent contract version recorded.
- [ ] tool schema version recorded.
- [ ] TurnPlan/telemetry/cost/pricing versions recorded.
- [ ] deployment markers appear in dashboards.

## Accounting-grade FinOps

- [ ] deterministic usage_event_id.
- [ ] duplicate protection.
- [ ] durable outbox/recovery.
- [ ] correction/reversal semantics.
- [ ] late-arriving usage supported.
- [ ] component/tariff price model implemented.
- [ ] Detailed Billing + Pricing primary sources.
- [ ] FOCUS supplemental only.
- [ ] EUR reporting plus native currency retained.

## Quality/value

- [ ] governed-answer outcome available.
- [ ] operator acceptance where explicitly captured.
- [ ] troubleshooting completion/resolution/escalation available.
- [ ] action success available where trusted.
- [ ] FTC is only shown when measurable.
- [ ] cost-quality-latency joins available.

## Observability-as-code / CI

- [ ] Terraform manages supported dashboards/alerts/BigQuery/IAM/retention resources.
- [ ] Collector config source-controlled.
- [ ] CI rejects unknown telemetry vocabulary.
- [ ] CI rejects metric cardinality violations.
- [ ] CI rejects prohibited telemetry fields.
- [ ] CI validates usage-ledger path for billable model calls.
- [ ] CI validates schema/version discipline.

## Profiling

- [ ] continuous CPU profiling enabled.
- [ ] memory profiling enabled where platform-safe.
- [ ] profile data correlates with release.
- [ ] profiling overhead measured and accepted.


---

# FILE: 16_PRODUCTION_TELEMETRY_BACKEND.md


# 16 — Production Telemetry Backend and Collector Topology

## Production decision

The production topology is fixed:

```text
SLOPANOC application
        |
        | OTLP
        v
OpenTelemetry Collector
        |
        +--> Google Cloud Trace
        +--> Google Cloud Monitoring
        +--> Google Cloud Logging
        |
        +--> observability health metrics
```

FinOps uses BigQuery separately for billing, pricing, reconciliation and analytical workloads.

The application must not contain vendor-specific observability business logic beyond exporter/configuration adapters.

## Collector responsibility

The OpenTelemetry Collector is mandatory in production.

It must provide:
- OTLP ingestion,
- batching,
- retry/queueing,
- memory limiting,
- attribute filtering,
- redaction defense-in-depth,
- environment/resource enrichment,
- tail-sampling support,
- exporter health telemetry,
- bounded shutdown/flush behavior.

The Collector configuration is version-controlled as infrastructure-as-code.

## Collector processors

Production baseline must include:
- `memory_limiter`,
- `batch`,
- resource/environment enrichment,
- attribute filtering/redaction,
- tail-sampling processor when sampling is enabled.

Do not add processors that mutate semantic meaning of SLOPANOC events.

## Export destinations

### Traces
Primary: Google Cloud Trace.

### Metrics
Primary: Google Cloud Monitoring.

### Logs
Primary: Google Cloud Logging.

### FinOps
Primary runtime usage ledger: SLOPANOC durable relational store.  
Primary provider-billing analytics: BigQuery.

## Export failure behavior

A telemetry backend outage must not normally fail a user turn.

Required:
- exporter failure metric,
- dropped span/log metric,
- local bounded queue,
- retry with backoff,
- alert on sustained degradation.

Accounting-grade cost-ledger persistence has separate reliability rules in `20_FINOPS_ACCOUNTING_DURABILITY.md`.

## Semantic convention pinning

The repository must pin:
- OpenTelemetry SDK versions,
- instrumentation-library versions,
- semantic-convention version used for GenAI/HTTP/DB mapping.

Do not silently upgrade semantic-convention packages in production.

Every intentional semantic-convention upgrade requires:
- changelog,
- compatibility review,
- dashboard/query review,
- test update.

## Tail sampling production policy

Initial stabilization:
- 100% trace capture.

After stabilization, production tail sampling must retain 100% of:
- failed turns,
- timeout turns,
- stalled turns,
- safety events,
- governed operational turns,
- command-authority/approval flows,
- cost anomalies over configured threshold.

Healthy general-chat turns may be sampled at 10–20%.

Usage/cost ledgers and aggregate metrics are never sampled.

## Collector health

Monitor:
- accepted spans,
- exported spans,
- refused spans,
- dropped spans,
- queue size,
- retry count,
- memory pressure,
- exporter failures,
- collector CPU/memory,
- scrape/export latency.

Collector failure must appear in the SLOPANOC Health dashboard.


---

# FILE: 17_INFRASTRUCTURE_OBSERVABILITY.md


# 17 — Infrastructure Observability

## Scope decision

Production observability includes all major SLOPANOC infrastructure and dependencies:

- Cloud Run,
- Cloud SQL,
- Vertex AI / Gemini,
- Microsoft Graph,
- BigQuery,
- Google Cloud Storage,
- knowledge retrieval/vector/search infrastructure,
- network/external API dependencies,
- OpenTelemetry Collector,
- browser/SSE delivery path.

Application telemetry alone is not sufficient.

## Cloud Run

Monitor:
- request count,
- request latency,
- instance count,
- concurrency,
- CPU utilization,
- memory utilization,
- container startup/cold start,
- instance startup failures,
- 4xx/5xx,
- container restarts,
- minimum-instance utilization where configured.

Correlate deployments to service/revision identity.

## Cloud SQL

Monitor:
- CPU,
- memory,
- active connections,
- connection utilization,
- connection errors,
- storage utilization,
- disk latency/IOPS where available,
- transaction/query latency,
- lock/deadlock indicators,
- SQLAlchemy pool wait time,
- pool exhaustion,
- failed persistence.

Application DB spans complement platform metrics.

## Vertex AI / Gemini

Monitor application-visible:
- request rate,
- model,
- latency,
- TTFT,
- tokens,
- retries,
- rate limits,
- provider errors,
- timeout,
- cost.

Also ingest provider/platform service health where available.

## Microsoft Graph

Monitor:
- request rate,
- latency,
- 401/403,
- 404,
- 429,
- 5xx,
- retry count,
- pagination,
- timeout,
- auth/permission changes.

Never emit access tokens or message bodies.

## BigQuery

For FinOps/observability workloads track:
- query count,
- bytes processed,
- slot/resource use where available,
- query latency,
- failed queries,
- storage volume,
- cost of observability/FinOps queries.

Dashboard queries use curated views, not uncontrolled raw scans.

## Google Cloud Storage

Monitor:
- request rate,
- latency,
- failures,
- bytes uploaded/downloaded,
- storage growth.

## Knowledge subsystem

Monitor:
- sparse retrieval latency,
- dense retrieval latency,
- embedding latency/cost,
- candidate count,
- zero-result rate,
- relevance/fusion stages,
- applicability failures,
- DB/vector-provider failures.

## Network/external dependencies

Instrument every external HTTP boundary with:
- dependency name,
- duration,
- status class,
- timeout,
- retry,
- connection error.

## Infrastructure dashboard

Must provide:
- current saturation,
- latency,
- errors,
- dependency health,
- deployment/revision correlation,
- capacity trend.

## Capacity alerts

At minimum:
- Cloud Run sustained CPU/memory/concurrency saturation,
- Cloud SQL connection/storage thresholds,
- Collector queue/memory saturation,
- BigQuery cost/query anomaly,
- provider throttling/rate-limit spike.


---

# FILE: 18_STRUCTURED_LOGGING_STANDARD.md


# 18 — Structured Logging Standard

## Production decision

All production application logs are structured JSON and exported to Google Cloud Logging.

Logs must correlate with:
- `trace_id`,
- `span_id`,
- `run_id` where available.

Plain unstructured production logs are not the primary logging format.

## Canonical JSON fields

Required:

```text
timestamp
severity
service
environment
message
event_name
error_code
trace_id
span_id
run_id
```

Conditional:
- agent,
- tool,
- dependency,
- stage,
- model,
- duration_ms,
- retry_count.

Prohibited:
- access tokens,
- auth headers,
- secrets,
- full prompt/response text,
- full Teams content,
- chain-of-thought.

## Severity mapping

- DEBUG — developer detail, normally reduced in production.
- INFO — lifecycle/expected operational transitions.
- WARNING — degraded/retried/recoverable condition.
- ERROR — failed operation requiring investigation.
- CRITICAL — safety/security/data-integrity incidents.

A source gap or command rejection is not automatically ERROR; classification depends on whether it is expected policy behavior.

## Exception ownership

One boundary owns the detailed exception log.

Higher layers:
- add context via trace events,
- do not emit duplicate full stack traces.

## Cloud Logging requirements

Configure:
- structured field parsing,
- trace correlation,
- log-based metrics only when not duplicating OTel metrics,
- retention policy,
- exclusion filters for noisy low-value logs where safe.

## Logging vs tracing

Logs answer textual operational events/errors.  
Traces answer causal flow and latency.  
Metrics answer aggregate health.

Do not duplicate identical payloads across all three.

## Log retention

Default production retention:
- 30 days for normal application logs unless enterprise policy requires otherwise,
- security/audit logs follow security policy,
- raw conversation content is not retained in logs.

## Logging tests

CI must detect:
- JSON schema violations,
- prohibited field names,
- fake-secret fixtures appearing in exporter payloads,
- raw exception duplication in known critical paths.


---

# FILE: 19_AI_QUALITY_OUTCOMES.md


# 19 — AI Quality and Operational Outcome Telemetry

## Purpose

FinOps unit economics must be joined to quality and operational value.

Cost without quality/outcome context is insufficient.

## Required quality/outcome model

Create a durable outcome schema that can represent:

### Answer quality
- governed_answer,
- source_coverage_status,
- model_reasoning_disclosed,
- operator_acceptance where explicitly available,
- correction/retry requested,
- escalation requested.

### Troubleshooting
- troubleshooting_started,
- troubleshooting_completed,
- incident_resolved,
- escalated,
- diagnostic_steps_count,
- first_time_correct where measurable,
- operator_action_success.

### Teams / Incident Manager
- evidence_found,
- evidence_sufficient,
- chat_resolution_success.

### Automation / AOE
- artifact_generated,
- artifact_accepted,
- artifact_validation_status,
- execution_success where applicable.

## Do not fabricate quality

If the system does not have an authoritative outcome signal:
- store `unknown`,
- do not infer success from absence of complaint.

## First-time-correct

FTC must have an explicit operational definition.

Initial definition:
A turn/case is first-time-correct only when the requested operational objective is completed without:
- correction of the primary answer,
- invalid governed action,
- re-planning caused by incorrect system output,
- operator-declared incorrect result.

If a reliable authoritative FTC signal does not yet exist, expose FTC as unavailable rather than guessed.

## Quality-cost joins

Required analytical joins:
- cost vs completion,
- cost vs governed-answer status,
- cost vs FTC,
- cost vs resolution,
- latency vs outcome,
- model version vs outcome,
- deployment version vs outcome.

## Outcome ownership

Outcome updates come from:
- server-owned workflow state,
- explicit operator feedback,
- trusted downstream action/result state.

Never from hidden model self-evaluation alone.


---

# FILE: 20_FINOPS_ACCOUNTING_DURABILITY.md


# 20 — FinOps Accounting-Grade Durability

## Production decision

The usage/cost ledger is accounting-grade, append-oriented, idempotent and unsampled.

A model call must not disappear financially because a trace was sampled or an exporter failed.

## Deterministic usage event identity

Every billable provider operation gets deterministic `usage_event_id`.

Recommended composition:
- run_id,
- stable call sequence or call UUID generated before provider invocation,
- provider,
- operation.

Database enforces uniqueness.

Retries are separate usage events when they incur provider usage.

## Write semantics

Preferred:
1. create call identity before provider request;
2. execute provider call;
3. receive usage;
4. write immutable ledger event;
5. roll up turn summary asynchronously or transactionally as appropriate.

If ledger persistence fails:
- record `COST_LEDGER_PERSIST_FAILED`,
- retry via durable outbox/recovery path,
- do not silently discard usage.

## Outbox/recovery

Use a durable recovery mechanism for ledger persistence failures.

Acceptable:
- transactional outbox in Cloud SQL,
- equivalent durable queue.

In-memory retry alone is insufficient.

## Immutable corrections

Do not overwrite historical usage records after reconciliation.

Corrections use:
- correction/reversal event,
- reference to original usage event,
- reason,
- actor/process,
- timestamp.

## Late-arriving usage

Support:
- provisional estimated usage,
- later provider-reported usage,
- reconciliation state,
- adjustment event.

## Price model

Use component/tariff rows, not only fixed input/output columns.

### `finops_price_component`

```text
price_component_id
price_version
provider
service
sku
model
region
charge_type
modality
tier_lower_bound
tier_upper_bound
unit
unit_price
currency
effective_from
effective_to
source
```

`charge_type` examples:
- input_token,
- output_token,
- cached_input_token,
- cache_write,
- request,
- image,
- audio,
- batch,
- embedding,
- storage,
- compute.

## Billing source decision

Primary production accounting inputs:
1. GCP Detailed Billing Export.
2. GCP Pricing Export/catalog.

FOCUS export:
- supplementary normalized cross-provider dataset,
- not the sole authoritative source until lifecycle/governance explicitly approves it.

## Reconciliation states

```text
UNRECONCILED
PARTIALLY_RECONCILED
RECONCILED
VARIANCE_REVIEW
CORRECTED
```

## Coverage controls

Metrics:
- provider calls observed,
- usage events persisted,
- missing usage count,
- outbox backlog,
- reconciliation lag,
- unallocated billed cost.

Target:
- effectively 100% usage-ledger coverage.

Any gap is visible.


---

# FILE: 21_SLO_FORMULAS_AND_INITIAL_TARGETS.md


# 21 — SLO Formulas and Initial Production Targets

## Principle

Definitions are fixed now. Numeric targets are provisional and recalibrated after a production baseline.

## Measurement window

Initial:
- rolling 28-day SLO window,
- operational dashboards also show 1h / 6h / 24h / 7d.

## Accepted valid request

A denominator-eligible request:
- passes authentication/authorization,
- passes request schema validation,
- enters canonical turn execution.

Excluded:
- rejected malformed requests,
- explicit client cancellation before execution begins,
- synthetic requests from business unit economics.

## Availability SLI

Numerator:
turns ending in a valid terminal application outcome:
- completed answer,
- valid clarification,
- valid source gap,
- valid approval request,
- safe policy rejection.

Denominator:
accepted valid requests.

Infrastructure/model/provider failures are not successful.

Initial target:
- 99.5%.

## Terminal completion SLI

Numerator:
accepted requests reaching any explicit terminal state within maximum turn deadline.

Denominator:
accepted valid requests.

Initial target:
- 99.9%.

## Latency SLOs

Measure server end-to-end duration from API accepted to terminal server completion.

### General
Initial p95 <= 8 s.

### Teams lookup
Initial p95 <= 20 s.

### Governed troubleshooting
Initial p95 <= 30 s.

### Complex multi-agent
Initial p95 <= 45 s.

Targets are provisional and recalibrated from real baseline without weakening safety.

## First-token SLO

For streaming model-authored turns:
initial p95 TTFT <= 5 s.

Server-rendered clarification/source-gap responses are excluded from model TTFT.

## Trace completeness SLI

Numerator:
terminal turns containing all required trace phases applicable to that turn.

Denominator:
terminal turns.

Initial target:
- 99.9%.

## Cost-ledger completeness SLI

Numerator:
billable provider operations with exactly one persisted accounting usage event.

Denominator:
billable provider operations observed by provider-call instrumentation.

Target:
- 100% design target;
- alert if < 99.99% over 24h.

## SSE delivery SLI

Numerator:
server `message.completed` events observed by browser completion telemetry when browser remains connected.

Denominator:
eligible connected SSE turns.

Initial target:
- 99.9%.

## Model reliability SLI

Numerator:
provider calls completing successfully.

Denominator:
provider calls excluding explicit client cancellation.

Initial target:
- 99.5%, recalibrated by provider/model.

## Dependency reliability

Track separately for:
- Graph,
- DB,
- storage,
- knowledge.

Initial target:
- 99.5% successful operations per dependency class, subject to baseline.

## Safety SLIs

Zero tolerance:
- unauthorized command exposure,
- command egress bypass,
- credential/secret telemetry leak,
- hidden chain-of-thought persistence.

Any occurrence = incident, not error-budget consumption.

## Error budget

```text
allowed_bad = eligible_events * (1 - SLO_target)
consumed_budget = observed_bad / allowed_bad
remaining_budget = 1 - consumed_budget
```

Zero-tolerance safety SLIs use incident semantics instead.

## Burn-rate alerts

Implement multi-window burn-rate alerts after metrics pipeline is live.

At minimum:
- fast burn: 1h/6h,
- slow burn: 6h/3d or equivalent.

Exact factors are calibrated during SRE rollout.


---

# FILE: 22_RELEASE_CONFIG_CORRELATION_CI.md


# 22 — Release, Configuration Correlation and CI Enforcement

## Production decision

Every trace must be attributable to the exact software/configuration that produced it.

## Required release attributes

Per process/resource:
- service version,
- Git SHA,
- deployment/release ID,
- Cloud Run revision,
- environment,
- region.

Per turn where applicable:
- model name/version,
- agent contract version,
- prompt/system-instruction version,
- tool schema version,
- TurnPlan schema version,
- telemetry schema version,
- cost schema version,
- pricing version,
- feature/config version.

## Version strategy

Prompt/agent/tool contracts require explicit version identifiers when changes can affect:
- quality,
- routing,
- tool calling,
- safety,
- cost,
- latency.

A hash of normalized configuration is acceptable where semantic versioning is impractical.

## Change-correlation dashboards

Support:
- latency by deployment,
- failures by Git SHA,
- cost by model/prompt version,
- quality by release,
- planning failure by tool-schema version.

## W3C Trace Context

Browser -> backend propagation uses W3C Trace Context:
- `traceparent`,
- `tracestate` where needed.

Do not put user/session/customer secrets in baggage.

The backend remains authoritative for `run_id`.

## CI observability gates

CI fails for:

### Unknown telemetry vocabulary
- new unregistered stage/status/error code.

### Cardinality violations
- run/session/user/chat/case/fault IDs used as metric labels.

### Redaction violations
- prohibited field names,
- fake-secret fixtures appearing in exporter payloads.

### Span lifecycle
- known execution path leaves unclosed spans.

### Model usage accounting
- instrumented billable model call lacks usage-ledger path.

### Schema/version discipline
- telemetry schema changed without version/migration;
- price schema changed without compatibility update.

## Deployment annotations

Every deployment emits a dashboard/trace marker containing:
- release ID,
- commit,
- timestamp,
- environment.

## Rollback analysis

Runbooks support:
- compare pre/post release latency,
- compare pre/post model cost,
- compare error/SLO burn,
- compare quality/outcome metrics.


---

# FILE: 23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md


# 23 — Terraform and Observability as Code

## Production decision

Observability infrastructure is managed as code.

Terraform is the standard for cloud resources and observability configuration where supported.

Manual console configuration is not the system of record.

## Terraform scope

Manage where supported:
- OTel Collector deployment/config dependencies,
- Cloud Monitoring dashboards,
- alert policies,
- log sinks/exclusions,
- BigQuery datasets/tables/views,
- billing export destination dependencies,
- service accounts/IAM,
- retention policies,
- budget alerts where supported,
- Cloud Run observability environment/config wiring.

If a resource cannot be managed by Terraform:
- store declarative config/script in repo,
- document manual bootstrap,
- add drift-review process.

## Repository layout

Recommended:

```text
infra/
  observability/
    terraform/
    collector/
    dashboards/
    alerts/
    bigquery/
```

Exact layout may follow existing infrastructure conventions.

## Environments

Separate:
- dev,
- staging,
- production.

No production dashboard/alert points to dev resources.

## Promotion

Changes follow:
- code review,
- plan,
- apply,
- validation.

Dashboard/alert changes are reviewed like application code.

## Secrets

Terraform must not store provider secrets in plaintext config.

Use approved secret-management patterns.

## Drift

Scheduled drift detection identifies:
- manual dashboard edits,
- alert changes,
- IAM drift,
- retention drift.


---

# FILE: 24_CONTINUOUS_PROFILING.md


# 24 — Continuous Profiling

## Production decision

Continuous backend CPU/memory profiling is included in the full observability build.

It complements traces, metrics and logs.

## Goals

Detect:
- CPU regressions,
- memory growth/leaks,
- expensive serialization,
- high-cost instrumentation code,
- pathological retrieval/model-preparation paths,
- backend performance changes not obvious from span latency alone.

## Requirements

Use a production-safe profiling mechanism compatible with deployment policy.

Profile at minimum:
- CPU,
- heap/memory where supported.

Correlate profiles with:
- service version,
- Cloud Run revision,
- environment.

Do not include:
- raw prompt contents,
- secrets,
- customer payloads.

## Overhead budget

Profiling overhead is measured independently.

If profiling creates unacceptable overhead:
- reduce sampling frequency,
- do not disable all profiling without documenting evidence.

## Dashboard integration

Show:
- CPU profile comparison by release,
- top hot paths,
- memory trends,
- observability instrumentation overhead where identifiable.

## Validation

Load tests run:
- profiling off,
- profiling on,

and compare:
- CPU,
- memory,
- p95 latency,
- throughput.
