# 25 — Observability Console and Separate Read Plane

## Decision, scope and precedence

Introduced 2026-10-10 after the M16 validation baseline. This is a separate
architecture workstream, **not M17**. The initial documentation pass was approved;
local implementation is now explicitly authorized and recorded below. Cloud
mutations remain unauthorized. Historical architecture audit statements describe
the pre-implementation baseline.

The complete Observability & FinOps experience lives at `/resources/observability`
in the **Resources-site shell**, using the existing top navigation/branding and
full-width dark operations layout. The explicit 2026-10-11 user correction moves
both dashboards and configuration/policy/administration here. `/app` Settings has
no Observability entry, mount, configuration view or console CTA; the console has
no ANOC sidebar, Settings modal or workspace overlay. The site header's **Open app**
returns to `/app`. This supersedes earlier app-shell and configuration-in-Settings
placement throughout the historical build pack. It does not authorize backend
redesign, new financial truth, governance changes or progression to M17.

**OBSERVABILITY ONLY — NEVER EVIDENCE.** Existing M7–M15 backend semantics remain
authoritative. No redesign of tracing, SLO formulas, ledger, reconciliation,
allocation, unit economics, budgets, forecasts, anomalies, sampling, retention,
archive or RBAC. Docs 09, 16, 20 and 21 retain their security/storage/accounting/SLO
contracts. This document governs console placement and runtime separation.

M16 remains incomplete: C41 FAIL, C42 FAIL, Ready for Next Milestone NO. Final M16
CPU and regression acceptance must be rerun after integration; separation must not
bypass the existing CPU defect, alter its threshold or move measured work outside
its acceptance scope. Historical results remain unchanged.

## Current local architecture audit

Audit is of the working tree, including uncommitted M16 changes, not remote GitHub.

| Inspected source | What exists and consequence |
|---|---|
| `backend/api/app.py` | FastAPI `create_app` mounts the observability router alongside session/chat/approval/execution and other business routes. Its lifespan owns telemetry, projection, SLO writer, accounting, optional billing ingestion, reconciliation, unit-economics and controls workers, and model warmup. Do not import this app factory or reuse its lifespan for the reader. |
| `backend/api/observability_routes.py` | Protected GETs cover runs, active runs, timelines, session runs, health/config, policies/access, SLOs, runtime usage, financial sources, reconciliation/allocation, unit economics, budgets/forecasts/anomalies, run retention and archive reconstruction. The same router also contains POST `/sse-receipts`: mounting it wholesale violates the new boundary. |
| `backend/observability/service.py` | Shared capability/environment admission, bounded reads, cursor scope and minimized views. Health includes execution-local coordinator/exporter counters; absent reader workers cannot mean zero ANOC failures. |
| `backend/observability/authorization.py`, `schemas.py` | Verified `Principal`, canonical role ceilings, capabilities and environment scope. `verified_principal_provider` currently returns `None`: protected access defaults denied. No live production identity integration is established by this audit. |
| `backend/api/identity.py` | Development user header is unverified and denied outside local/development/test. It is not a substitute for the protected verified-principal seam. |
| `backend/observability/database.py` | Inert bounded SQLAlchemy async pool, PostgreSQL required except explicit isolated tests. No schema creation at construction. Current app pools use the existing resolved database URL. |
| `backend/observability/slo_service.py` | Bounded M9 snapshots and existing evaluator; GET can publish local result metrics. Reader must not become a second authoritative SLO writer. |
| `backend/observability/finops/*_service.py` | Reads published repositories/caches; M10 facade currently takes a ledger-shaped dependency, including process-local admission counters. Read adapters must expose durable truth without constructing a running ledger or financial worker. |
| `src/api/client.ts`, `config.ts`, `observability.ts`, `observabilityPolicy.ts`, `finops.ts` | Observability, policy and financial clients use the common business API base. No independent observability base exists yet. Shared error handling/cancellation and decoders should be reused. |
| `src/components/shell/settings/observability/` | Existing operational, financial and effective-policy panels, timeline/run views, decoders, status/read states and bounded polling hook. Overview still has historical M9/M10 placeholder text; layout migration must audit actual availability instead of copying stale claims. |
| `src/App.tsx`, `lib/router.tsx`, `ProductApp.tsx`, `components/shell/AppShell.tsx` | Existing History API router, lazy product boundary and shared state above route switch; AppShell combines Sidebar/MainWorkspace. `/resources/observability` is not currently routed. Preserve chat/project/task state during navigation. |
| `src/data/landingNav.ts`, `components/shell/Sidebar.tsx` | Resources hierarchy and product navigation are existing integration points. `/resources/changelog` is a resource link, not an observability route. |

Existing local tests and M16 receipts are historical evidence of their own gates,
not validation of this proposed reader or console. Complete Cloud Monitoring trend,
per-agent performance and alert-history contracts are not established by the
inspected GET inventory; expose unavailable until a bounded authoritative source
is mapped. Existing timelines are summaries, not proof of retained full spans.

## Target planes and flow

```text
User/business traffic -> ANOC business + agent runtime
                          | durable telemetry / published observability
                          v
             Observability data plane
             Cloud SQL summaries / SLO rollups / usage ledger
             published M11–M14 financial results
             Cloud Trace / Monitoring / Logging / exceptional archive
                          |
                          v
             Independently deployed Observability Read API
             verified identity + capabilities + environment scope
             bounded GET reads and protected archive reconstruction
                          |
                          v
             /resources/observability in existing SLOPANOC shell
```

ANOC remains the sole owner of the canonical turn pipeline, Team Manager,
specialists, tools, model requests, command/approval paths, business persistence,
SSE, telemetry producers and accounting emitters. Existing producers/workers
continue publishing outside dashboard requests. Reader consumers never send
telemetry back into intent, evidence selection, provenance or authority.

## Read-plane entrypoint and boundary

Planned entrypoint: `backend/api/observability_app.py`, using FastAPI and the existing
SafeError contract. This file does not exist yet. Its independent factory/lifespan
creates only validated reader configuration, bounded read pools, read facades,
protected archive reader and minimal reader-service health instrumentation.
It does not load the business app, model warmup, ADK runners, business session
services, coordinator, ledger admission/recovery, ingestion/reconciliation/unit/
controls workers, retention jobs, archive writer or remediation.

Refactor route composition to share existing GET handlers/dependencies in a read
router and keep SSE receipt ingestion in a separate ANOC router. The reader mounts
an explicit reviewed GET allowlist, never all business GETs or a whole mixed router.
POST `/api/observability/sse-receipts` stays on the ANOC API and retains ownership
verification/M9 semantics. Console clients cannot redirect receipt writes to the
read base URL. Reject POST/PUT/PATCH/DELETE, even for observability namespaces.
CORS preflight OPTIONS is transport negotiation only; HEAD, if supported, has the
same authentication/scope/response minimization as GET.

| Reused GET family under `/api/observability` | Existing gate / source |
|---|---|
| `/runs`, `/active`, `/runs/{run_id}`, `/runs/{run_id}/timeline`, `/sessions/{session_id}/runs`, `/runs/{run_id}/retention` | `OPERATIONAL_METADATA`, authorized environments; M7 repository and M15 retention views |
| `/health`, `/slos`, `/slos/{slo_id}` | Operational metadata; durable run/SLO/accounting snapshots and existing M9 evaluator |
| `/config`, `/policies` | `EFFECTIVE_CONFIGURATION`, environment-scoped effective producer policy |
| `/access` | Verified principal; own capabilities/environments only |
| `/finops/runtime-usage`, `/finops/ledger-health`, `/finops/ledger-completeness` | `FINANCIAL_DATA`; unsampled durable M10 usage/health, same bounded grouping and evaluator |
| `/finops/billing-health`, `/finops/pricing-status`, `/finops/billing-summary` | `FINANCIAL_DATA`; M11 published source projections |
| `/finops/reconciliation[/{period}]`, `/finops/allocation` | `FINANCIAL_DATA`; M12 published results |
| `/finops/unit-economics[/{unit_id}]` | `FINANCIAL_DATA`; M13 published results |
| `/finops/budgets[/{budget_id}]`, `/finops/forecasts[/{generation_id}]`, `/finops/anomalies[/{anomaly_id}]` | `FINANCIAL_DATA`; M14 published generations/history |
| `/archive/{trace_id}` with validated digest/page | `TECHNICAL_DIAGNOSTICS` and explicit environment scope; existing M15 reconstruction bounds |

Bracket notation above describes route families, not new endpoint syntax.
Reuse existing financial read services and formulas. Small read dependency adapters
may replace writer-shaped handles, but must not duplicate calculation/security logic.
GET never starts billing queries/ingestion, pricing refresh, reconciliation,
allocation recomputation, forecasting, anomaly jobs or retention cleanup. Bounded
aggregation of published M10 minute buckets and existing M9 snapshot evaluation
are compatible; avoid GET-driven publication of authoritative SLO state.
No React financial/SLO/retention formulas. Stale or missing published generations
remain stale/unavailable; no synchronous repair or substituted estimates.

Preserve existing deadlines (typically 2 seconds), query windows, page/row limits,
archive byte/span limits, cursor security/scope and safe error mapping. New reads
must be bounded too. Archive reconstruction uses a separate bounded admission
lane so large authorized traces cannot exhaust ordinary reader requests. Do not
broaden object/URL selectors, allow bulk export, return signed public URLs, or
retry business execution to fill missing spans.

## Security, identity and origins

Reuse `verified_principal_provider`, `Principal.valid`, `require`, role ceilings,
environment authorization, financial/technical field minimization and existing
privacy/archive validators. No duplicated role map, client role trust or implicit
FinOps access to technical traces/content. Visible navigation is not authorization.
Unavailable identity means denied access, including local default; isolated tests
may inject verified principals using the existing dependency seam.

Production integration requires a shared server-verified authentication adapter at
that seam. Exact issuer/audience/session transport and credential policy are an
implementation/deployment prerequisite, not inferred from the dev identity header.
No OAuth/IAM changes are authorized here. Revalidate capabilities and environment
on every request; session expiry/revocation clears mounted protected UI data.

Planned `SLOPANOC_OBSERVABILITY_ALLOWED_ORIGINS` is an explicit origin allowlist,
empty by default. No wildcard for protected production APIs; validate exact
scheme/host/port and reject wildcard/null/malformed origins. CORS permits only
necessary GET/preflight headers, including verified auth and W3C context if used.
Credentialed cookies require explicit origins, established secure cookie/CSRF
policy and approved transport; never set credential forwarding automatically.
CORS is not an authentication or environment gate. Disable/restrict production
OpenAPI/docs exposure according to existing secure deployment policy.

## Infrastructure, database and failure isolation

Production target: separate Cloud Run ANOC and Observability API services, with
independent entrypoints, service identity, CPU/memory/concurrency, autoscaling,
request/admission limits, health checks and deployment/rollback. UI stays in the
existing product deployment; no second frontend shell is required. These are
deployment targets, not created resources.

Planned `VITE_OBSERVABILITY_API_BASE_URL` routes ALL console/policy/financial GETs
to the reader service via shared transport/decoder/error helpers. Validate/normalize
the configured base; no hard-coded localhost. Unset local configuration may use
the existing API base/same-origin development proxy. Production must explicitly
bind an independent URL (or gateway upstream resolving only to the reader); never
silently fall back to ANOC workers on read-service failure. Vite settings are
public and must not contain credentials. SSE receipts/chat/business calls retain
the business base. DNS/routing/auth/CORS changes need exact approval later.

Planned `SLOPANOC_OBSERVABILITY_READ_DATABASE_URL` selects a separate read pool.
Local isolated/test use may explicitly reuse the existing DB configuration;
production requires an explicit reviewed binding, no automatic broad writer
credential fallback. Target a dedicated read-only database principal on required
observability tables/views where feasible, minimum GCS archive object-read and
approved read-only telemetry API permissions. Do not create principals/users,
secrets, grants, schemas or replicas in this pass. Reader startup never migrates.

Reuse existing repositories behind read-only interfaces, bounded pool sizes,
statement/transaction deadlines and PostgreSQL read-only transactions as defense
in depth; database grants are the enforcement boundary. No write recovery jobs,
DDL, mutation methods or writer objects in reader lifecycle. Shared DB does not
provide full isolation: cap total reader connections at maximum replicas, bound
query scans, monitor contention and reserve ANOC capacity. A future replica or
additional read model is optional only after consistency/lag/cost review; do not
claim it exists or create one by implication.

Reader liveness reports its process; readiness reports its configured read sources
without leaking sensitive bindings. Protected health distinguishes reader-service
health from ANOC producer/exporter/Collector health. Obtain producer status from
published authorized sources; absent remote observations mean unknown/unavailable,
never zero backlog, healthy export or 100% completeness. Effective configuration
must identify the source runtime/environment/policy version; reader exporter
settings cannot be presented as ANOC policy. Existing DTOs may need compatible
extensions for scope/freshness/unknown values, with contract version/decoder tests.

ANOC has no synchronous dependency on reader availability. Console polling never
consumes ANOC application workers in the isolated production topology. Reader
outage/overload renders unavailable with bounded retries and does not trigger
business replay or an ANOC fallback. Preserve bounded visibility-aware polling,
one completion-scheduled request per mounted section, cancellation on navigation,
no hidden-tab polling, revocation clearing, and no persistent protected browser
cache. Independent service CPU does not eliminate shared DB/storage contention.

## Console information architecture and source mapping

Use the existing layout, typography, spacing, navigation, components and interaction
model: dense dark operator console, compact cards, thin borders, restrained
status colors/elevation, responsive tables and small source-backed charts. No
excessive neon/futuristic styling. Reuse `ReadState`, `StatusBadge`, `RunTable`,
`RunDetail`, `Timeline`, protected decoders and financial panels after inspection.
Reuse the Resources site header/top navigation; retain AppState above the route
switch so returning to ANOC preserves conversational continuity. ProductApp and
its Sidebar/Settings are not mounted by the Resources console.

| Section | Content and authoritative source / explicit gap |
|---|---|
| Overview | Availability, P95 Latency, Error Rate, Throughput, Actual Cost KPI row. M9 SLO snapshots and M11/M14 actuals only where matching window/environment/coverage exist. P95/rate/throughput trends need an actual bounded source; do not infer from a paginated or sampled run list. Trace activity, run status, execution timeline, authorized alerts and available release/config context. |
| Traces | Safe run list: status, request class, durations, available participating agents/model/tool counts, authorized cost, timestamps. Existing M7 summaries/timeline and M15 protected archive, plus independently confirmed Cloud Trace availability. Hierarchical waterfall only from real parent/span timings; do not manufacture missing children from summary events. |
| Agents | Team Manager, Technical Authority Engineer, Incident Manager, Problem Manager, Automated Operations Engineer. Requests/latency/failure/model/tool metrics only with authoritative agent aggregates; M10 supported agent usage/direct estimates and M13 financially valid published costs. Fully loaded cost only when allocation supports that dimension. Governed rate/clarification/procedure activity only when measured. Unavailable otherwise. |
| Reliability | Existing M9 availability, terminal completion, latency classes, TTFT, trace/SSE completeness, dependency reliability and accounting completeness; separate instrumentation, Cloud Trace and archive source states. |
| SLOs | Existing M9 targets/current/burn state/freshness and definitions; preserve unknown denominators and unavailable sources. No new browser evaluator. |
| FinOps | M10–M14 runtime usage, actual cost, forecast, configured budget, server-produced utilization, unallocated cost, billing/pricing, reconciliation/allocation, unit economics, budgets/forecasts/anomalies. Model/provider/agent/workload/request-class breakdowns only where supported and financially valid. Separate estimate/billed values, native currency/EUR, generation, coverage and freshness. |
| Alerts | Existing authoritative SLO breach/latency/error burn state, projected budget overrun, cost/unallocated-cost anomaly and source degradation. Respect operational vs financial capabilities independently. Label derived source states honestly; alert delivery/history requires a real source and is unavailable otherwise. No remediation controls. |
| Diagnostics | Existing permitted instrumentation completeness, Cloud Trace retained, exceptional archive retained, intentionally not retained, source stale/archive degraded, Collector/config status; technical details require the technical capability. Missing trace retention is not execution failure. |

Call the trace event view **Agent Execution** or **Execution Timeline**, never
“Reasoning Steps”. Show only safe server-owned classification, Team Manager
orchestration, specialist invocation, retrieval, provider call, approval-required,
tool-completed and response-completed events when actually present. No private
reasoning, full prompts/responses, raw tool results, Teams/knowledge bodies or
invented hallucination/confidence/reasoning-quality scores.

All KPIs/charts include source window/environment, freshness and meaningful units;
missing values render unavailable, not zero or synthetic sparkline data. Display
financial decimals from server DTOs without client-side financial arithmetic.
Access to one section never implies access to another; unauthorized details and
costs must be omitted by the server and cleared on revocation.

## Resources shell and complete UI migration

1. `/resources/observability` and its section routes mount a Resources page directly,
   reusing the existing LandingHeader/DesktopNav/MobileNav/BrandMark. No ProductApp,
   ANOC sidebar, Settings modal, new-chat/project/scheduler/chat chrome or workspace
   return overlay is mounted. Use most viewport width under the fixed site header.
2. Primary sections: Overview, Traces, Agents, Reliability, SLOs, FinOps, Alerts,
   Diagnostics, Configuration. Configuration has URL subnavigation for Data &
   Retention, Integrations, Access and Effective Configuration. Reuse existing
   protected panels/query/contracts; configuration stays read-only.
3. Remove the entire Observability & FinOps app Settings navigation/type/mount and
   obsolete Settings wrapper. Preserve shared panel implementations used by the
   Resources console. One canonical UI; no duplicate policy experience or app CTA.
4. Existing Resources > Observability routes directly to the full console. Existing
   Open app returns to ANOC. Browser reload/back/forward preserve the correct shell;
   production hosting fallback requires its separate deployment validation.
5. Resources location does not grant access. Verified access admission is checked
   before dashboard queries mount; unavailable/unverified identity produces safe
   authentication/access states. Every protected endpoint retains server capability,
   environment and revocation enforcement; read API/base remain independent.
6. Preserve chat safe progress, approval/command flows, Teams continuity, rewind,
   session persistence and state ownership. AppState remains above the route switch;
   moving shells does not reparent/reset workspace state or consume draft handoff.

## Approved implementation phases and validation gates

These are phases of this workstream, not new M-number milestones. Local phases
are authorized by the follow-up implementation request and execution-plan update;
production handoff remains gated. M17 remains blocked by M16.

| Phase | Deliverable | Required local proof before advancing |
|---|---|---|
| 1 — read composition | GET router split, independent FastAPI factory/lifespan, shared read dependencies | Route inventory proves no business/receipt mutation; import/startup spies prove no model/agent/tool warmup or workers; shared safe errors, identity/environment denial and DTO parity |
| 2 — isolated reads | Configurable read DB pool, archive reader, published health/policy facades | Isolated disposable DB rejects writes; query/connection/archive bounds; no GET financial jobs; unknown producer state truthful; scope/cursor/privacy/adversarial regression and M9/M10–M15 equivalence |
| 3 — transport | Independent frontend read base, explicit origin policy, existing verified auth adapter seam | All observability/policy/financial GETs use read base; receipts/business retain ANOC; cross-origin/auth/revocation/timeout denial; production missing base fails closed; no wildcard/role-header trust |
| 4 — product migration | Resources shell route/nav, nine sections, all configuration moved; no app Settings entry | Build/type checks; route reload/back/forward/state continuity; capability tests, keyboard/responsive views, bounded polling; no duplicate dashboards, invented values or browser financial formulas |
| 5 — integrated qualification | Separate local processes, failure/load isolation and M16 rerun | Reader overload/outage leaves ANOC turn/approval/SSE/accounting semantics intact; shared DB contention tested; unchanged authoritative CPU/dependency tests and required full regression; individually evaluate C41/C42 without changing historical results |
| 6 — production handoff | Reviewed IaC/config, runtime identities, exact resource bindings and live validation plan | Local static/native checks honestly distinguished; exact operation approval for every external mutation; production isolation/auth/read grants/health/failure proof before claiming live readiness |

Future validation should reuse backend observability API/security/SLO/FinOps/archive
suites, frontend contracts/panels/polling suites, installed-framework route/lifespan
checks, and the authoritative M16 harness. Select concrete commands after the
implementation diff and isolated test configuration exist; never run shared DB
migrations or tests that can reach cloud by convenience. Final integration requires
build/import checks, existing applicable regressions and individual acceptance
review. Doc 13 and M16 acceptance thresholds remain unchanged.

## Cloud handoff, risks and readiness

Cloud deployment status: **NOT EXECUTED — USER APPROVAL REQUIRED**. No exact remote
command is proposed in this pass; project/account/environment/resource bindings
remain to be supplied and reviewed. Future required operations may include a
separate Cloud Run deployment and scaling/health config, service identity and
least-privilege read IAM, database read principal/grants and secret binding,
explicit origin/auth/frontend service routing, optional DNS and source-controlled
monitoring. Existing archive deployment remains a separate approved handoff.
No service, IAM/user, DNS, Terraform apply, shared migration or external setting
is created or changed here. Cost includes reader compute/minimum instances,
connections, authorized trace/archive reads and possible network egress; no new
billing export or billing-policy changes are required by console separation.

Every future mutation must present the exact command, project/account/tenant,
resources, active-workstream need, operational/security/cost impact, data-loss/
interruption risk, rollback and DEV/STAGING/PRODUCTION classification, then await
specific approval. Rollback plan: restore prior reviewed route/base/service revision
and limit/disable reader traffic; preserve all accounting/archive history. Rollback
must not quietly reintroduce dashboard polling on ANOC or bypass authentication.

Primary blockers/risks: unresolved M16 CPU defect and missing current full-regression
acceptance; no established production verified-principal integration; unavailable
durable publication of producer-local counters; missing aggregate/trend/alert-history
sources; production DB grants/service bindings not supplied; shared DB contention;
archive reconstruction resource pressure; cross-origin auth/CORS configuration;
production/live validation not executed. A console redesign does not resolve them.

Local implementation exists; production readiness is **NO**. Ready for Next
Milestone remains **NO**. Local review status and current validation results are
recorded in `.agent/OBSERVABILITY_EXECUTION.md`.


## Implemented local architecture

The dedicated Resources-shell console and independent FastAPI read factory now
exist. See [local launch and source audit](OBSERVABILITY_READ_PLANE_LOCAL.md).
The 30-GET allowlist excludes receipts/business routes; reusable GET composition
preserves the existing ANOC combined router. Reader lifespan owns only its bounded
read-only pool, repository facades, existing production safe JSON formatter lease
and optional existing archive reconstruction.
No producer or financial worker is constructed. Process-local ANOC counters and
ledger admissions are nullable/unavailable; Collector/exporter state is unknown.
Effective producer policy requires an environment-matched immutable snapshot.

Frontend observability/policy/financial reads use an independent HTTPS-required
production base. Existing ANOC/SSE receipt paths remain unchanged. Nine URL-backed
sections share environment/completed-UTC window/refresh controls and in-flight
reads; no durable browser cache. SLO and financial values reuse existing sources.
Unsupported metrics remain unavailable. Configuration contains policy, retention,
integrations, access and effective values in Resources; app Settings contains none. Protected archive projection
retains safe names, parent IDs and timing without raw attributes/events.

Local proof includes startup/every-GET side-effect spies, verified principal and
role/environment/cursor tests, write rejection, privacy, real published billing
and immutable archive reads, plus independent loopback ANOC/reader processes.
Test identity is injected only in isolated fixtures; production provider still
defaults denied. The process test proves session-request isolation during reader
slowdown/burst/outage/restart, not production shared database or whole-turn load.
Full frontend/build and backend regression results are recorded in the execution
record, including failed attempts and repairs; M16 CPU acceptance is separate.

No Terraform/deployment/database migration was authored or applied for this
feature. Production service/grants/auth/CORS/shared contention remain unvalidated.
Historical M16 receipts are preserved; C41/C42 require final revalidation, M16 is
not complete and M17 is unstarted.


## Resources UX correction implemented — 2026-10-11

App route dispatch now mounts ObservabilityResourcesPage with ResourcesShell and
existing site header, independently of ProductApp. The app Settings type/nav/mount,
sidebar console link and route-specific app-workspace branch are removed. Obsolete
Settings wrapper/nav/tracing wrapper removed; shared protected modules retained.
Configuration migrated into the console with bookmarkable subroutes; integrations
include read-plane and financial-source state guarded by their existing capabilities.
Verified-access failure renders an authentication/access state without dashboard
queries; query cancellation/revocation/privacy and separate API base are reused.
Current validation and scoped changed files are in the execution record. This is a
frontend-only correction; backend/read-plane files and M16 receipts are unchanged.
Cloud changes NONE EXECUTED. M16 C41/C42 remain subject to final revalidation; no M17.


## Shared LOCAL/DEV identity amendment — 2026-10-11

The user explicitly authorized a shared server-side LOCAL/DEV adapter after
confirming no production human provider exists. Both ANOC UserContext and reader
Principal now consume one VerifiedIdentityProvider boundary. Development must be
explicitly enabled with a reviewed, bounded server-owned subject/grant file; no
browser capability, role or environment claims are trusted. Role ceilings and
financial/technical isolation remain unchanged. Unconfigured identity denies;
production/hosted runtimes reject development mode before lifecycle work. Future
IAP provider is inert/deny-only; it does not accept plain email or fake assertions.
See [authentication boundary and future IAP handoff](AUTHENTICATION.md) and
[local launch](OBSERVABILITY_READ_PLANE_LOCAL.md). This supersedes earlier
LOCAL/DEV-only provider-unavailable statements, not production readiness. No cloud
mutation or M16 acceptance change is authorized; M17 remains unstarted.

## Hybrid derived read-model amendment — 2026-10-11

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) preserves this independently deployable GET-only read plane and current
Resources-shell console. A separate projection writer (never the reader or ANOC
request path) consumes audit reference events and sanitized Collector operational
OTLP to supply bounded durable dashboard/agent/activity/alert views. Existing
business/audit/M10–M14 sources remain authority. Reader jobs/mutations remain
forbidden; no ANOC fallback. Future data contracts distinguish source version/window,
CAPTURED/RECONSTRUCTED/UNKNOWN, freshness/lag/coverage/debt. No synthetic historical
chart or zero default for missing data. Near-real-time bounded refresh remains
appropriate; no new stream/WebSocket is added. Current configuration UI stays
read-only and server role ceilings remain unchanged. This pass changes no frontend.


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

## Phase-1 production-readiness update — 2026-10-11

Independent GET-only reader and existing console remain unchanged. Authenticated audit POST belongs only to the separate projector. Strict hybrid activity DTO supplies derived run/model-agent activity and bounded debt, not complete cross-domain drill-down. See AUDIT_COVERAGE_MATRIX.md and HYBRID_PHASE1_PRODUCTION_READINESS.md.

## Execution journal status — 2026-10-11

GET /api/observability/runs/{run_id}/audit uses existing operational capability and environment gates. Bounded occurrence/event-ID chronology exposes arrival time and PARTIAL status; bounded current action summaries report DISPATCHING outcome UNKNOWN. DTO/client only; no UI redesign or policy mutation. See [implementation](27_EXECUTION_AUDIT_JOURNAL_AND_CONTROLLED_EGRESS.md), [coverage](AUDIT_COVERAGE_MATRIX.md) and [remaining gaps](AUDIT_OWNERSHIP_GAP_DESIGN.md).
