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

## Console/read-plane workstream integration (planned, not implemented)

See [25](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md) for the inspected source audit.
Planned `backend/api/observability_app.py` uses FastAPI with an independent reader
lifespan, shared GET handlers/security/read facades, and no business app/model
warmup or producer workers. Split the mixed `observability_routes.py` composition;
SSE receipts stay on ANOC. Reuse repository reads through read-only dependencies.

Frontend integration points: `src/App.tsx`, `src/lib/routes.ts`, existing
`AppShell`/`Sidebar` and Resources navigation, shared API transport/config,
`src/api/observability.ts`, `observabilityPolicy.ts`, `finops.ts`, and existing
Settings observability panels/contracts/polling. Add the independent read base
without changing chat/SSE receipt routing or shared conversational state.
Move operational panels rather than duplicate them; retain configuration in Settings.
These are future targets, not files changed by this documentation pass.

## Hybrid scaffolding and future transaction integration

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) places inert contracts in `backend/observability/hybrid/`, dormant Collector
specification in `infra/observability/collector/hybrid-branch.yaml.example` and
local no-op composer in `scripts/observability/hybrid_collector_config.py`.
No business request/lifespan imports these modules. Future integration must locate
actual critical transaction owners (including provider admission/usage and approval/
dispatch persistence), add atomic outbox writes and use separate publisher/projector
entrypoints. Existing M7 Coordinator is operational/coalescing, not audit authority.
Existing GET-only read app/service and UI are reused; no parallel executor/ledger.
