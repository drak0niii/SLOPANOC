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
26. `25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md`
27. `26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md`

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

## Observability Console + Separate Read Plane — architecture workstream

[25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md)
is authoritative for the primary operational route `/resources/observability`,
configuration-only Settings and an independently deployable GET-only read API.
It supersedes historical Settings-first dashboard placement; existing M7–M15
security, telemetry and financial semantics remain authoritative. Introduced after
the M16 baseline: documentation only, not M17. M16 C41/C42 remain FAIL and final
CPU/regression acceptance must be rerun after integration.

## Hybrid event-driven architecture — 2026-10-11

Read [26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) for the approved two-lane architecture and authoritative Mermaid.
Critical audit/accounting remains Cloud SQL durable with same-transaction outbox;
operational telemetry stays async OTLP -> Collector, including an optional sanitized
Pub/Sub branch. A separate publisher/projector supplies derived read models; the
separate GET-only read plane is preserved. No duplicate business/accounting truth.
The [resource manifest](GCP_HYBRID_OBSERVABILITY_RESOURCE_MANIFEST.md) lists future
Phase 1/2/optional resources; no provisioning/migration is authorized or executed.
Local Phase-1 owner-transaction capture, SQL outbox, bounded publisher, fake
transport, projector, durable projections and GET/DTO contracts are implemented
and locally validated. Production adapter/verified ingress and PostgreSQL/cloud
validation remain pending. Operational Telemetry Pub/Sub is OPTIONAL / SCALE-TRIGGERED,
disabled and curated only after measured need. M16 INCOMPLETE,
C41/C42 not PASS, M17 UNSTARTED. Doc 26 supersedes synchronous ANOC read assumptions.

## Phase-1 production-readiness update — 2026-10-11

Production-readiness local implementation now adds real opt-in ANOC/Case composition, lazy Google Pub/Sub transport, authenticated push, and review-only Terraform/SQL. Read [coverage](AUDIT_COVERAGE_MATRIX.md) and [production readiness](HYBRID_PHASE1_PRODUCTION_READINESS.md). No deployment/shared migration; PostgreSQL rehearsal unavailable; full auditability and M16 acceptance remain incomplete.

Audit completeness/rehearsal follow-up: [owner gap design](AUDIT_OWNERSHIP_GAP_DESIGN.md),
[human reader identity contract](HYBRID_READER_IDENTITY_CONTRACT.md),
[recovery requirements](HYBRID_RECOVERY_REQUIREMENTS.md),
[PostgreSQL 18 rehearsal evidence](validation/HYBRID_POSTGRES_REHEARSAL.json).
Full auditability NO; no new owners/events because material business semantics require
design resolution; local PostgreSQL PASS; M16 INCOMPLETE; provisioning NO.

## Execution journal status — 2026-10-11

Read document 27 for the locally implemented Execution Audit Journal and Controlled Action Egress. Current coverage and ownership-gap documents supersede earlier design-only stop classifications. Full auditability NO; M16 INCOMPLETE; no external changes. See [implementation](27_EXECUTION_AUDIT_JOURNAL_AND_CONTROLLED_EGRESS.md), [coverage](AUDIT_COVERAGE_MATRIX.md) and [remaining gaps](AUDIT_OWNERSHIP_GAP_DESIGN.md).
