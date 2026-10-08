# SLOPANOC Production Observability + SRE + FinOps — Execution Record

## Program Status

Current milestone: **M10 — IMPLEMENTED AND LOCALLY VALIDATED**

Last completed milestone: **M10 (all blocking local gates PASS; production validation pending)**

Next permitted action: **STOP after M10. Local Ready for Next Milestone: YES; M11 requires a separate instruction and has not begun. No deployment or cloud operation authorized.**

Overall status: **M0 VALIDATED COMPLETE; M1–M10 IMPLEMENTED AND LOCALLY VALIDATED; CLOUD NOT EXECUTED**

Authoritative architecture:

`docs/Telemetry/`

---

## Milestone Matrix

| Milestone | Scope | Status | Next Milestone Permitted |
|---|---|---|---|
| M0 | Architecture Contract | VALIDATED COMPLETE | YES |
| M1 | OpenTelemetry Foundation + Production Collector | IMPLEMENTED AND LOCALLY VALIDATED; cloud pending | YES |
| M2 | Root Turn Trace | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS | YES |
| M3 | Model Instrumentation | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS; durable accounting M10 by design | YES |
| M4 | Agent / Tool Instrumentation | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS | YES |
| M5 | Dependency + Infrastructure Instrumentation | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS | YES |
| M6 | Reliability Controls | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS | YES |
| M7 | Persistence + Observability APIs | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS; production pending | YES |
| M8 | UI Operational Visibility | IMPLEMENTED AND LOCALLY VALIDATED; all blocking local gates PASS; production pending | YES |
| M9 | SRE Metrics + SLOs | IMPLEMENTED AND LOCALLY VALIDATED; production activation pending | YES (local only) |
| M10 | Accounting-Grade FinOps Runtime Ledger | IMPLEMENTED AND LOCALLY VALIDATED; production validation pending | YES (local only; STOP, M11 not authorized) |
| M11 | Billing Integration | NOT IMPLEMENTED BY DESIGN; separate instruction required | NO |
| M12 | Reconciliation + Allocation | BLOCKED BY M11 | NO |
| M13 | Unit Economics | BLOCKED BY M12 | NO |
| M14 | Budgets / Forecasting / Anomalies | BLOCKED BY M13 | NO |
| M15 | Security / Retention / Sampling Hardening | BLOCKED BY M14 | NO |
| M16 | Validation + Failure Injection | BLOCKED BY M15 | NO |
| M17 | Production Operationalization | BLOCKED BY M16 | NO |
| M18 | Release / CI / IaC Hardening | BLOCKED BY M17 | NO |
| M19 | Quality / Outcome + Continuous Profiling | BLOCKED BY M18 | NO |

## M0 — Architecture Contract: implementation plan

Planning date: 2026-10-07. Authorization: PLANNING ONLY. M0 implementation NOT STARTED; runtime validation NOT RUN; last completed milestone NONE; M1 blocked. This pass modifies only this execution record.

### Objective and authoritative specifications

Plan an inert, importable architecture skeleton defining one telemetry vocabulary, schema/version contract, configuration, privacy and cardinality policy. No OpenTelemetry initialization, instrumentation, routes, database creation, migrations, deployment or frontend implementation belongs in this pass.

Read: AGENTS.md; .agent/PLANS.md; prior execution record; docs/Telemetry/README.md; 00_MASTER_BUILD_CONTRACT.md; 01_TARGET_ARCHITECTURE.md; 02_TELEMETRY_DATA_CONTRACT.md; 11_SLOPANOC_INTEGRATION_MAP.md; 12_IMPLEMENTATION_ROADMAP.md; 14_CODEX_EXECUTION_GUIDE.md; 15_DEFINITION_OF_DONE.md. Additional relevant contracts read: 04_RELIABILITY_TIMEOUTS_ERRORS.md; 05_STORAGE_APIS_AND_UI.md; 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md; 13_TEST_VALIDATION_ACCEPTANCE.md; 18_STRUCTURED_LOGGING_STANDARD.md; 20_FINOPS_ACCOUNTING_DURABILITY.md. Later milestone infrastructure/SLO/release/profiling specifications must be read before their implementation.

### Current-state audit and components to reuse

| Source | Actual mechanism / reuse decision |
|---|---|
| backend/api/chat_service.py | execute_turn_events is the canonical sync/SSE pipeline; _run_turn_events performs work. Background tasks own session locking and mutation independently of HTTP relay lifetime. _background_turns and _run_tasks[(session_id, run_id)] own execution/cancellation. Preserve these mechanics; future root spans cover complete execution, not only the Runner loop. |
| backend/api/app.py | One FastAPI lifespan validates runtime PostgreSQL configuration then performs best-effort model warmup. Existing SSE, cancellation, session history and rewind routes remain. Future M1 SDK lifecycle integrates here. Do not boot app for M0 audit: startup can fetch secrets and call a model. |
| backend/api/streaming_events.py | EventSequencer generates trusted UUID run IDs and typed sequenced envelopes. Public Stage, TraceCategory, TraceStepStatus and run.completed outcome ok/error already exist. Retain wire compatibility. |
| backend/api/run_trace.py | Per-run RunTraceRecorder emits sanitized trace.step SSE events, stores recorded steps, deduplicates consecutive signatures, and allowlists named integer counts excluding booleans. This is a UI projection, NOT an active-run registry. |
| backend/api/activity_queue.py; activity_translator.py | Bounded run-scoped activity transport and static public labels expose nested tool work invisible to outer ADK events. Reuse observed facts and projection boundaries; preserve cleanup/thread semantics. |
| backend/api/perf_timing.py | Injectable monotonic clocks, PerfTimer, DelegationTimer, streaming-aware before/after model callbacks, partial-chunk usage accumulation and terminal deduplication. Text-format backend.perf logger. Pending model calls are keyed only by run: cannot serve as nested/concurrent invocation accounting identity. |
| backend/api/turn_context.py | Trusted run ContextVar plus run-scoped content/evidence bridges and cleanup. Reuse correlation, never export bridge contents. chat_service's persistent ADK drain task preserves attach/detach context; do not reintroduce per-item Tasks driving one generator. |
| backend/tools/knowledge/diagnostic_trace.py | Locked run-scoped domain records, snapshot/discard and count caps; snapshots persisted in turn_retrieval_diagnostics, capped at 20 turns. Records bounded query/command/user-request/objective text, titles/headings and generic operational-event dictionaries. These are not automatically safe for production export. authority_preview annotates hypothetical diagnostics only. |
| backend/context/assembly.py | Existing Context Engineering Broker assembles knowledge/case/operational/experience context and estimates tokens. Observe counts/policy/exclusions; never export ContextItem.content or formatted prompt bodies. Observability records must not become operational evidence. |
| Conversation/governance | backend/conversation/ is absent; backend search found no named TurnPlan or TurnAuthority class. Responsibilities reside in Team Manager schemas/source requirements/routing/state synchronization, API pending action/selection/clarification, TAE troubleshooting threads, cases/progression/evidence, final-answer/source-reference/command-egress modules and operations/approval policy. Preserve actual owners; do not build a parallel conversation subsystem to match conceptual documentation. |
| backend/agents/* | Team Manager, IM, TAE, PM and AOE already register perf model callbacks. Team Manager callback chains enforce content projection/routing and delegation; IM includes image injection/tool diagnostics. Later instrumentation composes with their order/return semantics, never replaces enforcement. Warmup, direct/remediation and embedding calls need separate M3 coverage inventory. |
| TAE structured_output.py | Locked process-lifetime retry/failure counters and diagnostic events; reuse bounded facts as later metric producers. Local counters are not production metrics or accounting. |
| backend/tools/teams/*; gateway/power_automate_client.py | Teams uses Power Automate gateway with existing request timeout and duration/outcome logging. Observe the gateway honestly; do not claim direct Graph spans without downstream propagation. Preserve write approvals and retry/idempotency semantics. |
| Knowledge / storage | Existing knowledge tools/runtime/repositories own evidence. Attachments and knowledge artifacts have GCS storage adapters. Extend actual dependency boundaries later, not replacement clients. |
| Errors/logging | Python text-format logging/perf/retrieval diagnostics; no unified application JSON/OTLP initialization found in inspected entrypoints. gateway/safe_error.py owns safe lowercase public errors, retryability and correlationId; api/errors.py maps HTTP errors. ADK uses internal OTel spans: inspect installed SDK providers in M1 before initializing or auto-instrumenting. |
| backend/config/settings.py | Injectable environment mappings, validated accessors, process-cached Secret Manager retrieval, shared model access; separate session/case and knowledge URL configuration. Gateway, dense retrieval and warmup timeouts already exist. Never invoke secret resolvers during M0. |
| Persistence / alembic | ADK DatabaseSessionService owns session schema. CaseDatabase owns an application async engine against the configured session/case URL; knowledge owns separate Base/engine/config. Alembic combines CaseBase, KnowledgeBase and AttachmentBase, filtering application-owned tables. Four revisions cover baseline, timestamps, attachments and troubleshooting progression. Old CaseDatabase comments saying Alembic is absent are stale; migration source wins. |
| src/ transport/state/chat | React/Vite/TS; streamChat, sseParser, runBackendChat and DTOs separated from AppState/rendering. Missing run.completed becomes frontend error completion; abort is transport cleanup. Reuse CurrentActivity, ToolActivityFeed, expandable RunTrace, elapsed-time helpers, provenance/source components and existing tests. |
| Settings / design | Sidebar opens AppState-managed SettingsModal; sections Usage, Connectors, Skills. UsagePanel reads MOCK_USAGE, not financial truth. Radix dialog: max width 880px, height 580px, 208px sidebar, padded scrolling content. Reuse Tailwind light/dark surface/text/border/accent tokens, Ericsson Hilda/Inter typography, rounded controls, focus rings and fade interactions. |
| Tests/tooling | Backend tests are in backend/tests/, not root tests/. Conftest injects fake gateway URL, disables warmup and substitutes runtime DB policy. It does not globally block network or replace every DB URL. Audit fixtures and isolate DB/client dependencies before tests. Frontend uses Vitest/Testing Library. .venv and node_modules exist; no installs performed. |

### Conflicts / duplication and M0 reconciliation

1. Public SSE stages/statuses, perf mark names, domain success/error and target dot-separated stages/uppercase statuses differ. Establish one internal contract and explicit one-way compatibility mappings; do not rename public wire values in M0. Trace-step warning is presentation severity, not terminal status.
2. Perf, delegation, gateway timing and ADK spans overlap. Define one owner per boundary and later adapt useful existing measurements; do not create two duration/metric truths.
3. Task registries control execution; domain dictionaries and UI trace steps do not provide operational cross-instance status. Future single active-run owner has a Cloud SQL repository and execution-local cache, while task handles remain local execution mechanics.
4. Pending model state per run can be overwritten by nested/concurrent calls. M3 needs pre-call identity, per-attempt accounting and streaming-terminal deduplication. Token logs cannot become the durable ledger.
5. Legacy diagnostics contain bounded sensitive free text and arbitrary nested dictionaries. Define a counts/codes/approved opaque-ID export projection; do not export whole snapshots. Later convergence must sanitize before every log/export/persistence/API sink and avoid a shadow copy in ADK state. M0 documents the repair; does not change existing behavior.
6. Preserve SafeError user messages/retryability; technical error codes are a contextual observation. A generic connector failure alone cannot prove GRAPH_TIMEOUT. One owning boundary logs sanitized details; higher layers add context rather than duplicate stack traces.
7. Normative doc 02 codes take precedence over lowercase running and span-like teams.read_messages in illustrative API examples. Event stages and span operation names are distinct; future processing.heartbeat wire event projects internal heartbeat. Document mappings rather than inventing aliases.
8. Roadmap requires M3 usage rows and M10 pricing/estimation; existing execution matrix labels M10 accounting-grade ledger. One durable usage path starts in M3, extended by M10, never recreated. M2 needs minimal durable production active status; M7 expands summaries/query/RBAC/retention. Neither is implemented in M0.
9. identity.py trusts an unverified development user header and has no roles. Reuse dependency seam only. Production authentication/RBAC must precede protected API exposure; not an M0 implementation task.
10. Conceptual conversation constructs differ from local architecture. Map observation onto existing owners without refactoring governance or claiming missing constructs already exist.

### Proposed package boundaries and ownership

Only inert contract modules are implemented in M0. Future runtime modules are documented ownership destinations, not fake working services.

| Package owner | Responsibility and activation |
|---|---|
| __init__.py | Inert package; no app imports, models/providers/engines/registries initialized. M0. |
| stages.py | Exact doc 02 event vocabulary and RunStatus; STALLED nonterminal, terminal set COMPLETED/FAILED/TIMEOUT/CANCELLED. M0. |
| attributes.py | Resource/span/release keys, bounded metric label allowlist, identifier exclusions. M0. |
| schemas.py | Strict pure TraceEvent, SafeValue, ActiveRun, turn-summary and usage contracts; telemetry/cost/pricing schema versions initially 1. No ORM models. M0. |
| errors.py | Exact doc 04 technical error enum and classification contract. M0 codes only; runtime mapping M6. Expected source/approval policy outcomes are not automatically ERROR or failed turns. |
| redaction.py | Single allowlist/bounds/classification policy and pure validation/sanitization helpers; reject unknown/nested unsafe payloads. M0 offline helpers, no application wiring. |
| config.py | Typed immutable projection of existing Settings/environment; no secrets or runtime startup. M0. |
| tracing.py; exporters/ | OTel provider/context/span plus OTLP/local/no-op export M1 onward. chat_service alone owns root turn M2; request/server parent span is distinct. |
| metrics.py; logging.py | Bounded aggregates and correlated JSON logging, centralized safe metadata, one detailed exception owner. M1 foundation; producers/SLOs later. |
| instrumentation.py; model/tool/dependency instrumentation | Context-safe wrappers/callback composition at actual invocation boundaries M3–M5; no parallel runner or governance return-value changes. |
| active_runs.py; watchdog.py | One operational status owner; durable Cloud SQL plus local execution cache. M2 lifecycle/minimal persistence, M6 watchdog/deadlines. Task execution ownership stays in ChatService. |
| persistence.py; repositories.py; models.py | Application-owned async operational/usage persistence. M2 status, M3 ledger/outbox, M7 summaries/query/retention. No ADK private engine access. |
| api_models.py / existing API | Safe authorized run/timeline/active/health projections and separate financial projections. M7, no M0 endpoints. |
| finops/ | usage_ledger/outbox M3; pricing/estimator M10; reconciliation/allocation M11–M12; unit_economics/budgets/forecasting M13–M14. One unsampled append-oriented idempotent accounting path, separate from export sampling. |

Dependency direction: producers → contract/runtime facade → redaction → exporters/repositories. Contracts cannot import agents/orchestration/tools or retrieve evidence. FinOps does not depend on sampled traces. No observability record feeds governance, provenance or conversation intent. Existing Settings owns environment access; config.py owns typed observability policy. Terraform/production policy owns effective infrastructure configuration.

### Canonical lifecycle, errors, schemas and versions

- Copy every doc 02 stage/status and doc 04 error into enums, tested against full documented sets. Span operation names (slopanoc.turn, session.load, gen_ai.request, http.microsoft_graph) are separate from lifecycle event stages. Legacy mappings remain explicit, not a second canonical namespace.
- PENDING → RUNNING; RUNNING → STALLED; STALLED → RUNNING on progress; nonterminal → exactly one terminal state. No terminal reopening. Cancellation request is not terminal until execution stops mutation; SSE disconnect is transport state, not automatic cancellation.
- Reuse EventSequencer run identity; define future trusted turn_id separately from span/ADK invocation identities. Verify legacy diagnostic turn_id callsites before mapping. Future W3C trace context is validated at API boundary; incoming identifiers never grant authority.
- TraceEvent follows doc 02 fields with schema version; timestamps timezone-aware UTC, counts/durations nonnegative, unknown fields rejected, metadata bounded SafeValue. ActiveRun carries IDs, status/stage/agent/tool/dependency and start/stage/progress/heartbeat/terminal timestamps. Summary stores operational facts, not bodies/full trace trees.
- Usage contract reserves deterministic usage_event_id, pre-call identity/attempt, provider/service/model/operation/agent/domain, units/quantities/token counts, estimated flag, separate estimated/billed values, native currency/EUR reporting, price/source/version and correction references. Missing usage stays unknown or explicitly estimated. Decimal financial values; no estimator now.
- One version owner in schemas.py: telemetry, cost and pricing schema versions. Reserve doc 02 release/config/model/prompt/agent/tool/TurnPlan correlation keys; do not fabricate a governance contract version absent from source. Breaking versions require migration/compatibility strategy.
- Default metadata: bounded counts/codes/approved identifiers only. Prohibit credentials/auth headers, full prompts/responses, Teams bodies, attachment/knowledge contents and hidden reasoning. Query/command/request/error text is not safe merely because truncated. Restricted IDs require authorized sinks.
- Metric labels permit environment and approved agent/tool/dependency/model/provider/domain/status/error/operation categories only. No run/turn/session/user/chat/case/fault/source/resource IDs, URLs, free-text errors or model-generated labels. Resource attributes contain process metadata only.

### Configuration ownership

M0 implementation adds unused validated Settings accessors and an explicit pure config projection, preserving current behavior. Observability/OTel/FinOps flags default OFF; sampling default 1.0, always-error/governed flags true, service name slopanoc, currency EUR. No import-time environment validation, secret resolution or SDK startup.

Define all master-contract SLOPANOC settings: OBSERVABILITY_ENABLED; OTEL_ENABLED; OTEL_EXPORTER_OTLP_ENDPOINT; OTEL_SERVICE_NAME; OTEL_ENVIRONMENT; TRACE_SAMPLE_RATE; TRACE_ERRORS_ALWAYS; TRACE_GOVERNED_TURNS_ALWAYS; MODEL_TIMEOUT_SECONDS; GRAPH_TIMEOUT_SECONDS; KNOWLEDGE_TIMEOUT_SECONDS; DATABASE_TIMEOUT_SECONDS; STORAGE_TIMEOUT_SECONDS; WATCHDOG_STALL_SECONDS; HEARTBEAT_SECONDS; FINOPS_ENABLED; FINOPS_CURRENCY; FINOPS_PRICE_SOURCE; FINOPS_BILLING_PROJECT; FINOPS_BILLING_DATASET. Names carry SLOPANOC_ prefix. Detailed Billing + Pricing are primary price/billing sources; FOCUS supplemental.

Require strict booleans, finite positive durations, sampling [0,1] with initial stabilization 100%, heartbeat below stall threshold, validated endpoint when exporting is enabled. Production deployment supplies environment explicitly. Do not log credential-bearing endpoints. Before implementing defaults, compare actual gateway/warmup/dense timeout caps and document fallback precedence; new unused accessors do not apply deadlines in M0. Billing project/dataset are requirements for later enabled integration, not grounds for M0 cloud access. Tests inject mapping values.

### Database and migration ownership

M0 creates no migrations, ORM tables, engine or connections. Future models.py owns a separate application observability Base, using the configured session/case DB and injected async SQLAlchemy conventions. No extra production database requirement; never reuse ADK private internals. Assess application pooling before choosing explicit shared application session factory versus dedicated application engine.

When the first actual table arrives, extend existing Alembic target_metadata and retain derived application-owned table filtering. Never migrate/drop ADK sessions/events/app/user states or unrelated tables. Determine actual revision head from revision dependencies, not filename order. Minimal run status belongs M2; ledger/outbox M3; summary/query indexes M7; pricing/allocation/budget/reconciliation follow their milestones. No configured remote autogenerate or migration now. Later migration proposal reports target/revision/schema/table/index/lock/data risk and rollback before exact-operation approval. Local disposable DB only when isolated.

### Settings / UI contract and explicit impact evaluation

1. Information: M0 creates contract/policy/version definitions needed by Settings, no runtime observations.
2. API support: later safe effective-config DTO includes effective value/source (environment/IaC/backend policy), schema/config versions, capabilities/read-only state. Never expose secrets. Run/timeline DTOs follow doc 05; FinOps DTOs are separate and contain no conversation bodies. No M0 API.
3. Timing: M7 APIs/RBAC, M8 operational UI/progress; later SRE/financial data appears only after its milestone passes. No mock financial data presented as measured production data.
4. RBAC: User own safe progress/details; Operator permitted operational metadata; Developer/SRE technical diagnostics; FinOps financial data without content; Admin configuration visibility; Auditor read-only governed summaries. Server enforces query/field scope; no implicit content permission or role inheritance. Dev identity is not production RBAC.
5. Editability: Terraform/environment/secure-backend settings show effective read-only values even to Admin unless architecture expressly permits runtime mutation. UI cannot bypass IaC or change cloud/billing/IAM/secrets.

Existing Sidebar/AppState SettingsModal gets one future Observability & FinOps category, retaining Usage/Connectors/Skills. Secondary in-panel navigation: Overview, Tracing, Reliability, SLOs, FinOps, Data & Retention, Integrations, Access, Diagnostics. Reuse current dialog focus/Escape, scrolling, tokens, typography, spacing, rounded cards, focus rings and transitions. No separate app/design system. Timeline space can adapt within existing shell. UsagePanel mock quota remains distinct from accounting and must never become financial truth.

| Section | Intended data |
|---|---|
| Overview | Safe health, active-run and permitted aggregate usage/cost freshness. |
| Tracing | Run lookup, causal waterfall, correlations, safe governance summaries. |
| Reliability | Dependency/stage health, stalls/timeouts/retries and safeguard state. |
| SLOs | Versioned targets, performance, error budgets/burn rates. |
| FinOps | Usage, separate estimated/billed cost, EUR/native currency, allocation, unit economics, budgets/forecast/anomalies. |
| Data & Retention | Effective read-only retention/sampling/redaction/schema policy. |
| Integrations | Collector/cloud/billing health/freshness, no credentials or cloud writes. |
| Access | Effective server permissions/content separation. |
| Diagnostics | Authorized run ID/status/stage/agent/tool/dependency, elapsed/last progress, timeline/errors/permitted cost. |

Chat preserves sanitized expandable RunTrace, source/provenance views and terminal/rewind behavior. Future safe static labels: Understanding request…, Selecting context…, Reading Teams conversation…, Searching governed knowledge…, Analyzing evidence…, Preparing response…. Optional Details/run ID; no hidden reasoning or unrestricted diagnostics.

### Exact files expected for M0 implementation

Add backend/observability/__init__.py, README.md, stages.py, attributes.py, schemas.py, errors.py, redaction.py, config.py. These contain only inert contracts, pure policy/config and ownership/compatibility documentation.

Add backend/tests/test_observability_contract.py, test_observability_policy.py, test_observability_settings.py.

Modify backend/config/settings.py (unused validated accessors only), docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md (clarify normative vocabulary versus illustrative span/projection values and version representation without changing approved codes), and this execution record (implementation/validation evidence).

No M0 edits to chat_service/app/perf_timing/run_trace/domain diagnostics/agents/tools/context/src/Alembic/requirements/deployment/Terraform. Runtime modules above are future destinations, not executable placeholders. Update plan before any newly necessary file is changed.

### Implementation sequence — not executed

1. Re-read local status/diff and M0 requirements. Finalize safe config defaults/precedence and public/legacy mappings against actual callers.
2. Create inert skeleton and full canonical vocabulary, attributes, schemas and versions. No OTel dependencies or provider initialization.
3. Add unused Settings accessors/config projection and pure redaction/cardinality policy.
4. Document ownership/source mappings/DB/UI policy; add offline contract/policy/config tests.
5. Run audited isolated validation/regressions; repair failures and rerun affected checks.
6. Inspect tracked/untracked changes and individually evaluate exits; record evidence. Stop after M0, never start M1 by implication.

### Architecture and safety invariants

No M0 production behavior change, even with flags set; no providers, logging handlers, global registries, engines, routes or startup hooks. One canonical turn and local task cancellation owner; preserve callback order, persistent driver context, locks, approvals, egress, evidence/provenance, session persistence/SSE/rewind. Observability never evidence/authority/intent. Cloud SQL active truth, local cache only; full traces outside ADK state. Central redaction, bounded labels, one detailed error owner. Accounting unsampled/idempotent/append-oriented/durable with outbox/recovery/corrections; estimated versus billed separate; EUR/native currency, Detailed Billing + Pricing. Fixed OTLP → Collector → Cloud Trace/Monitoring/Logging; Terraform, W3C correlation and continuous profiling remain future requirements. No remote mutation, deployment, secret lookup or shared migration by implication.

### Validation commands and safety preconditions

Planning: read-only source/document/symbol inventory, git status --short, git diff --stat, git diff --check; inspect this untracked record directly. No imports/tests/builds in this pass.

M0 implementation commands (planned, NOT RUN):

```sh
.venv/bin/python -m pytest -q backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py
.venv/bin/python -m compileall -q backend/observability
.venv/bin/python -m pytest -q backend/tests/test_settings_secret_caching.py backend/tests/test_settings_knowledge_database_url.py backend/tests/test_runtime_database_policy.py backend/tests/test_startup_import_graph.py
.venv/bin/python -m pytest -q backend/tests/test_perf_timing.py backend/tests/test_run_trace.py backend/tests/test_streaming_events.py backend/tests/test_p2_model_call_correlation.py backend/tests/test_d2_merge_adk_activity_events_context_lifecycle.py backend/tests/test_chat_service_turn_context_lifecycle.py
.venv/bin/python -m pytest -q backend/tests/test_chat_service_streaming.py backend/tests/test_chat_service_rewind.py backend/tests/test_api_run_cancellation.py backend/tests/test_final_answer_persistence_and_consistency.py backend/tests/test_command_egress_boundary.py backend/tests/test_approval_security_contract.py backend/tests/test_context_assembly.py backend/tests/test_api_ownership.py
npm test -- src/api/streamChat.test.ts src/api/sseParser.test.ts src/api/runBackendChat.test.ts src/components/conversation/RunTrace.test.tsx src/components/conversation/CurrentActivity.test.tsx src/state/AppState.reducer.test.ts
npm run build
git diff --check
git status --short
git diff -- backend/config/settings.py docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md
```

Before tests inspect each fixture: fake gateway/model/secret clients, memory or isolated disposable DB; inherited shared DB configuration must never reach clients. Conftest alone is not proof of full isolation. Contract tests deny network/secret/provider/engine activity on import; compare complete documented vocabulary/version sets; exercise nested fake secrets, forbidden IDs/metric labels, unknown schema fields, invalid settings and terminal-policy definitions. Existing tests verify unchanged SSE/cancellation/rewind/evidence/approval behavior. Do not use real app startup, Alembic or cloud tooling for import checks. Broaden testing only if changes/failures justify it.

### Every M0 exit criterion

| Criterion | Required evidence | Current state |
|---|---|---|
| Roadmap: no production behavior changed | Scoped inert diff; no import side effects; unchanged execution/SSE/governance regression results | NOT EVALUATED: implementation absent |
| Roadmap: one canonical naming contract exists | Full doc 02 stages/statuses and doc 04 errors, attributes/versions in one owner; documented compatibility projections | NOT EVALUATED |
| Roadmap: docs and code skeleton agree | Mechanical doc/code contract tests and README/source map review | NOT EVALUATED |
| M0 build items all implemented | Skeleton, enums, attributes, redaction/cardinality, settings, telemetry/cost/pricing versions | NOT IMPLEMENTED |
| Imports/builds pass | Offline import/compile and frontend build, no network/provider/engine initialization | NOT RUN |
| Milestone tests pass | Contract/policy/settings cases including adversarial and invalid inputs | NOT RUN |
| Applicable existing regressions pass | Audited isolated regression commands above | NOT RUN |
| Architecture ownership complete | Nine requested responsibility areas, lifecycle/schema/error/correlation, migration ownership, no duplicate runner/registry/ledger | PLANNED; implementation review pending |
| UI/data/RBAC/editability explicit | Existing Settings integration, nine sections, five-part impact review, no premature UI code | PLANNED; implementation review pending |
| Scope/safety/hygiene pass | No remote mutation, migrations, M1+, unrelated edits; inspect tracked and untracked files | Planning satisfied; implementation check pending |
| Blocking defects fixed / all requirements evaluated | Every applicable gate individually PASS, failures repaired/revalidated, no waiver | NOT SATISFIED |

### Cloud / infrastructure changes required

M0: NONE. No cloud/migration/IAM/billing/Terraform state operation is needed for local architecture contracts. No project/account/environment assumed or contacted.

Cloud deployment status: NOT EXECUTED — USER APPROVAL REQUIRED for any future external mutation. Exact pending M0 cloud operations: none. Later provisioning, migrations, exports and IAM require exact-operation safety-gate report/approval; this plan grants none.

### Planning results, failures, limitations and readiness

- Implementation performed: none. Actual file changed: .agent/OBSERVABILITY_EXECUTION.md only.
- Baseline status: untracked .agent/, AGENTS.md, docs/Telemetry/; tracked diff empty. Preserve user artifacts; git diff does not show this untracked record, so review content directly.
- Validation performed: static local audit and read-only Git review. Final git diff --check passed; git status retains the same three untracked baseline entries; tracked git diff --stat is empty. Execution-record content inspected directly because it is untracked. No runtime tests/builds executed or claimed.
- Failures encountered: root tests/ absent; corrected planned paths to backend/tests/. Large combined outputs truncated; central contract/source sections reread with targeted commands. No application test failures asserted.
- Repairs: planning path correction only; no application repairs.
- Known limitations: static audit, no live UI/cloud/runtime/accounting proof. Legacy diagnostic privacy, pending-call identity and development-only authentication need later integration work. Config deadline defaults/precedence finalized during M0 implementation against existing caps. Conceptual conversation names differ from source.
- Blockers to M0 implementation: none identified. Later deployment/API blockers are not permission or prerequisites to implement them in M0.
- Ready To Implement M0: YES — plan prepared; implementation requires a subsequent user request.
- Ready for Next Milestone: NO. M0 not implemented/validated; M1 remains blocked.

---

## M0 implementation start — 2026-10-07

User approved M0 only. Reinspected Settings, SettingsModal, SDK-context comments, error vocabulary and test isolation fixtures. No runtime callsite changes permitted. Extend expected contract schemas to encode Settings section/RBAC/effective-config and span/log interfaces in schemas.py, avoiding new frontend files. Defaults: model 120s (separate from unchanged warmup 30s), Graph falls back to existing request_timeout_seconds (10s), knowledge falls back to existing knowledge_dense_timeout_seconds, DB/storage 30s, heartbeat 5s/stall 30s. New caps remain inert; later reliability must review whole-turn budgets. Settings exposes one explicit observability_config accessor using its existing mapping, with lazy imports and sanitized ConfigurationError; no second environment reader.

## M0 actual implementation and validation — 2026-10-07

This section supersedes historical planning-only/pending statuses above. M0 user authorization received via attached request; M1 remains unimplemented and must not begin in this task.

### Architecture implemented

Inert backend/observability package, exact stage/status/error enums, lifecycle policy,
attribute/release/resource keys, bounded registered metric-label policy, enforced
safe/restricted/prohibited classification and count-only metadata validation,
version-1 event/span/log/active-run/summary/usage schemas, active-run repository
Protocol, immutable adjustment references and separate Decimal estimated/billed
cost, nine Settings IDs/names, six roles and capability ceilings, configuration
owners and read-only effective configuration. No authentication implementation,
provider/runtime/exporter/registry/ledger/DB/UI added.

Settings.observability_config lazily consumes existing Settings mapping. Flags off,
100% stabilization traces, EUR, primary Detailed Billing/Pricing. Strict validation,
masked endpoint and sanitized errors. Existing gateway/dense timeout fallback
preserved; explicit future timeout overrides win. Accessor is unused by runtime.
README documents actual source ownership, legacy/public projections, future package
activation, Cloud SQL truth/process cache, migration protection, safe logging,
unsampled durable accounting/outbox, and Settings integration without a new app.

### Actual files changed

Added:
- backend/observability/__init__.py
- backend/observability/README.md
- backend/observability/stages.py
- backend/observability/errors.py
- backend/observability/attributes.py
- backend/observability/redaction.py
- backend/observability/schemas.py
- backend/observability/config.py
- backend/tests/test_observability_contract.py
- backend/tests/test_observability_policy.py
- backend/tests/test_observability_settings.py

Modified: backend/config/settings.py (14-line unused lazy accessor),
docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md (M0 ownership/compatibility clarification),
.agent/OBSERVABILITY_EXECUTION.md (plan/results).

No changes to application execution, agents/tools/context, frontend, migrations,
requirements, infrastructure or deployment. Existing untracked AGENTS.md and
Telemetry documentation preserved. No commits or unrelated refactors.

### Validation performed and results

Backend validation used a local /private/tmp/slopanoc-m0-test-guard/run_tests.py
launcher, invoking the planned .venv/bin/python -m pytest -q commands with inherited
SLOPANOC variables removed, explicit isolated in-memory SQLite URLs, disabled warmup,
and a sitecustomize guard denying AF_INET/AF_INET6 socket connect/connect_ex. Guard
propagates into subprocess import tests. It is local test scaffolding, not a repository
or production modification. Fixtures still supply fake gateway/model/secret clients.
No cloud integration tests or configured remote database connection attempted.

- Planned 18-file backend regression selection: **277 passed, 2 deprecation warnings,
  22.35 seconds**. Includes startup import graph, settings/secret caching/runtime DB
  policy, timing/model tracking, trace/SSE/context lifecycle, streaming/rewind/cancel,
  final-answer persistence, command egress, approvals, Context Broker and ownership.
- Final contract plus settings/runtime-policy subset: **137 passed, 1 deprecation
  warning, 2.21 seconds** after sanitization/lifecycle review fixes. This comprises
  95 M0 cases plus 42 repeated existing settings/policy tests.
- Final M0-only 3-file suite after complete attribute review: **95 passed, 1.90 seconds**.
- .venv/bin/python -m compileall -q backend/observability: **PASS**.
- Planned 6-file Vitest selection: **211 passed, 6 files, 2.02 seconds**.
- npm run build (tsc -b and Vite): **PASS**, 2028 modules transformed. No src files
  modified; build performed as approved preservation validation.
- git status, git diff --stat, git diff inspected; git diff --check **PASS**. Tracked
  source diff only the inert Settings accessor. New package/tests and modified docs
  are untracked, so reviewed content directly rather than relying on git diff alone.

Commands correspond exactly to the plan's file selections; the safety launcher only
changes the process environment and blocks network. No dependencies changed in
source manifests. Existing test-only pytest 9.0.3 and pytest-asyncio 1.4.0 installed
locally in .venv following approved escalated package download.

### Failures encountered and repairs

1. .venv lacked pytest. Initial invocation failed; sandbox package download could
   not resolve PyPI. Approved escalation installed repository-pinned pytest and
   pytest-asyncio locally; tests then executed normally.
2. Pydantic Rust regex rejects lookahead. Replaced all-zero trace/span rejection
   with pure AfterValidator and supported anchored hex regex; tests pass.
3. Test helper initially imported without backend.tests qualification; corrected.
4. Fresh import isolation assertion counted preloaded google/google.cloud namespace
   modules from interpreter startup. Compare newly imported modules against initial
   module snapshot; still deny runtime/API/agent/tool/SQLAlchemy/SDK imports and network.
5. Review found existing timeout ConfigurationError might reveal its original raw
   value through fallback. Accessor now sanitizes that exception too; adversarial
   fallback tests pass. Corrected test fixture to established gateway timeout name.
6. Review added explicit terminal-stage/status agreement, conditional restricted IDs,
   active record version for future CAS and conditional identity attributes. No
   runtime lifecycle behavior was changed. All M0 tests revalidated.

### Individual exit-criterion evaluation

| Gate | Result | Evidence |
|---|---|---|
| No production behavior changed | PASS | Only unused lazy Settings accessor modifies tracked application source; existing sync/SSE/approval/rewind/cancel/persistence regressions pass. |
| One canonical naming contract | PASS | Full doc 02 stage/status and doc 04 error sets mechanically compared; unique enums, attributes/versions, explicit one-way public/legacy mappings. |
| Docs and code skeleton agree | PASS | Full-set documentation tests, doc 02 ownership clarification and package README/source/milestone review. |
| Required M0 build items implemented | PASS | Inert skeleton, all enums/attributes, executable privacy/cardinality policy, Settings projection and telemetry/cost/pricing versions. |
| Imports/builds pass | PASS | Fresh import isolation, no new SDK/engine/runtime modules or socket access, compileall and frontend type/build successful. |
| Milestone-specific tests pass | PASS | 95 M0 contract/policy/settings cases; adversarial metadata, restricted IDs, lifecycle, configuration and financial schemas. |
| Applicable existing regressions pass | PASS | 277 backend and 211 frontend cases; relevant settings subset repeated after final accessor change. |
| Architecture ownership complete | PASS | Package README/plan define tracing, metrics, logs, active state, errors, redaction, FinOps, configuration, persistence and Alembic protection; no duplicate runtime system. |
| Settings/UI/RBAC/editability explicit | PASS | Nine section IDs/names, six roles/capability ceilings and forced read-only effective-config schema; existing modal owner/design and future server checks documented; no UI built. |
| Scope/safety/repository hygiene | PASS | Source/changed/untracked review, whitespace check; no unrelated edits, credentials, cloud artifacts, migrations, deployment, SDK initialization or M1 code. |
| No blocking defects / all required architecture present | PASS | All above independently evaluated and local failures repaired/revalidated; later runtime deferrals explicitly owned by later milestones. |

### UI / Settings impact: five-part evaluation

1. Creates pure naming/schema/config/privacy information for future Settings;
   no current telemetry measurements or navigation changes.
2. Encodes run/summary/log/config contracts; later M7 authorized APIs and financial
   projections must enforce field/query scope before serialization.
3. No current UI work: M8 integration retains SettingsModal/Sidebar/AppState/tokens.
4. Canonical roles and capability ceiling encoded, no frontend-only authorization;
   production authentication remains future API prerequisite.
5. IaC/environment/backend effective configuration remains read-only even for Admin.

### Cloud status

**NO CLOUD MUTATION REQUIRED / EXECUTED**. No external-service resource mutation,
remote migration, Terraform state access, GCP/Graph/billing/IAM/GCS/BigQuery command,
deployment or cloud validation. Pending M0 cloud operations: none. Local package
installation downloaded test tooling only; did not mutate an external resource.

### Known limitations

M0 contracts are deliberately unwired. Existing sensitive domain diagnostics and
single pending-model-call slot remain unchanged; later adapters must enforce the
new safe projection and per-call identity before production telemetry use. Production
identity is still development-only and must be replaced before protected APIs.
Future sinks must revalidate payloads before export (frozen models do not freeze nested
dicts); role capability definitions do not themselves authorize routes. Full Cloud SQL,
OTLP, accounting durability, timeout/watchdog and UI acceptance belong to later gates.
No live-production validation claimed; M0 has no cloud-dependent acceptance criterion.

### Completion

Local implementation: **COMPLETE**.
Local validation: **PASS**.
M0 exit criteria: **ALL PASS**.
Ready for Next Milestone: **YES**.
M1 status: **NOT STARTED**; stop here under the user's M0-only scope.

---

## M1 — OpenTelemetry Foundation + Production Collector: planning — 2026-10-07

**Status: PLANNED — NOT IMPLEMENTED.** This section is the current plan and supersedes historical M0 planning readiness text without changing M0 completion evidence. Authorization is planning only. M1 runtime implementation and validation have not occurred. M2 remains blocked. Ready for Next Milestone: **NO**.

### Gate and objective

M0 completion section records COMPLETE, local validation PASS, all exit criteria PASS and Ready for Next Milestone YES. Actual inert package, unused Settings accessor and three M0 test files exist. This audit verifies the recorded gate and source; it does not rerun or independently certify the historical 95/277/211 test results. No gate discrepancy found.

Establish OTel providers, safe signal correlation, exporter modes, resource metadata, production Collector configuration, health and bounded shutdown inside the existing application. No root turn instrumentation, active registry, model accounting, agent/tool/dependency integration, watchdog, financial engine, APIs, UI, dashboards or later milestone business behavior.

### Authoritative files read

AGENTS.md; .agent/PLANS.md; existing execution record; docs/Telemetry/README.md; 00_MASTER_BUILD_CONTRACT.md; 01_TARGET_ARCHITECTURE.md; 02_TELEMETRY_DATA_CONTRACT.md; 03_INSTRUMENTATION_AND_RUNTIME.md; 12_IMPLEMENTATION_ROADMAP.md; 13_TEST_VALIDATION_ACCEPTANCE.md; 14_CODEX_EXECUTION_GUIDE.md; 15_DEFINITION_OF_DONE.md; 16_PRODUCTION_TELEMETRY_BACKEND.md; 18_STRUCTURED_LOGGING_STANDARD.md; 22_RELEASE_CONFIG_CORRELATION_CI.md; 23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md; additional 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md.

SDK/exporter details also checked against installed dependency metadata and google/adk/telemetry/setup.py and tracing.py. Reference documentation: https://opentelemetry.io/docs/languages/python/exporters/ and https://opentelemetry.io/docs/collector/configuration/. Installed version source, rather than moving documentation examples, governs implementation APIs.

### Current-state audit / preserved components

- backend/observability/{stages,errors,attributes,redaction,schemas,config}.py and README.md own inert contracts. Package import must stay inert.
- Settings.observability_config is lazy, uses Settings' injected mapping and sanitized ConfigurationError, and is unused by runtime. No second environment reader or Secret Manager lookup for telemetry.
- api/app.py has one _lifespan: validate_runtime_database_configuration FIRST, sanitized dialect logging, then best-effort warmup_shared_model, then yield. Preserve database validation precedence, warmup behavior and routes. No shutdown hook currently exists.
- chat_service.execute_turn_events/_run_turn_events, persistent driver Task context, locks, run task handles, sync/SSE consumers, terminal cleanup and rewind remain canonical. No edits planned there.
- perf_timing.py retains monotonic timing and callbacks, run-keyed pending call bookkeeping and backend.perf logging. M1 does not interpret broad boundaries as fine stages, fabricate TTFT or replace model tracking.
- run_trace.py is a sanitized, count-allowlisted, deduplicated public trace.step emitter, not an active-run registry. Preserve it and public SSE vocabulary.
- knowledge/diagnostic_trace.py is a run-scoped domain producer; its bounded query/error/title/command data is still prohibited for general telemetry. Never export snapshots wholesale. Its later convergence belongs to M4/M5 and privacy hardening.
- No tracked Terraform, Collector, Dockerfile, deployment YAML or infrastructure convention was found by file inventory. Use infra/observability; do not create future dashboard/alert/billing directories merely to match the diagram.
- requirements.txt pins google-adk 1.33.0/google-genai 1.75.0 and framework/database packages but no direct OTel packages. Installed ADK supplies API/SDK/OTLP HTTP 1.41.1, proto/common 1.41.1 and semconv 0.62b1. ADK constrains API/SDK <=1.41.1. Installed GCP Python exporters are ADK dependencies, not the SLOPANOC export path.
- ADK uses global tracers and offers maybe_set_otel_providers. Do not call its provider bootstrap or enable ADK cloud/genai instrumentation. Its tracing source contains content-bearing attributes and log events; safe export filtering is mandatory even without new application instrumentation.

### M0 contract reuse and necessary compatible extensions

Reuse Stage, RunStatus, ErrorCode (TRACE_EXPORT_FAILED, METRIC_EXPORT_FAILED, TELEMETRY_DROPPED), Attribute, schema version constants, ResourceMetadata, LogRecord, centralized count-only redaction and validate_metric_labels. Never add a parallel vocabulary/config system. Preserve public/legacy mappings and inert financial/timeouts contracts.

1. Extend the SAME ObservabilityConfig/SETTING_NAMES with exporter mode (otlp default when enabled, local, none), explicit OTLP HTTP protocol, finite timeout/queue/batch/flush settings and trusted deployment metadata. Endpoint is required only for otlp. Existing enabled-with-endpoint configurations retain meaning; omitted flags still disable telemetry. Production enabled mode requires otlp and HTTPS/private authenticated transport; local/none are development/test modes. Initial sample rate stays 1.0. Reject contradictory flags, unsupported protocols, invalid endpoint/path/port and unbounded values with sanitized errors.
2. Add TELEMETRY_SCHEMA_VERSION to RESOURCE_ATTRIBUTES with a controlled serialization of integer schema constant 1 into ResourceMetadata's existing Code representation. Keep IDs outside resources; preserve version-1 event/financial schemas. Record additive changes in doc 02 and compatibility tests. Do not silently widen all resource values.
3. Extend the existing effective-config DTO through discriminated, typed safe variants for strings/enums/booleans, retaining old numeric/boolean entries, owners and forced read_only=True. Endpoint is represented only as configured boolean, never its URL. This is a pure internal projection, no API/UI authorization bypass.
4. LogRecord currently permits only Stage for event/message. Register typed operational telemetry event names in the same schemas/contracts owner for startup/export failure/drop/shutdown and safe legacy log projections. Do not pretend lifecycle events are turn stages, accept arbitrary text, or overload unrelated error codes. Any log-export-specific code addition needs documented canonical error review and doc/code test updates first; otherwise use TELEMETRY_DROPPED for lost log delivery. New optional schema fields remain additive; any breaking change requires explicit schema version/compatibility review.

### Proposed architecture / initialization design

Provider factory and lifecycle handle in backend/observability/runtime.py; signal helpers in tracing.py, metrics.py and logging.py; exporter adapters under exporters/. Contracts never import runtime or backend orchestration. Runtime accepts validated config, metadata, clock and injected exporters/providers; correlation accepts trusted run-ID supplier injection from the application rather than importing api.turn_context into the package.

_lifespan sequence: existing DB validation first; resolve the single Settings config; construct/reuse telemetry handle and logging foundation; emit safe startup record; perform unchanged warmup; yield; finally release the handle with bounded flush/shutdown, including startup exceptions after telemetry acquisition. Initialization does not ping Collector or fetch cloud metadata/credentials. Malformed explicit config is a sanitized startup error; missing/unreachable exporter is degraded observability and never a user-turn failure.

Production has one process-owned provider set, initialized under a lock and reused for identical config/resource fingerprints, with reference-counted lifespan leases. app.state holds the lease. Install providers once only; repeat initialization adds no processor, handler, thread or metric reader. Global provider setters cannot be reset safely: foreign already-installed providers cause a sanitized ownership/config error, never a second export pipeline. Conflicting active configurations are rejected. After final shutdown the process-global runtime is terminal; production restart requires process restart. Do not attempt private global reset for repeated TestClient sessions. Tests primarily use provider factories with private explicit providers; global ownership/lifecycle tests execute in fresh subprocesses, including disabled/foreign-provider cases. Repeated lease tests keep the process runtime alive until the last lease is closed.

Disabled: no SDK installation, exporter/client/thread/handler or network activity; wrapper calls return no-op behavior. Enabled none mode: SDK with no remote exporter/readers requiring networking. Enabled local mode: injected in-memory capture or bounded, sanitized local JSON exporter (not arbitrary console dumps); no Collector/cloud required. Enabled otlp: OTLP HTTP/protobuf traces, metrics and logs through the one configured base endpoint and explicit /v1/traces, /v1/metrics, /v1/logs paths. No direct Python GCP exporters, environment-autoconfiguration or baggage import. Use W3C TraceContextTextMapPropagator foundation only; browser/request propagation and root-turn ownership are later milestones. Do not add FastAPI/ASGI auto-instrumentation in M1 because it would add request/SSE spans and raw URL/header capture before root ownership is settled.

Resource built from explicit trusted Settings values: service.name, service.version, deployment.environment.name, cloud.region, slopanoc.git_sha, slopanoc.release_id, slopanoc.cloud_run_revision and slopanoc.telemetry_schema_version. Settings may normalize supplied K_REVISION; no metadata-server detector or live Cloud Run required. cloud.provider=gcp and cloud.platform=gcp_cloud_run only for declared Cloud Run deployment, never fabricated locally. Required service/environment/schema always present; unavailable release/region/revision omitted. Validate bounded deployment identifiers, never place release hashes or run IDs in metric labels. Preserve slopanoc semantics rather than accepting arbitrary OTEL_RESOURCE_ATTRIBUTES.

Batch asynchronous trace/log export and periodic metric reader share resource and instrumentation scope/schema ownership. Proposed explicit defaults: export request timeout 2s, trace/log queue 2048 records, batch 256, schedule 5s, metrics interval 30s, aggregate shutdown budget 5s; validate positive finite caps and batch <= queue. Initial sampler AlwaysOn; no later sampling policy implemented. Export retry/backoff must remain within total configured timeout; Collector owns longer retries.

Shutdown uses one monotonic total deadline across trace/metric/log force_flush and shutdown. Run blocking SDK work off the event loop, pass remaining timeout wherever supported, avoid unbounded ThreadPoolExecutor context-manager joins. Bound adapters whose SDK shutdown lacks a deadline; ensure no retry worker or atexit hook extends the budget. A cancelled asyncio wait alone does not stop a worker: verify pinned SDK internals and use bounded daemon cleanup where necessary with explicit loss counters. Clean owned handlers/leases once; preserve unrelated clients/tasks. Slow/hanging exporters must be tested with wall-clock upper bound and a subprocess exit bound, not merely mocked timeout arguments.

### Privacy, correlation and self-observability

Central allowlist projection applies BEFORE queueing/serialization for every signal, including third-party ADK spans/events/links/resource attributes, status descriptions and logs. Drop unknown instrumentation scopes in M1; only the SLOPANOC foundation test/runtime scope exports until later adapters explicitly register safe producers. Do not queue raw ADK spans then filter in the exporter. Implement a processor/facade gate that transforms approved records into safe SDK export records without mutating ADK behavior. Disable automatic exception recording on owned foundation spans; never forward raw exception messages/stacks. Metric reader/view boundary rejects unregistered instruments/label keys/values; foreign third-party metrics cannot bypass the policy.

Tracer IDs are read from the current valid SpanContext; logs outside a trace have null correlation rather than fabricated IDs. Metrics correlate through permitted exemplars/context, not trace/run label dimensions. Trusted run ID is optional and supplied by existing execution context later; no new registry or run generation in M1.

Measure attempts, success/failure, dropped records, queue occupancy/capacity, shutdown loss and export duration. Register fixed operation values trace_export/metric_export/log_export and bounded outcomes; use current allowed environment/operation/status/error_code label keys. Health counts come from actual adapter/queue outcomes, including overflow (SDK batches may silently evict); test observed drop counts, never invent sent counts from enqueue counts. Never recursively export/log a failure through the same failing handler. Maintain bounded process-local health counters plus rate-limited safe local JSON degradation records when metric export is down. This is health cache only, not cross-instance run truth. Collector provides its own receiver/exporter/queue/memory/retry health. Sustained-degradation alerts and Health UI wiring belong to M9/M17/M8; counters/config support them now.

### Structured logging foundation

Production uses one sanitized JSON formatter/handler arrangement for application and inherited Uvicorn/ADK logs, configured idempotently in lifespan; controlled server logging config must prevent independent plain-text handlers and raw Uvicorn request paths. No basicConfig(force=True) or blanket removal of unrelated handlers. Inspect current root/child handlers, propagation and duplicate destinations during implementation. Local development can retain the current readable diagnostic format; production serialization never passes getMessage()/exc_info wholesale.

Canonical fields: timestamp UTC, severity, service, environment, safe static message/event_name, nullable error_code/trace_id/span_id/run_id; conditional typed safe fields. Map standard severity and preserve expected-policy versus failure distinction. Registered structured records reuse LogRecord. Legacy free-text records become a safe static operational event with bounded registered logger/severity and explicit approved numeric projections only; do not regex-scrub and export the original text. Existing perf producer code stays intact; foundation may serialize known safe timing fields without mapping legacy marks into canonical stages.

One owning boundary records sanitized error classification; outer layers correlate without duplicate full exceptions. M1 adds no raw detailed stack export or application-wide callsite refactor. Production stdout JSON is a local fallback, while OTLP logs go through Collector to Cloud Logging. To avoid duplicate normal Cloud Logging ingestion, deployment design must specify a narrowly scoped exclusion for primary application stdout copies, keeping degradation fallback and access/security logs as appropriate. Exclusion authoring belongs to local IaC; applying it needs separate approval. Test no duplicate handler processing and recursive exception emission. Cloud Logging project-qualified trace field translation belongs to Collector/export configuration, not application vendor business logic.

### Dependency / semantic-convention ownership

Plan exact requirements.txt pins matching the inspected ADK-compatible environment:
- opentelemetry-api==1.41.1
- opentelemetry-sdk==1.41.1
- opentelemetry-exporter-otlp-proto-http==1.41.1
- opentelemetry-exporter-otlp-proto-common==1.41.1
- opentelemetry-proto==1.41.1
- opentelemetry-semantic-conventions==0.62b1

No FastAPI/ASGI/logging instrumentation dependency is used in M1: explicit lifecycle and safe correlation bridge avoid automatic capture. If later required, instrumentation/core/ASGI/FastAPI/logging packages must be pinned as a compatible cohort, reviewed before adding. Do not enable google-adk[otel-gcp] or genai auto-instrumentation. Document semantic mappings/schema URL, including M0 gen_ai.provider.name versus ADK's older gen_ai.system, without exporting unreviewed vendor fields. Upgrade requires changelog, compatibility/dependency-resolution review, dashboard/query review and updated schema/redaction/lifecycle tests; changing requirements alone is insufficient. pip check and fresh disposable-environment resolution validate pins during implementation. No package installs in this planning pass.

### Collector and infrastructure design

Author infra/observability/collector/config.yaml (production), config.local.yaml (offline validation), README.md and a version/digest manifest. Choose a pinned Collector distribution containing googlecloud exporter, OTLP, memory_limiter, batch, transform/filter, resource, health_check and self-metric support. Exact image/version/digest must be verified against official component docs and locally validated before marking config PASS; no floating latest. No arbitrary image pin invented in planning.

Pipelines: OTLP HTTP (4318) and optional gRPC (4317) receiver; memory_limiter first; fail-closed signal-specific attribute/body/resource filtering; deployment resource enrichment only for missing approved values; batch; googlecloud exporter targeting Cloud Trace/Monitoring/Logging. Sanitization removes credentials/content and unregistered data; never retain raw status/error descriptions or request body. Configure bounded memory/queues, export timeouts, retry initial/max intervals and elapsed cap, health extension and self-telemetry endpoint bound privately. Validate actual supported syntax in the chosen binary; YAML parsing alone cannot prove exporter/components valid.

Initial traces remain 100%. Document tail_sampling support and the later versioned M15 policy, but no lossy tail sampler or cost-anomaly classifier enabled. Metrics/logs are not trace sampled; accounting has no Collector path dependency.

Proposed hosting baseline: Collector sidecar in the existing Cloud Run service template, application OTLP HTTP to loopback; immutable image/config artifact, health/startup probes, resource limits and sufficient CPU for background export. This fits private ingress without public OTLP, but existing production service configuration is not inspected remotely in this pass. Verify sidecar lifecycle, CPU allocation, loopback allowance and reliable post-request flush before selecting deployable settings; externally dedicated Collector is not silently substituted. HTTPS is required for external endpoint; explicit Cloud Run loopback sidecar HTTP exception must be typed/validated in the same config and documented, not a blanket insecure override. No app database/ledger changes.

Author infra/observability/terraform/{versions.tf,variables.tf,collector.tf,outputs.tf,README.md} only for current Collector config/deployment/service identity and necessary environment wiring. Adopt existing service ownership if discovered; do not author a competing resource that would replace the whole Cloud Run service. Until ownership is verified, provide a reviewed sidecar/config module and explicit integration inputs, with no import/state operation. Pin Google provider/Terraform constraints and lock dependency choices during implementation. Keep environment inputs distinct dev/staging/production; no plaintext keys, generic remote backend or auto-apply scripts. Application metadata/authentication uses supplied deployment policy/ADC, never new service-account keys.

No dashboards, alert policies, BigQuery datasets, billing exports, Cloud SQL migrations, retention jobs, budgets or profiling resources in M1. Terraform fmt/validate only on isolated local copies with backend disabled and no remote/provider operations; if provider initialization/download cannot be isolated, record NOT RUN instead of unsafe validation. Terraform plan/apply/import/state operations are not in the local test plan.

### Settings / UI impact: required five-part evaluation

1. M1 creates effective flags, endpoint-configured boolean, environment/service/exporter mode/schema version and safe local exporter health information for future Settings → Observability & FinOps Overview/Integrations/Diagnostics.
2. Extend existing typed effective-config contracts in schemas.py and pure config projection; no HTTP route. Configuration version must derive from canonical safe effective values, excluding endpoint secrets. Status must distinguish configured from actual healthy delivery.
3. UI implementation belongs to M8, protected data APIs to M7; no frontend/navigation change now. Existing layout/components remain owners.
4. Future API access requires server RBAC; FinOps gains no conversation/Teams/raw diagnostic data. Internal projection does not authorize routes. Current dev identity is not production authentication.
5. IaC/environment values are effective read-only, including Admin. No UI mutation/config endpoint.

### Expected files / implementation sequence (NOT EXECUTED)

Modify backend/api/app.py, backend/config/settings.py, requirements.txt; extend backend/observability/{config,attributes,schemas,redaction}.py and README.md; docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md for compatible ownership/extensions; canonical errors/spec only if log-specific code is justified; update this execution record.

Add backend/observability/runtime.py, tracing.py, metrics.py, logging.py, exporters/{__init__,otlp,local}.py; backend/tests/test_observability_runtime.py, test_observability_exporters.py, test_observability_logging.py, test_observability_collector.py. Extend current contract/policy/settings tests and targeted lifespan tests in test_api_app.py/test_model_warmup.py. Add only Collector/terraform files above; no frontend or migrations.

Sequence: (1) reinspect status and refine additive contracts/global provider policy; (2) pin dependencies and verify SDK internals/ADK safe scope gating; (3) implement private provider factories/modes/resource/health/safe logging; (4) integrate sole lifespan and bounded shutdown; (5) author and validate pinned Collector/local sink plus current-scope IaC; (6) run isolated unit/integration/regression gates, repair and individually evaluate exits; (7) record local and cloud status separately and stop before M2.

### Validation plan and commands (NOT RUN)

Before imports/tests strip inherited SLOPANOC database/gateway/secret/billing/provider settings, use injected settings/fake models/secret clients and explicit isolated SQLite; deny outbound AF_INET/AF_INET6 except allowlisted loopback test receiver. No real warmup, remote DB, gateway, ADC or cloud exporter allowed. Existing conftest alone is insufficient proof. Use the prior network-denial guard where available after inspecting it; create temporary local guard if absent. Audit every selected fixture before running.

```sh
.venv/bin/python -m pytest -q backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_runtime.py backend/tests/test_observability_exporters.py backend/tests/test_observability_logging.py backend/tests/test_observability_collector.py
.venv/bin/python -m pytest -q backend/tests/test_api_app.py backend/tests/test_api_streaming_endpoint.py backend/tests/test_model_warmup.py backend/tests/test_runtime_database_policy.py backend/tests/test_startup_import_graph.py backend/tests/test_settings_secret_caching.py backend/tests/test_settings_knowledge_database_url.py
.venv/bin/python -m pytest -q backend/tests/test_perf_timing.py backend/tests/test_run_trace.py backend/tests/test_streaming_events.py backend/tests/test_p2_model_call_correlation.py backend/tests/test_d2_merge_adk_activity_events_context_lifecycle.py backend/tests/test_chat_service_turn_context_lifecycle.py backend/tests/test_chat_service_streaming.py backend/tests/test_chat_service_rewind.py backend/tests/test_api_run_cancellation.py backend/tests/test_final_answer_persistence_and_consistency.py backend/tests/test_command_egress_boundary.py backend/tests/test_approval_security_contract.py backend/tests/test_context_assembly.py backend/tests/test_api_ownership.py
.venv/bin/python -m compileall -q backend/observability backend/api/app.py backend/config/settings.py
.venv/bin/python -m pip check
npm test -- src/api/streamChat.test.ts src/api/sseParser.test.ts src/api/runBackendChat.test.ts src/components/conversation/RunTrace.test.tsx src/components/conversation/CurrentActivity.test.tsx src/state/AppState.reducer.test.ts
npm run build
terraform -chdir=infra/observability/terraform fmt -check -recursive
terraform -chdir=infra/observability/terraform validate
git diff --check
git status --short
git diff --stat
```

Terraform validate requires previously audited isolated backend-disabled provider initialization; do not run against unknown backend. Collector tests run chosen pinned binary's validate subcommand for production config with dummy values and local config, no exporter connections/ADC. Local integration runs only config.local.yaml and a loopback OTLP capture sink with no googlecloud pipelines. Capture decoded trace/metric/log payloads and validate known resource/correlation attributes; do not persist real telemetry exports. Binary/image acquisition and exact invocation documented once pinned; unavailable tool means validation NOT RUN, never static PASS masquerading as binary validation.

Test enabled/disabled/local/none; repeated leases/conflict/foreign-global provider and fresh-process isolation; malformed finite/flag/endpoint config; metadata missing/present/trusted K_REVISION; pin/schema compatibility; OTLP explicit endpoints/timeouts; safe span/context/log/metric correlation; in-memory and local Collector sample span export; fake-token/request/Teams/exception/event/link leakage denial; unregistered ADK scopes/instruments rejected before queue; queue overflow and exact health counters; returned failure, thrown exception, retry timeout and hanging exporter; total flush/shutdown/exit bounds; logging recursion/double emission; normal/error/cancelled fake user-turn output and governance/SSE behavior during outage unchanged. No live GCP integration tests.

M0 inert import assertions continue for contract modules; existing AST architecture assertion must accommodate new runtime modules importing SDK while still forbidding orchestration imports and automatic SDK activation on package import. Do not weaken existing privacy/cardinality contract tests to make providers pass.

### Individually testable M1 exit criteria

All statuses below are **NOT IMPLEMENTED / NOT VALIDATED** at planning time. Historical M0 PASS is retained separately.

| Criterion and authoritative source | Required evidence / classification |
|---|---|
| App boots OTel on/off (roadmap M1) | Context-managed app lifespan with injected DB policy/model and private/fresh-process providers in disabled, local, none and otlp modes; no duplicate lifecycle setup. LOCAL gate. |
| Sample span exports (roadmap M1) | Known safe span captured through actual OTLP HTTP encoder and loopback receiver, plus pinned local Collector pipeline forwarding to local sink. Memory exporter alone insufficient. LOCAL gate; GCP delivery remains pending. |
| Exporter outage never crashes a user turn (roadmap M1) | Throwing/failure/unreachable/slow exporter injection with fake sync/SSE turns produces unchanged terminal output/governance and bounded latency; failure/drop health recorded. LOCAL gate. |
| Trace/metric/log provider and correlation foundation (03/12/18) | All three providers use same metadata and valid span IDs; no IDs as metric labels; safe log outside span has null IDs; no automatic model content/export. LOCAL gate. |
| OTLP/local/no-op and validated resource/config (02/03/12/22) | Explicit mode tests, all applicable release/schema attributes, missing local attributes omitted; canonical config/pin tests pass. LOCAL gate. |
| Structured JSON / privacy / exception ownership (09/18) | Required-field/severity assertions, fake-secret adversarial records from owned/legacy/ADK paths, no raw content before queue, no duplicate exception/handler export. LOCAL gate. |
| Exporter health and bounded queue/shutdown (01/12/16) | Actual failures/overflow/drop/queue/retry metrics, local degradation fallback without recursion, wall-clock cleanup and subprocess exit bounds. LOCAL gate. |
| Mandatory Collector topology authored (12/16/23) | Source-controlled pinned production pipelines with required processors/GCP destinations/retries/health, binary validation plus safe local pipeline test; initial 100% capture. LOCAL configuration gate, not deployed proof. |
| Production topology functioning (16 and applicable production DoD) | Approved deployed Collector, Cloud Trace span, Monitoring metric and Logging correlated JSON, authenticated transport/health/CPU/shutdown proof. CLOUD-PENDING; never claim runtime PASS without deployment. |
| Import/build / applicable regressions / hygiene (AGENTS/13/15) | Listed suites/builds pass after fixture audit; scoped tracked/untracked review, no secrets/unrelated work. LOCAL gate. |
| Scope, effective Settings policy and ownership complete | Pure safe read-only DTO and five-part UI review; no M2+ functions, registry/accounting/UI/security bypass. LOCAL gate. |

M1 can be labelled IMPLEMENTED AND LOCALLY VALIDATED only after all local gates pass. Roadmap's sample span criterion can be demonstrated locally, but production topology is also an M1 build requirement; roadmap does not explicitly waive production deployment validation. Conservatively Ready for Next Milestone remains **NO** until approved applicable production topology validation is complete. If a later authoritative roadmap update explicitly separates deployment from advancement, record it; do not infer a waiver. Full end-to-end scenario traces/accounting/SLO/retention/profiling DoD items are assigned to later milestones, not silently counted as M1 failures or implemented early.

### Cloud / infrastructure changes required — future, NOT AUTHORIZED

Cloud deployment status: **NOT EXECUTED — USER APPROVAL REQUIRED**. No cloud, database, IAM, billing, Terraform state or external-service mutation/read session was attempted. Local files are design artifacts only.

Pending production operations after local review: deploy pinned Collector sidecar/config; update the owned Cloud Run service template with sidecar probes/resources/CPU/OTLP endpoint and release metadata; supply approved identity and least-privilege telemetry write access to Trace/Monitoring/Logging; enable destination APIs only if absent; configure narrowly scoped duplicate-log exclusion if needed. Terraform apply would require its own exact reviewed plan and target. No billing export/BigQuery/Cloud SQL/Graph operation required by M1.

Exact project/account/environment/service, IAM delta, artifact transport and provider/image pins are unknown in this local planning pass. Before proposing ANY execution, supply exact command/action, target, resources, current-M1 need, operational/security/cost impacts, interruption/data risk, rollback and DEV/STAGING/PRODUCTION designation under AGENTS.md. No permission request is made now because no concrete deployment exists to approve. Expected later impact: new Collector CPU/memory and telemetry ingestion cost; background CPU may affect Cloud Run cost; no intended DB schema/data change. Recovery is reviewed prior service revision/config rollback and restoration of exact IAM/exclusion changes, subject to separate approval. This paragraph grants no mutation permission.

### Planning results, risks, limitations and readiness

Implementation performed: **NONE**. Actual file changed this pass: **.agent/OBSERVABILITY_EXECUTION.md only**. Baseline unrelated/uncommitted M0 files and documentation preserved. Planning validation is source/spec/dependency metadata audit plus Git/content review; no app startup, tests, builds, package installs, Collector execution, Terraform or live-cloud validation run. Large combined source output was truncated; lifecycle/schema/import assertions and SDK ownership/content surfaces were reread with targeted commands. No implementation defect repaired or tests claimed PASS.

Risks: ADK content-bearing telemetry and global provider ownership; unavailable pinned Collector binary/Google exporter log support; SDK queue eviction observability and non-timeout shutdown APIs; production server logging duplicates and raw legacy text; sidecar/CPU/Cloud Run service IaC ownership not yet verified; additive schema/config evolution; missing deployment metadata and production authentication. Local implementation can resolve runtime/privacy/version/tooling risks without cloud access. Production target/hosting/IAM proof blocks deployment and advancement, not starting authorized local implementation. No architecture redesign is proposed.

**Ready To Implement M1: YES** — a subsequent implementation request is required; this turn authorizes planning only.
**Ready for Next Milestone: NO** — M1 planned, implementation/validation pending, M2 blocked.

## M1 implementation start — 2026-10-07

User authorizes M1 local implementation only; no deployments/external mutations/M2.
Rechecked current package, lifespan, test fixtures and installed SDK metadata. The
explicit user M1 gate distinguishes local completion from live deployment; use that
gate for local status, while recording all production proof as NOT LIVE-VALIDATED.
Google-Built Collector 0.160.0 is the current documented baseline; verify its component
set and validate matching native development binary because Docker daemon is absent.
Add cohesive exporters/queue.py and resource construction in runtime.py if needed.
Terraform will be a provider-free sidecar integration contract (outputs only), avoiding
speculative Cloud Run ownership/IAM resources. It has no backend/provider/remote reads.
No global app logger rewrite: install safe production formatting on existing owned
backend/Uvicorn handlers with restoration; new foundation logs use explicit safe bridge.
Global providers are deliberately installed once; foreign scopes blocked before queues.

## M1 actual implementation and validation — 2026-10-07

This section supersedes M1 planning-only/pending statuses above. User approved M1
local implementation only. M2 was not started; no deployment or external mutation.

### Implementation performed

- Explicit Runtime factory, tracer/meter/logger providers and process-owned lifespan
  leases. Identical active acquisitions reuse providers, readers and handlers;
  foreign/conflicting/terminal global ownership fails with sanitized diagnostics.
  Private providers in ordinary tests, irreversible installation in fresh subprocesses.
- Single FastAPI _lifespan integrates after mandatory DB validation and before unchanged
  model warmup. Telemetry closes in finally, including failed warmup. Startup logging
  follows formatter setup. No route/chat_service/execution/agent/tool changes.
- Disabled, none, local and OTLP HTTP/protobuf modes; validated finite queue/batch/
  timeout/flush settings, production TLS or explicit loopback sidecar exception.
  Settings is still the only environment reader, including K_REVISION normalization
  and denial of implicit SDK provider selection. No telemetry secret/metadata lookup.
- Resource/process release attributes from trusted settings; missing local values
  omitted, schema 1 represented as process string. Read-only typed effective Settings
  entries contain only endpoint-configured boolean and safe values/hash.
- Typed span/log facade, scope filtering and centralized metadata projection before
  trace/log queues. ADK/foreign scopes never queue. Raw exceptions/events/links,
  scope attributes/version/schema, tracestate, log extras and unregistered metric
  descriptions/units/labels are rejected or normalized. No content auto-instrumentation.
- Explicit asynchronous bounded trace/log queues with actual accepted/exported/
  failed/dropped/queue observations; fixed-cardinality health gauges. Rate-limited
  daemon JSON fallback does not recurse or block a failed exporter/shutdown.
- Total monotonic shutdown/flush deadline with daemon isolation of unsupported/hanging
  SDK shutdown/metric APIs, no SDK atexit hooks or indefinite executor cleanup. Confirmed
  queued loss counted; in-flight delivery stays unknown after timeout. Existing model
  clients/tasks remain owned by their existing lifecycle.
- Structured JSON foundation and safe operational log bridge; production formatter
  handles existing backend/root/Uvicorn/ADK/OTel handler formats without rewriting
  producer callsites. Raw message/args/exception evaluation is denied; formatting
  changes restored on lease close. Legacy structured stdout and new OTLP operational
  logs are distinct paths pending later producer convergence/hosting verification.
- Pinned Google-Built Collector sidecar production configuration and matching native
  contrib development validator. OTLP, memory limiter, fail-closed scope/value/attribute
  processing, resource enrichment, GCP resource detection (production only), batching,
  bounded sending queue/timeouts, health and loopback self-telemetry. googlecloud
  destinations remain Trace/Monitoring/Logging. Initial capture 100%, no tail sampling.
- Provider-free Terraform sidecar/config integration outputs, environment distinction,
  always-allocated CPU contract and health startup probe. No cloud resources, providers,
  data sources, backend, state import or competing Cloud Run service declaration.

No root-turn hierarchy, active-run registry, DB persistence/migrations, model/agent/
tool/dependency instrumentation, financial accounting, protected API, UI, SLO engine,
dashboards or later milestone functionality was implemented. M0 financial schemas,
canonical stages/statuses/error codes and existing timing/run/domain traces unchanged.

### Actual M1 files changed

Modified existing local files:
- backend/api/app.py — sole lifespan acquisition/release.
- backend/config/settings.py — M0 accessor docstring updated; no second config reader.
- requirements.txt — explicit OTel cohort pins.
- backend/observability/__init__.py — explicit inert-import documentation.
- backend/observability/config.py — modes/caps/release data, safe effective projection.
- backend/observability/attributes.py — schema version allowed as resource attribute.
- backend/observability/redaction.py — typed/count-only span projection.
- backend/observability/schemas.py — compatible typed operational logs/effective settings.
- backend/observability/README.md — actual lifecycle/export/privacy/version ownership.
- docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md — compatible M1 extensions.
- .agent/OBSERVABILITY_EXECUTION.md — plan, decisions and evidence.

Added:
- backend/observability/runtime.py
- backend/observability/tracing.py
- backend/observability/metrics.py
- backend/observability/logging.py
- backend/observability/exporters/{__init__,queue,local,otlp}.py
- backend/tests/_m1_otel.py
- backend/tests/test_observability_runtime.py
- backend/tests/test_observability_exporters.py
- backend/tests/test_observability_logging.py
- backend/tests/test_observability_collector.py
- infra/observability/collector/{config.yaml,config.local.yaml,version.json,README.md}
- infra/observability/terraform/{versions.tf,variables.tf,collector.tf,outputs.tf,README.md}

The M0 package/test/doc/AGENTS files were already untracked at baseline. Git diff
therefore shows only tracked changes (app.py, Settings accessor and requirements),
not the entire implementation. New/untracked source/config/docs were inspected
explicitly; no user work discarded, reset, committed or unrelated refactor performed.
No generated sensitive payload, credential, remote export or database file added.

### Dependencies and tooling

requirements.txt pins opentelemetry-api/sdk/exporter-otlp-proto-http/proto-common/proto
at 1.41.1 and semantic-conventions at 0.62b1, matching installed ADK 1.33.0 constraints.
No Python dependency installation/upgrade was needed. No FastAPI/ASGI/logging/GenAI
auto-instrumentation dependencies added. Schema URL uses pinned Schemas.V1_40_0;
SDK version is not the semantic schema version. Upgrade ownership documented.

Production image tag: us-docker.pkg.dev/cloud-ops-agents-artifacts/google-cloud-opentelemetry-collector/otelcol-google:0.160.0.
Source component list checked at v0.160.0. No production image pulled/run/pushed.
Native development archive: otelcol-contrib_0.160.0_darwin_arm64.tar.gz, version 0.160.0,
SHA256 ceb5309ba16f2587dbef765d54e15c803354d038b0495b0b691e1eb9876d17c9,
compared with the public release checksum and recorded in version.json. Test binary
/private/tmp/m1-collector/otelcol-contrib, outside repo. This validates matching
upstream components, not the Google image in production. Google image digest and
actual environment compatibility must be reviewed before promotion.

Terraform 1.14.0 public local binary /private/tmp/m1-terraform/terraform used only
for fmt/validate. Module needs no initialization, providers/backend or state access.
Docker client exists but daemon is absent; no Docker deployment/start attempted.
Public artifact/document downloads are read-only, not external resource mutations.

### Exact validation commands and results

The temporary /private/tmp/slopanoc-m1-test-guard/run_tests.py invokes the following
pytest selections with inherited SLOPANOC variables removed, explicit SQLite memory
URLs, warmup/Vertex disabled and sitecustomize denying non-loopback AF_INET/AF_INET6
connect/connect_ex. Fake gateway/model/secret fixtures audited/reused. Loopback binds
required approved sandbox escalation. Collector child environment is separately
minimal and local config has no GCP exporter/detector; production config only validated,
never started. No ADC, real model, shared DB, gateway or cloud integration exercised.

Final M0/M1 selection:
```sh
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_runtime.py backend/tests/test_observability_logging.py backend/tests/test_observability_exporters.py backend/tests/test_observability_collector.py
```
Result: **147 passed, 1 existing Starlette deprecation warning, 7.85 seconds**.
Includes the 95 original M0 cases and 52 M1 cases. Actual local OTLP encoder/receiver
proves trace/log/metric correlation and sentinel denial; pinned Collector forwards
all three signals to loopback receiver. Both production/local configs parse and pass
native validate. None/local/disabled and OTLP app boots, global idempotency/conflicts,
metadata, malformed config, secret-free effective config, ADK/SDK bypass privacy,
queue overflow, returned/throwing/HTTP-503 failures, fake sync/SSE turns, resource
identity, invalid labels/scope/tracestate/descriptions/units, failed startup cleanup,
hanging export/shutdown and blocked fallback writer covered.

Regression selection:
```sh
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_api_app.py backend/tests/test_api_streaming_endpoint.py backend/tests/test_model_warmup.py backend/tests/test_runtime_database_policy.py backend/tests/test_startup_import_graph.py backend/tests/test_settings_secret_caching.py backend/tests/test_settings_knowledge_database_url.py backend/tests/test_perf_timing.py backend/tests/test_run_trace.py backend/tests/test_streaming_events.py backend/tests/test_p2_model_call_correlation.py backend/tests/test_d2_merge_adk_activity_events_context_lifecycle.py backend/tests/test_chat_service_turn_context_lifecycle.py backend/tests/test_chat_service_streaming.py backend/tests/test_chat_service_rewind.py backend/tests/test_api_run_cancellation.py backend/tests/test_final_answer_persistence_and_consistency.py backend/tests/test_command_egress_boundary.py backend/tests/test_approval_security_contract.py backend/tests/test_context_assembly.py backend/tests/test_api_ownership.py
```
Initial regression-only result **324 passed, 2 existing deprecation warnings,
21.35 seconds**. After final application lifecycle/privacy repairs the same regression
selection and then-current 146-case M0/M1 suite ran together: **470 passed, 2 warnings,
25.30 seconds**. Subsequent metric description/unit/privacy changes were revalidated
with the final 147-case affected suite above; no application/SSE/governance changes
followed that 470-case pass. Final distinct backend coverage: 147 + 324 cases.

Other checks:
```sh
.venv/bin/python -m compileall -q backend/observability backend/api/app.py backend/config/settings.py
.venv/bin/python -m pip check
npm test -- src/api/streamChat.test.ts src/api/sseParser.test.ts src/api/runBackendChat.test.ts src/components/conversation/RunTrace.test.tsx src/components/conversation/CurrentActivity.test.tsx src/state/AppState.reducer.test.ts
npm run build
/private/tmp/m1-terraform/terraform -chdir=infra/observability/terraform fmt -check -recursive
/private/tmp/m1-terraform/terraform -chdir=infra/observability/terraform validate
git status --short
git diff --stat
git diff -- backend/api/app.py backend/config/settings.py requirements.txt
git diff --check
```
Compile/import coverage **PASS**; pip check **no broken requirements**. No fresh Python
resolution/install needed because all exact pins already installed and verified by
metadata tests. Vitest **211 passed, 6 files, 1.87 seconds**. TypeScript/Vite build
**PASS**, 2028 modules, 1.95-second Vite build. Terraform fmt/validate **PASS** without
init/plan/provider/backend/remote state. Scoped diff/untracked review and whitespace
check **PASS**. No lint/type-check tool was added; frontend type-check is in build.

Collector test invokes:
`/private/tmp/m1-collector/otelcol-contrib validate --config=<config.yaml|config.local.yaml>`
with dummy environment and five/ten-second subprocess bounds. Local execution uses
only config.local.yaml, random loopback ports and local sink. Healthy Collector SIGTERM
shutdown completed within the test's five-second bound. Production failure/termination
behavior remains NOT LIVE-VALIDATED.

Non-network primitive measurement after SDK imports: local initialization **0.653 ms**,
1000 spans **0.0368 ms/span** producer average, local shutdown **15.372 ms**. Unit gate
500-span average <10 ms also passes. These are local primitive measurements, not a
production CPU-overhead/load claim; <3% full-turn overhead belongs to later validation.

### Failures encountered / repairs

1. Initial import used outdated propagator path; corrected to pinned SDK
   opentelemetry.trace.propagation.tracecontext before validation.
2. SDK severity uses WARN/FATAL rather than JSON WARNING/CRITICAL. Mapping corrected;
   correlated warning logs now survive safe queue gate.
3. Sandbox denied local HTTP bind. Approved escalation used the same loopback-only
   guard; no external network access enabled for tests.
4. Collector OTTL regex escaping failed native validate. Replaced escaped dots with
   character classes, guarded nullable fields and used typed/static projections.
5. Collector's default gzip output initially failed receiver protobuf decode. Test
   sink now decodes Content-Encoding before capture; actual pipeline passes.
6. SDK empty-header argument adopted inherited auth env. Supply explicit protobuf
   header/session, deny redirects/proxy/auth inheritance and clear inherited client
   certificate adaptation; adversarial actual-transport tests pass.
7. Metric adversarial test initially reused an existing ObservableGauge and its SDK
   ignored the second callbacks, so filtered count was zero. Use a distinct meter
   version to exercise real invalid values; export filter and test now pass.
8. Review tightened total metric flush, scope/schema/version/tracestate normalization,
   fixed metric descriptions/units, trusted log resource matching, rate-limited daemon
   fallback and multi-batch draining. Added meaningful leakage/hanging tests and reran
   affected gates. No M0 contract failure waived or expected result weakened.

### Individual M1 exit criterion evaluation

| Criterion | Result | Evidence |
|---|---|---|
| Application boots telemetry disabled | PASS | Disabled factory/lifespan and existing health/app/warmup regressions. |
| Application boots telemetry enabled | PASS | Local/none private lifespan tests and actual OTLP outage app test; explicit global providers proven in subprocess. |
| M0 contracts reused | PASS | All 95 M0 cases pass, stages/statuses/error sets/schema versions unchanged; extensions in same owners. |
| Initialization idempotent / duplicate safe | PASS | Shared active global leases, no provider/reader/handler duplication; conflicts/foreign/terminal rejection tested. |
| Local/no-exporter/OTLP modes and config | PASS | All modes, strict config/TLS/loopback/caps and safe effective settings tests. |
| Tracer/meter/logger and resource metadata | PASS | Explicit providers, release/env/revision/schema assertions; missing local metadata omitted. |
| Local OTLP sample export proven | PASS | Actual HTTP protobuf capture plus SDK→native Collector→sink spans, logs and metrics. |
| Collector config validates | PASS | Native 0.160.0 validate on both configs and isolated local three-signal pipeline; matching Google component list checked. |
| Sensitive content protected | PASS | Pre-queue count/typed/scope projection, fake secrets/prompts/Teams/auth, SDK bypass/events/links/stack/tracestate/scope/description/units tests. |
| JSON/log/trace/span correlation foundation | PASS | Required fields/severity, same valid IDs, absent IDs outside trace, no raw exception duplication/new producer logging. |
| Exporter outage non-fatal | PASS | Thrown/returned/503/hanging failures; unchanged fake sync/SSE response and terminal events. |
| Bounded queues/retries and health | PASS | Exact overflow/failure/drop/occupancy counters, finite SDK timeout, native Collector queue caps, nonrecursive local fallback. |
| Bounded flush/shutdown | PASS | Monotonic deadline, thread/atexit isolation, process-exit bounds for hanging trace/metric exporters and blocked fallback. |
| Applicable regressions/import/build pass | PASS | 324 backend plus final 147 M0/M1 cases, 211 frontend, compile/pip/build/Terraform checks. |
| M2 unimplemented / scope preserved | PASS | No chat_service/perf/run/domain/agent/tool/frontend/DB edits; source and tracked/untracked inspection. |
| No cloud mutation | PASS | No deployment, GCP/Graph/IAM/billing/SQL/BigQuery/GCS/state operation; only local tests and public artifact reads. |
| No blocking local M1 defect | PASS | Required local gates pass after repairs; live limitations below explicitly separated. |
| Google-Built image/sidecar live behavior | NOT LIVE-VALIDATED | No production image execution or Cloud Run deployment. Native contrib component validation only. |
| Cloud Trace / Monitoring / Logging delivery | NOT LIVE-VALIDATED | No authenticated destination test; local sink only. |
| Production IAM/auth/CPU/billing/termination | NOT LIVE-VALIDATED | No target inspection/change; integration contract requires later verified exact-operation review. |

Readiness interpretation: the user's implementation request sections 26–27 explicitly
separate local M1 completion from deployment and define the local exit gate. Roadmap
M1 exits (boot on/off, sample export, non-fatal outage) are demonstrated locally. The
production-wide DoD/live destination checks remain pending and are not silently
reported PASS. This supersedes the planning-only conservative assumption that live
cloud deployment must block local advancement. It does not authorize cloud operations
or starting M2 in this M1-only task.

### Settings / UI impact: five-part evaluation

1. Safe effective flags/mode/endpoint-configured/service/environment/schema and runtime
   health state exist for future Overview/Integrations/Diagnostics Settings views.
2. Existing typed effective-config contract extended; no unrestricted URL/credential;
   configured state distinct from health delivery. No API routes or serialization bypass.
3. No M8 UI built; existing layout/navigation/components/frontend files unchanged.
4. Future M7 API/server authentication/RBAC still required; FinOps receives no content
   permission. Internal runtime/config objects grant no API capability.
5. Effective entries stay read-only, including Admin; IaC/environment ownership preserved.

### Cloud / infrastructure status and known limitations

Local implementation: **IMPLEMENTED AND LOCALLY VALIDATED**.
Cloud deployment: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Cloud Trace/Monitoring/Logging, IAM, Google image and production sidecar: **NOT LIVE-VALIDATED**.

Pending exact operations: integrate pinned/digest-reviewed Collector/config into the
owned Cloud Run template; set startup dependencies/probes, resource/always-allocated
CPU/billing choices and app OTLP/release/environment wiring; review existing identity
and minimum telemetry-write roles/APIs, changing only absent requirements if approved;
review necessary duplicate-log handling with narrowly scoped verified filters. Config
artifact/secret transport and any image build/push need their own explicit approval.
Project/account/service/environment are not assumed or accessed. No Terraform apply,
import, state operation, IAM/API/billing setting change, migration or GCP deployment.
No SQL/BigQuery/Graph/billing-export operation required for M1. Every future operation
must include AGENTS.md exact command/target/resources/need/operational/security/cost/
data-interruption/rollback/environment report and user approval.

Known limitations: production Google image/digest/actual service ownership and live
transport/IAM/CPU/termination unverified. Native validator is matching upstream contrib,
not Google image runtime evidence. self-metrics pull endpoint is local; dashboards,
alerts and external collection are later milestones. Legacy free text becomes static
JSON envelope and legacy producers are not fully converged into OTLP; host handlers
installed after lifespan setup need the safe formatter and review before production.
Deadline cancellation can abandon daemon cleanup with unconfirmed in-flight delivery;
telemetry is best effort, not accounting. Bounded local sinks are not durable. No full
turn traces or production overhead/load claim; browser propagation, run correlation,
authenticated diagnostics, persistent active state and accounting remain later work.

### Completion

M1 local implementation: **COMPLETE**.
M1 local validation: **PASS**.
Required local M1 exit criteria: **ALL PASS**.
Ready for Next Milestone: **YES** — local gate only; no cloud approval implied.
M2 status: **NOT STARTED**. Stop here under the user's M1-only scope.

## M2 — Root Turn Trace: current audit and implementation plan

Planning date: **2026-10-07**. Authorization: **M2 PLANNING ONLY**.
Status: **M2 — PLANNED — NOT IMPLEMENTED**. M3 remains blocked.
Only this execution record changed in this pass. No runtime, test, infrastructure,
schema, migration or frontend implementation was added. Historical M0/M1 sections
above remain evidence of their respective tasks, not the current M2 status.

### Entry gate and authoritative specifications

**PASS**: M0 completion records VALIDATED COMPLETE / Ready YES; M1 completion records
local implementation COMPLETE, local validation PASS, all required local exits PASS,
Ready YES. Actual M0/M1 modules/tests/configuration exist. M1 production Google image,
Cloud Run sidecar and Cloud Trace/Monitoring/Logging delivery are explicitly NOT
LIVE-VALIDATED. This plan does not certify production delivery from local evidence.
The latest M1 completion decision supersedes its historical planning-only readiness.

Read AGENTS.md, .agent/PLANS.md, this execution record, and docs/Telemetry/README.md,
00_MASTER_BUILD_CONTRACT.md, 01_TARGET_ARCHITECTURE.md, 02_TELEMETRY_DATA_CONTRACT.md,
03_INSTRUMENTATION_AND_RUNTIME.md, 04_RELIABILITY_TIMEOUTS_ERRORS.md,
05_STORAGE_APIS_AND_UI.md, 11_SLOPANOC_INTEGRATION_MAP.md,
12_IMPLEMENTATION_ROADMAP.md, 13_TEST_VALIDATION_ACCEPTANCE.md,
14_CODEX_EXECUTION_GUIDE.md, 15_DEFINITION_OF_DONE.md,
21_SLO_FORMULAS_AND_INITIAL_TARGETS.md, 22_RELEASE_CONFIG_CORRELATION_CI.md.
Also read privacy/logging contracts 09 and 18. Specifications define intent; the
source audit below defines actual available boundaries. No remote repository used.

### Objective, scope and safety invariants

One accepted execution creates one canonical `slopanoc.turn` span with trusted
support identity, real phase timing, execution-local progress and exactly one
terminal closure. Both sync and SSE use the existing ChatService pipeline.
Do not implement model/token/TTFT/retry/finish/cost contracts (M3), agent or tool
invocation instrumentation (M4), dependency spans (M5), deadlines/watchdog/periodic
heartbeats (M6), durable support APIs/Cloud SQL registry (M7), UI (M8), or accounting.
Existing perf model callbacks remain unchanged; their presence is not M3 completion.

Telemetry observes execution only: no routing, evidence selection, provenance,
approval, command authority, egress or session-history semantics may depend on it.
No remote mutation, authenticated cloud access, provider lookup or migration.
All IDs remain trace/record fields, never metric labels or resource attributes.
All exported events/attributes pass central typed validation before enqueueing and
Collector validation before forwarding. Never export exception strings/stacks,
prompts, responses, Teams bodies, attachment bytes/URIs, queries or diagnostic text.

### Current-state audit and reuse

| Actual source | Finding / reuse |
|---|---|
| backend/api/app.py | Sole lifespan acquires M1 Runtime after DB configuration gate and before unchanged warmup. Sync calls run_turn; SSE checks ownership before opening response, then relays execute_turn_events through Aclosing/format_sse. No browser completion acknowledgement exists. |
| backend/api/chat_service.py: execute_turn_events | Canonical accepted-turn entry. EventSequencer creates trusted UUID run_id; PerfTimer records acceptance. Currently yields run.started BEFORE creating _drive task, leaving a pre-task abandonment gap. _drive owns lock_for, Aclosing(_run_turn_events), queue relay and _TURN_DONE finally. |
| ChatService._run_turn_events | Actual complete business generator: session reload, continuations/stale-state cleanup, case checks, attachment preparation/linkage, route evaluation, Team Manager Runner, cleanup snapshots, deterministic response remediation/finalization, egress/provenance, durable final-answer write, message.completed and run.completed. |
| ChatService._run_turn_events error/finally | Runner exception becomes generic run_failure; CancelledError propagates. Session/preparation can fail before inner try; authority/enrichment/persistence/delivery occur AFTER runner finally. Root scope must enclose all of these. Persistence failures already emit error + run.completed(error). |
| ChatService._background_turns / _run_tasks | Execution/cancellation ownership, NOT telemetry registries. Preserve keyed (session_id,run_id), session locking, task references and removal callbacks. Explicit cancel validates ownership and cancels exact task; disconnect does not cancel work. Rewind uses same session lock and ADK history semantics. |
| backend/api/streaming_events.py | Existing sequenced public envelope carries run_id/session_id on every event; run.completed retains ok/error. Public Stage differs intentionally from canonical observability Stage. No new wire vocabulary required. |
| backend/api/run_trace.py | Per-execution sanitized trace.step emitter, consecutive dedup and recorded_steps for existing presentation persistence. Not a process/global active registry. Keep behavior; not owner of root/terminal state. |
| backend/api/perf_timing.py | Acceptance-anchored injectable monotonic PerfTimer, sequential marks, opaque delegation timings and existing streaming-aware model callbacks. Keep clock/legacy output; no broad rewrite or model-span bridge. |
| backend/api/turn_context.py / ADK drain | Existing trusted run ContextVar and content/evidence mailboxes are bound around runner only and reset before finalization. Persistent ADK drain task preserves async generator context; do not replace it with one task per next item. |
| backend/agents/team_manager/operational_routing.py | evaluate_operational_route is an actual server classification boundary; not a standalone TurnPlan engine. Observe its result without changing policy or exporting its text-bearing trace_view. |
| backend/agents/team_manager/case_context.py; backend/context/assembly.py | Instruction provider invokes ContextEngineeringBroker.assemble on linked-case path; assembled result is currently discarded and instruction rendering uses existing snapshot. Observe actual assemble entry/exit only, never change its selection or start calling it on other paths. |
| backend/agents/technical_authority_engineer/troubleshooting_threads.py | resolve_active_thread is actual fault-thread resolution, called by agent_tool.py; fault_id identifies its projection. No separate conversation thread_id or standalone conversation registry discovered. Preserve fault_id as fault identity; do not relabel it as thread_id. |
| backend/api/session_history_service.py | History turn_id is ADK invocation_id. _run_turn_events captures first event.invocation_id, late in execution; final-answer fallback currently searches prior active events. Telemetry must never borrow that fallback to claim a previous turn is this run. |
| backend/tools/knowledge/diagnostic_trace.py | Existing trusted run-keyed, locked diagnostic store snapshots/discards in runner finally. Contains bounded query/command/request/objective/title data that is not export-safe. Preserve OBSERVABILITY ONLY — NEVER EVIDENCE; no snapshot export or new detailed knowledge spans. |
| backend/observability/* | Reuse Runtime, tracer scope/pins, resource metadata, strict M0 schemas/stages/status/error sets, privacy/cardinality rules, queues/health/logging/local/OTLP exporters. Package lacks root lifecycle and active registry implementation. |
| infra/observability/collector/* | Both configs reject all span events/links and unknown span names; strip correlation/status/config attributes. Python SafeSpanProcessor likewise drops events and allows only Stage/OperationalEvent names. Merely adding root calls would silently lose traces. |
| infra/observability/terraform/* | Provider-free sidecar integration outputs, no backend/resources. Reuse without changes; M2 needs no Terraform operation. |

No concrete standalone TurnPlan/TurnAuthority class or backend/conversation package
was found. Do not invent their acceptance/selection stages from model events. M2
observes the server-owned planning/final-producer/provenance boundaries actually
present. Preserve later architecture requirements and explicitly record absent
phase applicability rather than claim those objects already exist.

### Canonical root ownership and implementation sequence

1. Extend existing tracing owner with registered span OPERATIONS distinct from
   lifecycle Stage: slopanoc.turn, request, session.load, attachments, orchestration,
   planning, thread.resolve, pending_interaction.resolve, context.select,
   finalization, persistence.final_answer and sse.complete. Keep Stage unchanged.
   Admit these operations and strictly validated lifecycle events in both Python
   and Collector gates; continue dropping raw SDK exceptions/links/free text.
2. Add reusable TurnTrace lifecycle/progress helper under backend/observability;
   acquire existing runtime by injection/process accessor, never initialize SDK in
   ChatService. Direct test construction defaults to inert/no-op tracing.
3. In execute_turn_events allocate sequencer/perf/lifecycle and start root once at
   acceptance, before run.started. Register/create _drive before first yield, with
   no suspension in between. This closes the current pre-task relay gap while
   retaining run.started as first wire event and all existing lock ownership.
4. Start span without leaving it attached across generator yields. _drive attaches
   explicit root context and trusted identity for its full lifetime, including lock
   wait; _run_turn_events adds thin observed phase calls. Relay tasks attach/reset
   correlation only around their own logging/delivery operations. No task borrows
   another task's ContextVar token. Root is a fresh backend trace in M2; do not
   accidentally adopt ADK/foreign spans or request baggage. W3C browser extraction
   and frontend propagation remain M18; M1 installed W3C propagator remains intact.
5. Root closes in _drive outer finally after generator close, business cleanup and
   lock release, before _TURN_DONE signals server execution completion. Span end,
   identity detach and cache removal each have independent fail-safe cleanup.
6. Task done callback is an idempotent safety backstop for cancellation BEFORE
   _drive's first instruction (a coroutine finally does not execute then). It also
   ensures relay sentinel is delivered in that case; no open root/cache leak.
   Explicit finalize_once owns terminality, preventing duplicate close/events.
7. Add only pure observation hooks to actual server classification, pending-read
   resolution, fault-thread resolution and Context Broker assemble. No ADK
   before_model/after_model or AgentTool invocation span changes. Keep execution
   order, return values, error handling and authoritative state writes identical.
8. Validate entire canonical fake sync/SSE paths, not a second synthetic executor;
   inspect tracked and untracked diff before local milestone completion.

### Identity and async/log correlation design

- OTel generates native trace_id/span_id; EventSequencer UUID remains trusted run_id.
  Session ID comes from the owner-validated session, never arbitrary header/baggage.
- At acceptance canonical ADK history turn_id does not exist. Use a typed temporary
  execution context with turn_id unknown (do not instantiate strict M0 Correlation
  with zeros/placeholders). Bind actual invocation_id from first current-run event;
  keep immutable thereafter. Root carries that ID on closure; earlier phases are
  joined by run_id/trace_id. No previous-history fallback for telemetry.
- If execution fails/cancels before any ADK invocation is observable, terminal
  telemetry uses an explicitly namespaced server ID `pre-adk:<run_id>` and a typed
  turn_id_origin discriminator. This is an execution-only identity, never a history,
  attachment or rewind key. Emit no false history association; document compatible
  optional origin contract in doc 02/schemas before implementation. Unknown initial
  identity and terminal fallback are intentional semantics, not a fabricated ADK ID.
- No new ADK run_async invocation_id argument: installed 1.33.0 documents it for
  resumption and generates new invocation IDs internally for nonresumable runs.
  No private Runner override/SDK patch. Retry/remediation sessions retain existing
  business run IDs while telemetry root stays this accepted turn's parent; never
  replace root identity with a remediation invocation.
- Thread ID remains absent where no distinct trusted conversation thread exists.
  Observe fault-thread timing and fault identity only where structurally available.
- Typed telemetry identity ContextVar is observational; existing evidence run binding
  keeps its runner-only lifetime. Structured safe_record/formatter obtains default
  run_id from the outer telemetry context, so early session/finalization logs correlate
  without extending evidence mailbox lifetimes or reading raw LogRecord args.
- asyncio child tasks inherit attached OTel/identity context; preserve drain task and
  copy_context/to_thread behavior. Shared context lifecycle stops accepting updates
  after terminality. Late detached child work must not reopen ended spans/cache.
- Enabled spans inherit M1 Git SHA/service version/release/revision/environment/region
  and schema resource attributes without duplicating those per span. Per-turn
  config_version uses M1 effective_configuration safe hash, identified explicitly
  as observability-config hash, NOT a version of all business configuration. Missing
  model/prompt/agent/tool/TurnPlan versions stay absent; M18 completes those contracts.
- Disabled/no-op mode never invents valid OTel IDs or creates exporters. Execution
  lifecycle works without trace IDs; strict TraceEvent/ActiveRun projections apply
  only when valid native correlation exists. Document enabled-mode coverage gates.

### Span/stage applicability map

| Operation | Canonical stages / actual boundary |
|---|---|
| request | request.received and request.validated at accepted canonical entry after caller ownership/schema validation; content/attachment semantic rejection observed separately, not falsely claimed validated. Lock wait is root timing; do not call it session.load. |
| session.load | session.load.started/completed around actual locked session reload. Subsequent refreshed reads remain observed session operations where useful, without SQL spans. |
| attachments | attachments.started/completed around existing prepare/link boundaries only when supplied; failure never emits completed. No IDs/URI/MIME payloads exported. |
| orchestration / planning | Coarse orchestration encloses existing route/continuation/Runner work. planning.started/completed only brackets evaluate_operational_route server decision; no claim that this is a formal TurnPlan or its model call. Source requirements completion only from the existing observed declaration. |
| pending_interaction.resolve | Existing pop_read_continuation consumption/validation and pending DTO resolution; emit actual started/completed, not fabricated approval execution. |
| thread.resolve | Pure observer around actual resolve_active_thread function; absent when not invoked, no made-up conversation thread identity. |
| context.select | Actual ContextEngineeringBroker.assemble calls get context.selection.started/completed and count-only results. General turns with no broker call show phase not applicable, not a simulated selection. |
| finalization | After runner cleanup, through trusted response remediation/final-producer choice, command egress and source/provenance preparation. authority.selected means actual server final-producer decision, not a new TurnAuthority object. provenance.completed and command_egress.completed at existing successful boundaries; no text/decisions corpus exported. |
| persistence.final_answer | persistence.started/completed around actual durable final delta. Failed write records DATABASE_PERSISTENCE_ERROR; no message.completed on failure. Existing cleanup bookkeeping timings remain separate and best effort. |
| sse.complete | Server enqueue of message.completed/run.completed and end of generator; sse.started/completed describe server delivery boundary only. Sync consumption shares server completion; browser reception is not claimed. |
| root terminal | Exactly one turn.completed/failed/timeout/cancelled event with matching uppercase RunStatus after all server work/cleanup. run.stalled is never terminal. |

No arbitrary stages, label-derived lifecycle, fake fine planning stages or content
metadata. Register operation names and typed event attributes explicitly, update
schema documentation compatibly, and extend Collector validation for the same set.
Do not weaken counts-only arbitrary metadata to admit correlation: IDs/status/origin/
config hash are separately validated typed attributes. Bounds cap event/timeline
retention; tests must expose any truncation instead of claiming full reconstruction.

### Terminal-state design and delivery separation

| Scenario | Root result / ownership |
|---|---|
| Normal answer, valid clarification, source gap, approval request or safe policy rejection | COMPLETED when canonical business path succeeds and required persistence completes. Source gap/rejection may be a typed event; not automatically service failure per doc 21. |
| Specialist/provider failure represented as a safe response | Observe failure at existing owning catch/result before it is swallowed; safe wording alone does not prove valid completed application outcome. Existing error state / server-owned failure classification determines FAILED. Expected policy/source-gap outcomes remain COMPLETED. |
| Known timeout | TIMEOUT from explicit TimeoutError/asyncio.TimeoutError or trusted timeout result at owning boundary BEFORE inner catch converts it to run_failure; stable code. No new timeout policy/deadline/retry in M2. |
| Explicit task cancellation, including during lock wait/preparation/finalization | CANCELLED / TURN_CANCELLED, propagate original CancelledError; await existing business cleanup under existing semantics. Finalization cannot turn cancellation into success. |
| Unexpected session/attachment/Runner/finalization exception | FAILED at appropriate owning stage; raw exception never enters telemetry. Generic root failure uses FAILED/status and owning stage without inventing an undocumented generic error code. |
| Mandatory final-response persistence failure | FAILED / DATABASE_PERSISTENCE_ERROR, preserve existing safe error + run.completed(error), never message.completed. Best-effort bookkeeping failure remains a degraded event unless existing business contract makes it mandatory. |
| SSE consumer disconnect/close | Record delivery disconnected/unknown separately; background task continues to actual terminal state. Disconnect alone does not cancel or fail root. |
| Wire run.completed emitted, later cleanup raises | Root does not finalize from wire event alone. Outer completion determines true terminal result; record completion mismatch safely if necessary without emitting a duplicate wire terminal. |

Root lifecycle owns technical result; run_trace recorded_steps is only presentation,
public run.completed(ok/error) remains backward-compatible projection. _drive observes
explicit safe failure signals plus classified internal outcome, never parses response
text, labels, or diagnostic snapshots. finalize_once selects one terminal result;
late cancellation cannot overwrite a completed execution. Terminal event/status and
span end have exactly one attempted emission, even if exporter fails to deliver it.
OTel status: completed OK, failed/timeout ERROR with no description, cancellation
UNSET with explicit CANCELLED attribute/event (do not count caller cancellation as
provider failure). Export failure never changes business terminal classification.

Separate execution completion, server queue emission and HTTP relay completion.
No browser receive/render claim without later frontend acknowledgement. Root need
not stay open awaiting client reads. Relay completion/disconnect occurring after
root closure uses safe correlated logs (or later linked delivery instrumentation),
never a late write/reopen on an ended root. Existing SSE payload/event ordering,
sequence, safe run_id and normal response content remain unchanged. No unrestricted
trace IDs/details newly exposed to ordinary users; no new SSE heartbeat protocol.

### Active progress, convergence and failure isolation

Execution-local registry/cache, keyed by existing trusted run_id, exposes status,
current_stage/current_span_id, accepted/stage-start/last-progress UTC timestamps,
monotonic elapsed and terminal result. Only actual work advances progress; heartbeat
is not progress. Keep nested phase stack so a broker/thread child cannot leave the
parent falsely stuck on completed child stage. Bound event history and any terminal
capture; release active entries deterministically, no process-wide unbounded history.
M0 strict ActiveRun is reused once identity is complete; pre-identity/no-op state has
an explicit internal shape, not invalid IDs or a second durable registry.

Roadmap M2 requires active registry/current-stage visibility; this request explicitly
places production durable registry in M7. Accordingly M2 supplies an INTERNAL local
snapshot/test seam, not cross-instance support truth or an unauthenticated endpoint.
M0 package README/persistence notes currently assign minimum Cloud SQL status to M2;
record this discrepancy and revise those notes when implementing to match the user's
explicit scope. Production Cloud SQL architecture remains mandatory for M7. No
database/migration/API added in M2; local cache must be labelled execution-local.

Keep RunTraceRecorder sanitizer/dedup/recorded_steps and existing diagnostic behavior.
It has no independent lifecycle truth to remove; do not export its free-text labels.
Canonical lifecycle emits typed observations; retain existing public projections at
their current boundaries. Avoid redundant lifecycle injection into trace.step.
PerfTimer's acceptance anchor/clock remains reused; add a read-only timestamp seam
only if needed for shared root clock. Legacy phase marks remain useful; root timing
is canonical server duration. Do not map generation_complete to turn.completed,
and do not turn model/delegation callbacks into M3/M4 instrumentation.
Knowledge diagnostic_trace is preserved unchanged and correlated through run/trace
outside its snapshots; no sensitive historical record is automatically exported.

Every telemetry start/update/end/detach/cache call is bounded and fail-open for
business execution. Span construction failure falls back to no-op lifecycle;
missing context omits correlation instead of importing stale globals. Trace end
failure still detaches/removes local state. Counters/fallback use M1 Health failed/
dropped/filtered mechanisms with fixed trace operation labels and rate-limited safe
JSON, not recursive logs/raw errors. Do not catch business exceptions inside a
telemetry-only guard or call SDK flush in turn path. Exporters remain async/bounded.

### Exact expected implementation files

Only .agent/OBSERVABILITY_EXECUTION.md changes NOW. Expected later M2 files:

| File | Planned change |
|---|---|
| backend/observability/turn_trace.py (new) | Fail-open root lifecycle, typed observational ContextVar, late identity, phases and finalize_once. |
| backend/observability/active_runs.py (new) | Bounded execution-local progress cache/snapshot; no production repository implementation. |
| backend/observability/tracing.py | Registered operations, validated events/current context, root parent ownership; retain strict foreign-scope/raw exception denial. |
| backend/observability/redaction.py; attributes.py; schemas.py | Separate typed correlation/status/origin/config fields and safe event projection; compatible contract documentation, no new Stage set or generic metadata strings. |
| backend/observability/runtime.py; logging.py | Access/inject existing process runtime without reinitialization; safe default run correlation and health degradation. |
| backend/api/chat_service.py | Root acceptance/driver/done backstop, exact phase observations and swallowed-error classification; no second executor. |
| backend/agents/team_manager/operational_routing.py | Observation-only server planning entry/exit. |
| backend/agents/technical_authority_engineer/troubleshooting_threads.py | Observation-only fault-thread resolve entry/exit. |
| backend/context/assembly.py | Observation-only broker assemble entry/exit/counts. |
| backend/api/app.py (only if needed) | Thin runtime/delivery correlation plumbing; preserve endpoint/schema/ownership behavior. |
| backend/api/perf_timing.py (only if needed) | Read-only clock/start seam; preserve all callbacks and logs. |
| infra/observability/collector/config.yaml; config.local.yaml; README.md | Matching operation/event/typed correlation allowlists, no resource/deployment change. |
| backend/observability/README.md; docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md | Actual M2 identity/applicability/operation ownership; correct premature persistence notes. |
| backend/tests/test_observability_turn_trace.py; test_observability_active_runs.py; test_chat_service_root_trace.py (new) | Meaningful lifecycle/progress/canonical-path integration tests. |
| backend/tests/test_observability_logging.py; test_observability_collector.py; test_observability_contract.py; test_observability_policy.py; test_observability_exporters.py | M2 allowlist/privacy compatibility and actual loopback lifecycle export. |
| Existing lifecycle/streaming/cancel/rewind/persistence test files (only necessary assertions) | Verify unchanged behavior and exact root outcomes. |

run_trace.py, turn_context.py, diagnostic_trace.py, settings.py, requirements.txt,
Terraform and frontend need no planned functional change. Do not opportunistically
refactor them; if implementation proves otherwise, update this record first.

### Validation plan and commands

Use injected private M1 providers/exporters, fake Runner/gateway/model/secret fixtures,
isolated SQLite memory and existing network guard. Never boot real default app/model
or resolve shared DB/ADC. Reinspect /private/tmp/slopanoc-m1-test-guard launcher and
sitecustomize before reuse; its inherited SLOPANOC vars are cleared, Vertex/warmup
disabled, non-loopback sockets denied. Collector subprocess gets minimal credentials-
free env and only config.local.yaml. Production config gets validate only, never run.
If temporary guard/binaries are missing, recreate equivalent local tools or report
the missing validation prerequisite; no silently skipped required Collector gate.

Planned focused suite (after files exist):
```sh
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_turn_trace.py backend/tests/test_observability_active_runs.py backend/tests/test_chat_service_root_trace.py backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_runtime.py backend/tests/test_observability_logging.py backend/tests/test_observability_exporters.py backend/tests/test_observability_collector.py
```

Coverage: one root/start/end/terminal event per canonical sync/SSE accepted execution;
valid parentage of each applicable phase; preparation and finalization exceptions;
clarification/source-gap/policy/approval outcomes; specialist failure vs expected
source gap; caught timeouts; explicit cancellation during lock/session/attachment/
Runner/persistence and before task start; immediate close after run.started;
disconnect mid-turn; persist-before-message completion; cleanup after wire terminal;
repeat finalization; no open/cache/context leaks. Nested tasks and persistent ADK
drain see correct parent/run; concurrent different sessions and serialized same
session turns never share identity; copied contexts cannot reopen completed state.
Late current invocation/fallback IDs never join prior history; remediation invocation
does not replace original identity. Disabled/local/none/OTLP modes and lifespan
initialization/flush remain compatible.

Inject span start/update/end/export failures and verify unchanged response/persistence/
governance/SSE and safe Health degradation. Capture decoded SDK and actual Collector
OTLP payloads: root name, parentage, lifecycle events, trace/run/session/turn/status/
config metadata survive; sentinels in prompt/response/Teams/query/attachment/stack/
tracestate/baggage/unknown events and SDK bypass do not. Malformed IDs, free text and
high-cardinality metric labels remain rejected; positive correlation tests prevent
privacy filters from silently removing required IDs/events. Logs correlate in early
session AND post-Runner finalization; outside tasks show no stale IDs.

Planned applicable regressions:
```sh
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_api_app.py backend/tests/test_api_chat_service.py backend/tests/test_api_streaming_endpoint.py backend/tests/test_chat_service_streaming.py backend/tests/test_chat_service_hardening.py backend/tests/test_chat_service_performance.py backend/tests/test_chat_service_turn_context_lifecycle.py backend/tests/test_d2_merge_adk_activity_events_context_lifecycle.py backend/tests/test_chat_service_rewind.py backend/tests/test_api_run_cancellation.py backend/tests/test_final_answer_persistence_and_consistency.py backend/tests/test_read_continuation_regression.py backend/tests/test_ambiguous_selection_read_resume_regression.py backend/tests/test_command_egress_boundary.py backend/tests/test_single_invocation_command_authority.py backend/tests/test_approval_security_contract.py backend/tests/test_api_ownership.py backend/tests/test_context_assembly.py backend/tests/test_troubleshooting_fault_threads.py backend/tests/test_team_manager_case_context_provider.py backend/tests/test_perf_timing.py backend/tests/test_run_trace.py backend/tests/test_streaming_events.py backend/tests/test_p2_model_call_correlation.py backend/tests/test_model_warmup.py backend/tests/test_runtime_database_policy.py backend/tests/test_startup_import_graph.py
.venv/bin/python -m compileall -q backend/observability backend/api backend/context backend/agents/team_manager backend/agents/technical_authority_engineer/troubleshooting_threads.py
.venv/bin/python -m pip check
npm test -- src/api/streamChat.test.ts src/api/sseParser.test.ts src/api/runBackendChat.test.ts src/components/conversation/RunTrace.test.tsx src/components/conversation/CurrentActivity.test.tsx src/state/AppState.reducer.test.ts
npm run build
git status --short
git diff --stat
git diff --check
```
Review actual changed and untracked content as well as tracked diff. Collector native
0.160.0 validation of BOTH configs is included by test_observability_collector; only
the local config is run. No Terraform validation needed unless its source changes.
No full later model/dependency/ledger/watchdog/SLO/profiling acceptance claimed.

Performance: compare disabled vs enabled/no-exporter/local/loopback-OTLP canonical
fake turns, same fixtures/input/phase count and warmed imports; interleave modes,
report repetitions/median/p95 wall latency, process CPU and producer-only timing,
separately from async export/flush. General, attachments, pending selection/resume,
governed finalization, failure/cancel and concurrent scenarios. Include deterministic
fake waits and CPU work, never bill a provider. Aim <10 ms synchronous added cost;
evaluate <3% typical-turn CPU target from doc 01 with baseline uncertainty. If local
variance prevents a defensible percentage, report inconclusive and do not claim
production overhead. Full production/load qualification remains M16/deployment.

### Individually evaluated M2 exit criteria (all pending implementation)

| Gate / source | Required objective evidence | Current result |
|---|---|---|
| Roadmap: prompt reconstructable phase-by-phase | Actual general and governed fake canonical turn traces with ordered applicable request/session/attachments/planning/pending/thread/context/finalization/persistence/server-delivery phases; absent business objects/calls explicitly not applicable. | NOT RUN |
| Roadmap: active current stage | Internal execution-local snapshot while turn is blocked, accurate stage start/last progress/elapsed; no fake cross-instance guarantee. | NOT RUN |
| Master/doc 01/03: one root | Exactly one root start/end for each accepted sync/SSE turn, including preparation/pre-task cancellation and disconnect; valid children, zero duplicate executor. | NOT RUN |
| Master/doc 02/15: identity | Native trace/span IDs + unchanged trusted run/session, late genuine turn ID or explicit execution-only pre-ADK identity; no stale history fallback; all applicable IDs retained through Python/Collector export. | NOT RUN |
| Master/doc 03/13: terminal cleanup | One completed/failed/timeout/cancelled transition/event/end; STALLED never terminal; every phase closes, cache/task/context cleanup, no terminal reopening. | NOT RUN |
| Doc 13: async/concurrency | Nested task context and concurrent runs isolated, persistent drain preserved; same-session locking and rewind/cancel ownership unchanged. | NOT RUN |
| Doc 04/21: classification | Safe clarification/source-gap/policy/approval distinct from provider/infrastructure/specialist failure; caught timeouts preserved; mandatory persistence failure FAILED; delivery disconnect independent. | NOT RUN |
| Doc 03/13/18: failure isolation/logging | Span start/end/export failure leaves business behavior intact; bounded Health fallback; correlated early/final logs and no identities outside execution. | NOT RUN |
| Doc 09/13/18: privacy/cardinality | Adversarial payloads absent from SDK/Collector/log output; typed attributes/events only, no raw exception/details, no ID metric labels. | NOT RUN |
| Doc 22: applicable release/config | Existing trusted M1 resource metadata plus correctly scoped effective observability config version; unknown future contract versions not fabricated. | NOT RUN |
| Doc 13/AGENTS: imports/regressions | Focused M0/M1/M2 tests, required existing backend/frontend checks/imports/build pass; no blocking defects. | NOT RUN |
| Doc 01/13: overhead | Recorded canonical local enabled/disabled measurements with limits and uncertainty; no production synthetic extrapolation. | NOT RUN |
| Scope/safety | No M3+ spans/ledger/DB/API/UI/deadline work; scoped tracked/untracked review; no external mutation. | Planning PASS; implementation PENDING |

M2 implementation completion must evaluate each row independently; no M3 progression
from this plan or from documentation alone. Local enabled-mode trace/progress gates
are distinct from live Cloud Trace/Logging/Monitoring, distributed registry and
browser receive/render gates owned by later approved deployment/milestones.

### Settings / Diagnostics impact: five-part evaluation

1. M2 will supply internal run/native trace correlation, technical terminal status,
   current stage, elapsed/last progress and bounded phase timeline for future
   Settings → Observability & FinOps → Diagnostics.
2. Pure typed projection contracts need pre-ADK origin/no-op awareness. Production
   run lookup/timeline/session/active/health APIs in doc 05 are still missing; M7
   owns authenticated server projections and Cloud SQL durability.
3. No Settings/frontend UI work belongs here; existing layout/public SSE unchanged.
   Agent/tool/dependency detail, cost, watchdog and cross-instance history unavailable.
4. User sees only existing ownership-scoped safe progress/run ID. Future Operator/
   SRE/FinOps/Admin/Auditor authorization remains server-enforced M7; an internal
   snapshot does not grant access. FinOps acquires no content permission.
5. M1 effective configuration stays read-only and IaC/environment-owned; M2 exposes
   correlation only, adds no setting editor/API/IaC bypass.

### Cloud status, risks, planning validation and readiness

M2 remote/cloud changes required: **NONE**. Local source/config allowlist edits and
local fake/loopback tests suffice. No gcloud/Terraform/database/Graph/billing/GCS/IAM
commands executed. No deployment, migration, state change, API or billing enablement.
Inherited M1 deployment status: **NOT EXECUTED — USER APPROVAL REQUIRED**; exact
pending integration/IAM/image/config/CPU operations remain in M1 section above.
Cloud delivery remains **NOT LIVE-VALIDATED**. If implementation uncovers a remote
need, stop that operation and produce AGENTS.md exact-operation approval report.

Risks: first-yield/task-start cancellation gap; inner finally ends too early;
swallowed timeout/failure signals; late ADK history identity; existing prior-history
fallback unsuitable for telemetry; different public/technical status vocabulary;
detached late child tasks; event/ID filters silently dropping traces; inherited
Cloud SQL-in-M2 package notes contradict this explicitly scoped request; broker
and thread phases are conditional and no standalone TurnPlan exists. This plan
addresses each without changing business authority or implementing missing business
architecture. Any trace requiring an unavailable phase must state why it is absent.
No known blocker to beginning local M2 implementation under this recorded design.

Planning validation performed:
- Read entry records/contracts and actual canonical/M0/M1/Collector/Settings/tests.
- Re-read installed ADK 1.33.0 Runner invocation semantics and before_run context;
  do not misuse resume argument or patch private SDK internals for early turn ID.
- Guarded foundation recheck:
  `.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_runtime.py backend/tests/test_observability_logging.py`
  Result: **138 passed, 1 existing Starlette deprecation warning, 3.10 seconds**.
  No local socket receiver/Collector/cloud exercised in this recheck. Historical
  M1 147-case export/Collector and broader regression evidence remains above;
  not falsely claimed rerun here.
- git status/diff --stat/diff --check inspected; existing uncommitted M0/M1 source
  and unrelated user work preserved. This record was already untracked at baseline.

M2 implementation: **NOT STARTED**. M2 runtime validation: **NOT RUN**.
Ready To Implement M2: **YES** — requires subsequent implementation instruction.
Ready for Next Milestone: **NO**. M3: **BLOCKED BY M2**. Stop after planning.

## M2 implementation authorization — 2026-10-07

Approved M2 plan and local implementation request received. Entry gates rechecked.
Implementation follows the M2 objective/specification/audit/reuse/file/test/exit plan
above, with M3 blocked. Cloud operations prohibited; no deployment or remote test.
Current status IMPLEMENTATION IN PROGRESS; all M2 exit gates pending validation.

## M2 implementation and final local acceptance — 2026-10-07

**M2 IMPLEMENTED AND LOCALLY VALIDATED. Every blocking local gate PASS.**
This section supersedes the planning-only/pending status above. That earlier plan
remains historical engineering evidence. M3 has not been started. No deployment,
remote migration or external mutation was performed. M2 requires none.

### Implementation performed and real execution ownership

`ChatService.execute_turn_events` creates one fresh `slopanoc.turn` at accepted
iteration, after trusted EventSequencer allocation. Merely creating an unentered
async generator accepts no execution and creates no root. The existing `_drive`
task is created, retained and registered for existing cancellation BEFORE the first
`run.started` yield; there is no intervening suspension. Closing that relay cannot
strand an accepted lifecycle before a task exists. There is no second executor.

`_drive` attaches native root and observational ContextVar in its own task and keeps
the root through `_run_turn_events`, Runner cleanup, real finalization, mandatory
final-answer persistence, server event queueing, generator cleanup and session-lock
release. Its outer finally is the canonical terminal boundary. The narrower inner
Runner finally remains unchanged and is explicitly NOT root ownership. Existing
task/cancellation references and original background-exception logging remain.

`TurnTrace._finish` makes exactly one technical terminal decision, closes remaining
phase spans, records one canonical terminal event/native status, freezes terminal
timestamp/elapsed time, ends the root and removes the local cache entry. Repeated
finish/backstop never overwrites a prior terminal result. Independent SDK guards
attempt end/cleanup even if attribute/event/status update fails. The idempotent
task-done backstop also handles cancellation before `_drive` enters; it preserves
TIMEOUT/CANCELLED candidates and valid completed results, retrieves rather than
hides task exceptions, and guarantees the queue-done sentinel once. No watchdog,
deadline, retry policy, kill switch or new cancellation behavior was introduced.

Canonical child operations: request, session.load, attachments (conditional),
pending_interaction.resolve, planning, orchestration, context.select and thread.resolve
(only when those real functions run), finalization, persistence.final_answer and
sse.complete. Request validation is the observable accepted-generator boundary,
not an invented Pydantic/business validation. Planning observes the existing server
route decision. Pending resolution observes the actual continuation pop, including
the empty result. Thread resolution observes actual fault-thread resolution, not an
invented conversation registry/TurnPlan operation. Broker assembly is decorated at
its real entry/exit. No function is called simply to produce a phase. The canonical
agent.team_manager stage denotes coarse orchestration progress; no detailed agent
invocation span exists. Actual source-declaration/authority/provenance/command-egress
boundaries emit safe enums without influencing their selection or authority.

Existing RunTraceRecorder is still a sanitized public step projection. Its
response-generated/failed step describes the public response, not a second technical
terminal owner. Existing `run.completed.outcome` remains the wire response outcome.
A required specialist/remediation failure can deliver an existing safe response
while the technical root is FAILED. Cleanup failure AFTER wire success is recorded
as FAILED with safe SSE_COMPLETION_MISMATCH, without duplicate public terminal
emission or hiding the original exception. Backend technical status and client relay
delivery are explicitly separate contracts. Normal clarification, source gap and
valid policy/approval outcomes remain eligible for COMPLETED. Caught TimeoutError
is classified before legacy conversion to safe error wording; persistence failure
is FAILED; real explicit cancellation is CANCELLED; STALLED is not terminal.

`perf_timing.py`, public RunTrace format and knowledge `diagnostic_trace.py` remain
unchanged. PerfTimer continues its existing stage/delegation/model developer timers;
root phases map to session load, Runner orchestration, generation/finalization,
final persistence and server emission. Root elapsed also covers lock wait and final
cleanup. No timing subsystem migration. Knowledge diagnostics remain observability
only and never evidence, routing input, authority, provenance or command permission.

### Correlation, progress, isolation and fail-open behavior

Trusted EventSequencer run UUID remains distinct from native OTel trace/span IDs.
Session ID remains server-owned. A root starts in a fresh native Context, without
foreign parent/baggage/tracestate. M1 process runtime is accessed without acquiring
or initializing providers per turn. Git SHA/release/revision metadata remains M1's
trusted resource metadata; config_version is the effective observability hash only.

Genuine ADK invocation ID binds once on this run's first observed ADK event. No
prior-history fallback is used. Early logs/progress have no conversational turn ID;
early spans join via trace/run/session. If no invocation is observed, terminal
identity is `pre-adk:<run-id>`, origin `execution`; it is never a session history,
rewind or attachment key. Observed invocation origin is `adk`. Early ended child
spans are not rewritten. Typed optional thread/fault fields remain safe contract
fields; no distinct conversation-thread ID exists at these observed boundaries and
fault_id is not falsely relabeled as thread_id.

Safe logging defaults run ID from active ContextVar, trace/span from native context,
and genuine turn/origin once bound. Tests distinguish simultaneous native phase
span IDs and distinct invocation IDs, including late persistence logs. Nested tasks
inherit only their owner's context; tokens reset in attaching tasks. Detached copied
contexts after terminality cannot reopen progress or inherit ended log identity.
Explicit post-root relay logs are the narrow exception and correlate to that root.

ActiveRuns is a 2,048-entry bounded weak-reference execution-local cache. Immutable
Progress snapshots expose root/current span IDs, optional early turn/native identity,
current stage, UTC start/stage/progress times, monotonic elapsed, terminal timestamp,
128-entry timeline, truncation count and independent relay state. Entries are removed
on completion; no historical or distributed support lookup. Strict M0 ActiveRun is
projected only when native and turn identity exist. No API exposes this cache.

Span creation, lifecycle update/attachment/finish and exporter fault injection leaves
existing response/persistence behavior intact. M1 safe Health counters report trace
degradation; no recursive raw logging. Local disabled/none modes remain operational.
When native creation fails there can be no successfully exported native root; safe
health reports that degradation instead of inventing trace IDs or failing the turn.

### Actual files changed in M2

- `.agent/OBSERVABILITY_EXECUTION.md`
- `backend/api/chat_service.py`
- `backend/agents/team_manager/operational_routing.py`
- `backend/agents/technical_authority_engineer/troubleshooting_threads.py`
- `backend/context/assembly.py`
- `backend/observability/turn_trace.py` (new)
- `backend/observability/active_runs.py` (new)
- `backend/observability/tracing.py`
- `backend/observability/redaction.py`
- `backend/observability/attributes.py`
- `backend/observability/schemas.py`
- `backend/observability/runtime.py`
- `backend/observability/logging.py`
- `backend/observability/README.md`
- `infra/observability/collector/config.yaml`
- `infra/observability/collector/config.local.yaml`
- `infra/observability/collector/README.md`
- `docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md`
- `backend/tests/test_chat_service_root_trace.py` (new)
- `backend/tests/test_observability_turn_trace.py` (new)
- `backend/tests/test_observability_active_runs.py` (new)
- `backend/tests/test_observability_collector.py`

Preexisting M1 edits in app.py/settings.py/requirements.txt were preserved byte-for-
byte against the saved entry SHA256 snapshot. Terraform/provider/exporter/config/
metric foundation files not listed above are unchanged against that snapshot.
Existing test files other than Collector were not rewritten. No frontend changes,
migrations, credentials, production exports, persisted payloads or dependency changes.
Collector YAML was serialized locally (formatting changed); existing topology,
exporters/resources/environment ownership and sampling remain unchanged. Only M2
operation/event/typed correlation admission was extended. No deployed config changed.

### Final validation performed and results

All Python tests used the re-inspected local guarded launcher:
`.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py`.
It clears inherited SLOPANOC configuration, uses SQLite in memory, disables Vertex
and model warmup, and rejects non-loopback sockets. Fixtures use fake Runner/tools/
gateways. Loopback receiver tests needed sandbox escalation, approved by automatic
review; this did not authorize or perform any remote/cloud operation.

Final focused command is the exact ten-file M0/M1/M2 command in the approved plan
(contract, policy, settings, runtime, logging, exporters, Collector, turn_trace,
active_runs, chat_service_root_trace): **190 passed, 1 existing Starlette deprecation
warning, 10.20 s**. No skips. Native Collector **0.160.0** validated BOTH production
and local configs. Only local config was started with credentials-free environment
and loopback sinks. Decoded real ChatService OTLP proves one slopanoc.turn plus safe
children/events, identical trace ID, trusted run/session/late turn/origin/status/config
survival, no injected prompt/token bytes, and continued logs/metrics receipt.

Final planned 27-file backend regression command above: **407 passed, 4 existing
Starlette/ADK deprecation warnings, 21.14 s**. Covers app lifecycle, API ownership,
SSE order, async drain, context lifecycle, cancellation, rewind, final persistence,
pending continuation, command egress, single invocation authority, approval,
broker/thread policy, perf/public trace, startup/database and model warmup behavior.

Additional command using that same launcher:
`backend/tests/test_clarification_continuity.py backend/tests/test_tae_negative_selection_completion.py`:
**76 passed, 33 existing ADK plugin deprecation warnings, 2.90 s**. Real domain
fixtures validate clarification continuity and trusted negative source selection.
Final distinct backend validation total: **673 passed** (190 + 407 + 76).
Intermediate focused 56-case check passed too; not added to the distinct total.

Frontend shared SSE/public trace contract checks, executed during M2:
`npm test -- src/api/streamChat.test.ts src/api/sseParser.test.ts src/api/runBackendChat.test.ts src/components/conversation/RunTrace.test.tsx src/components/conversation/CurrentActivity.test.tsx src/state/AppState.reducer.test.ts`:
**211 passed in 6 files, 1.84 s**. `npm run build`: **PASS**, 2,028 modules, Vite
build 1.64 s. No frontend source/contract changed afterward.

Final compileall command in plan: **PASS**. `.venv/bin/python -m pip check`:
**No broken requirements found** (only nonwritable local cache warning).
`git status --short`, `git diff --stat`, full `git diff`, `git diff --check`:
**reviewed/PASS**, including explicit untracked source/config/test/docs inspection
and saved baseline hash comparison. Tracked diff includes preserved earlier M1
app/settings/requirements changes; it does not imply those were changed in M2.

### Failures encountered and repairs

- Initial tests inspected task bookkeeping before asyncio done callbacks ran:
  assertions now wait one event-loop tick rather than changing cancellation ownership.
- Lifecycle event fault injection exposed unguarded constructor request-phase updates:
  constructor/phase notification guards now preserve native detach and business flow.
- Safe source-gap fixture targeted the wrong helper: corrected to actual server
  governed completion boundary; additional real domain regression suite also passed.
- Config fixture supplied duplicate keyword overrides: corrected fixture model copy.
- Required-specialist fixture lacked actual route.trace_view: corrected to the real
  OperationalRouteDecision dataclass, preserving the business contract under test.
- Standard sandbox denied local socket binding: reran the isolated loopback tests
  under reviewed escalation; no remote connectivity or cloud permission was requested.
- Final audit tightened missing required specialist/remediation classifications and
  prevented copied late-worker log correlation. The final suites above include them.
- Expanded benchmark initially retained a previous scenario's patched fake helper:
  corrected per-scenario teardown and reran; only corrected final results below count.

All blocking failures repaired and final validations passed. No silent exit waiver.

### Privacy validation

SDK and Collector allowlists admit explicit M2 operation/event enums, validated
run/session/turn/thread/fault identifiers, RunStatus, origin enum, configuration hash,
existing ErrorCode and count-only metadata. Unknown/raw exception events, content,
links, descriptions, foreign scopes/tracestate are excluded. Known safe event names
cannot carry unrestricted text. Production JSON formatter never evaluates raw
message/args/exception/authorization extras. Prompt/response/Teams/attachment/token
sentinels are absent from captured spans/events/JSON; actual Collector capture also
denies injected raw prompt/authorization. M0/M1 adversarial and metric cardinality
tests continue passing. No run/turn/session/user/chat/case/fault metric label added.
No telemetry becomes evidence or authority. Optional turn/origin log fields and
operation extensions are compatible schema version 1; no durable schema changed.

### Local overhead measurement

Final command: `.venv/bin/python /private/tmp/m2-overhead.py` (isolated environment,
same non-loopback guard). Actual ChatService/FakeRunner/SQLite paths; no provider
call or billing. Modes: disabled baseline, enabled/no exporter (`none`), enabled
local async exporter. For each scenario/mode, 90 repeats with first 10 warmups
discarded, 80 measured; alternating mode order. Session creation outside timing.
Process CPU includes exporter thread work; latency excludes explicit flush/shutdown.
Four concurrent turns are normalized per turn. Fake provider wait is 20 ms. Optional
attachment preparation, clarification and safe governed source-gap use local fakes
at real server boundaries. Cancellation is the real existing pre-start cancel path.

Measured numbers generated below from the corrected final run. These are local
synthetic measurements, not production performance or cloud transport claims.

| Scenario | Disabled median / p95 wall ms | Enabled none median / p95 wall ms | Enabled local median / p95 wall ms | Local minus disabled median ms | CPU disabled / none / local ms |
|---|---|---|---|---|---|
| general | 1.5172 / 1.6663 | 1.8903 / 2.1377 | 2.5164 / 2.9423 | 0.9992 | 1.2535 / 1.6165 / 2.2520 |
| sse | 0.9879 / 1.0889 | 1.3317 / 1.6782 | 1.9194 / 2.1507 | 0.9315 | 0.8200 / 1.1585 / 1.7460 |
| provider_wait | 22.9836 / 24.0181 | 23.5365 / 24.8848 | 24.1435 / 25.8920 | 1.1599 | 1.7100 / 2.2820 / 2.8470 |
| failure | 0.5418 / 0.6845 | 0.8311 / 1.3042 | 1.3019 / 1.5502 | 0.7601 | 0.4380 / 0.7275 / 1.1965 |
| concurrent | 1.1121 / 1.2440 | 1.4582 / 1.6146 | 2.0993 / 2.5181 | 0.9872 | 1.0495 / 1.4016 / 2.0473 |
| attachments | 1.0143 / 1.1298 | 1.3954 / 1.5776 | 2.0502 / 2.4149 | 1.0359 | 0.8485 / 1.2275 / 1.8815 |
| clarification | 1.4974 / 1.5955 | 1.8853 / 2.0991 | 2.4916 / 2.9183 | 0.9942 | 1.2410 / 1.6210 / 2.2340 |
| source_gap | 1.5488 / 1.8171 | 1.9140 / 2.2765 | 2.5802 / 3.0018 | 1.0314 | 1.2835 / 1.6550 / 2.3185 |
| cancellation | 0.1723 / 0.2134 | 0.2602 / 0.3104 | 0.3907 / 0.4892 | 0.2184 | 0.1110 / 0.1995 / 0.3290 |

Added median latency **0.2184–1.1599 ms**; added p95 latency **0.2758–1.8739 ms**
on these paths: local <10 ms synchronous overhead target PASS. General baseline
1.5172 ms vs local 2.5164 ms (+0.9992 ms); 20 ms fake-provider baseline 22.9836 ms
vs local 24.1435 ms (+1.1599 ms). Tiny workloads amplify relative CPU percentages
(local roughly +66% to +196% over disabled); do NOT assert <3% production CPU.
Typical production-turn CPU percentage is **INCONCLUSIVE / NOT VALIDATED** and full
load/production measurement remains M16/deployment. No optimization was justified
by the measured absolute latency. The initial five-scenario run was noisier; final
interleaved nine-scenario results supersede it. Pending pop occurs in normal paths;
full resumed-specialist latency and loopback-OTLP throughput were tested functionally
but not benchmarked separately. Collector compatibility has direct integration
evidence; this benchmark does not claim network exporter overhead.

### Every blocking M2 exit criterion individually evaluated

| Criterion | Result | Evidence |
|---|---|---|
| One root trace per accepted turn | PASS | Actual sync/SSE tests count one slopanoc.turn/start/end/terminal; unentered generator creates none. |
| Correct real execution lifetime | PASS | Root survives Runner finally, finalization, persistence, generator cleanup and lock release; first-yield abandonment and late cleanup tested. |
| Trusted run ID preserved | PASS | EventSequencer UUID matches root/log/decoded Collector records; native IDs remain separate. |
| Genuine turn identity safely bound | PASS | Early identity unset; genuine invocation binds once; no old-history association; explicit pre-ADK execution origin tested. |
| One terminal result | PASS | Idempotent finalization/backstop; exact one canonical terminal event; no terminal reopening. |
| Completion backstop works | PASS | Pre-start cancellation unblocks relay; injected finish failure recovered; valid completion/specific timeout preserved. |
| Concurrency isolation | PASS | Distinct concurrent run/trace/phase-span/ADK-turn IDs and late persistence logs; nested async inheritance; same-session serialization. |
| Cancellation/timeout/error semantics | PASS | All four terminal states; session/finalization/persistence/cleanup/specialist failures; cancellation at lock/session/attachment/Runner/persistence. |
| SSE disconnect semantics | PASS | Disconnect recorded separately; backend completed after disconnect; explicit existing cancellation remains CANCELLED. |
| Current-stage/progress contract | PASS | Immutable live snapshots, UTC stage/progress timestamps, monotonic elapsed, native current span, bounded timeline/capacity and terminal removal. |
| Logs correlate correctly | PASS | Early and late run/native identity, late genuine invocation and config/release metadata; reset/ended copied-context tests. |
| Privacy filters | PASS | Sentinel denial SDK/events/JSON/real Collector; typed allowlists, raw exception denial, no identifier metric labels. |
| Telemetry failure does not alter business behavior | PASS | Creation/update/attachment/finish/end/export outage fault injection; unchanged response and safe M1 health failures. |
| Applicable regressions | PASS | 673 distinct backend + 211 frontend tests, compileall, pip check and frontend build; no blocking failures. |
| Local Collector receives safe root telemetry | PASS | Actual native 0.160.0 loopback pipeline, decoded trusted fields/phase events plus logs/metrics; both configs validated. |
| M3/M4/M5 functionality unimplemented | PASS | Scoped source/hash/full-diff audit: no model/token/cost, detailed agent/tool or dependency instrumentation. No future API/DB/UI/ledger. |
| No cloud mutation | PASS | Only local source/tests/config validation/loopback processes; no deploy/migration/cloud/IAM/billing/Graph/state operation. |

Approved plan's additional specification gates:

| Gate | Result | Evidence |
|---|---|---|
| Roadmap phase reconstruction | PASS | Actual applicable request/session/pending/planning/orchestration/finalization/persistence/server emission; conditional attachments and actual broker/fault-thread child parentage; no invented TurnPlan. |
| Release/config correlation | PASS | Existing resource Git SHA/release/Cloud Run revision and per-root effective observability hash verified. |
| Imports/build/architecture preservation | PASS | Compiler, app/startup, routing/governance/authority/approval/provenance/session/rewind/SSE regressions; observation-only decorators return/raise unchanged. |
| Local overhead measurement | PASS | Nine corrected representative paths, three modes, 80 measured repetitions/mode; <10 ms local added latency, CPU uncertainty explicit. |

### Known limitations and deferred architecture

- Enabled healthy telemetry proves one exported native root. Disabled/no-op or
  native creation/export failure intentionally cannot guarantee delivered telemetry;
  business continuity and M1 health reporting take precedence. No invented IDs.
- M2 cache is bounded and execution-local, without retention/history or cross-instance
  truth. Cloud SQL registry, authorized APIs and support history belong M7 under the
  explicitly approved M2 scope; later implementation must retain production durability.
- Genuine ADK turn ID is late; early closed spans use run/trace joins. Paths without
  an observed ADK event are explicitly execution-origin, never old conversational IDs.
- No standalone TurnPlan or separate conversation-thread registry boundary exists in
  current code. Only real routing/broker/fault-thread functions are observed. No model,
  specialist/tool/dependency detail, ledger/cost, watchdog/STALLED detection, SLO,
  browser tracing, dashboards or profiling implemented. STALLED remains nonterminal.
- The production image/cloud OTLP path is not deployed/live-validated. Native local
  Collector validation is not Cloud Trace/Logging/Monitoring proof. Production CPU
  <3% target remains unvalidated; no synthetic extrapolation.
- Public response step/wire outcome describes response delivery. Technical failure
  after wire success is retained in root and safe mismatch event, without rewriting
  response content/event order. Browser receive/render instrumentation is later.

### UI / Settings impact: explicit five-part evaluation

1. **Required information created:** internal trusted run/native root/current span,
   status/stage, elapsed/last progress and bounded timeline for future Diagnostics.
2. **API/data contracts:** immutable Progress and late-origin-aware optional log fields;
   strict M0 ActiveRun projection when identity exists. No public/admin API or durable
   repository implemented; authenticated lookup/history remains M7.
3. **UI scope:** no Settings UI belongs to M2. Existing integrated UI/public progress,
   typography/navigation/SSE contracts unchanged. No separate app introduced.
4. **RBAC:** existing session/run ownership stays enforced; internal cache grants no
   access. Future observability role enforcement is server-side M7/M8. FinOps receives
   no conversation/Teams/knowledge/sensitive-case access through this work.
5. **Configuration ownership:** effective M1 policy remains read-only IaC/environment
   configuration. No runtime editor, setting mutation or Terraform bypass added.

### Cloud / Infrastructure status and readiness

M2 cloud deployment status: **NOT EXECUTED — no M2 remote operation required**.
No cloud/external mutation occurred. Authenticated cloud sessions were not used.
M2 exact pending cloud operations: **NONE**. Local Collector source allowlists have
not been deployed. Inherited M1 production deployment status remains
**NOT EXECUTED — USER APPROVAL REQUIRED**; exact pending image/integration/IAM/config/
CPU operations are unchanged in the M1 record. Cloud runtime validation remains
**NOT LIVE-VALIDATED**, with no false live acceptance claim.

All blocking M2 local acceptance gates PASS. **Ready for Next Milestone: YES.**
M3 is **NOT STARTED** and requires a new user request; this M2-only task stops here.


## M3 — Model Instrumentation: current-state audit and implementation plan

Planning date: **2026-10-07**. Authorization: **M3 PLANNING ONLY**.
Status: **M3 — PLANNED — NOT IMPLEMENTED**. M4 remains **BLOCKED BY M3**.
Only this execution record changes in this pass. Historical milestone plans and
completion evidence above remain intact. No M3 runtime implementation or tests added.

### Entry gate and authoritative sources

**PASS for local M3 planning:** M0 VALIDATED COMPLETE; M1 local implementation
COMPLETE, validation PASS, required local exits ALL PASS, Ready YES; M2 implementation
and all blocking local validation gates PASS, Ready YES. Actual code/tests exist.
M1 production deployment remains unexecuted; the prior explicitly accepted local
completion/deployment separation is preserved, not reclassified as live completion.
M2 reports 673 backend and 211 frontend cases plus build/Collector checks. Fresh
planning verification below adds limited current-source evidence, not a replacement
for those full validation records.

Read AGENTS.md and .agent/PLANS.md, then Telemetry README, 00 master contract,
12 roadmap, 14 execution guide and 15 definition of done; specialist sources inspected:
01 target architecture, 02 data contract, 03 instrumentation/runtime, 04 reliability,
07 FinOps architecture, 08 data model/unit economics, 11 integration map,
13 acceptance, 16 production backend, 18 structured logging, 20 accounting durability,
22 release correlation; also 09 security/privacy/retention/sampling.

**Explicit scope conflict, not a passed gate:** docs 03/12, M0 persistence notes and
backend/observability/README.md assign durable usage rows/outbox to M3. This user
request explicitly defers the accounting ledger to M10 and requires M3 metadata
telemetry plus a handoff contract only. Follow that instruction. Original roadmap
M3 durable-row/100% persisted-ledger criterion remains **DEFERRED BY EXPLICIT USER
SCOPE — NOT SATISFIED**, never relabelled as telemetry coverage. Later implementation
must document this same override in doc 02/package ownership notes. The full M0–M19
accounting requirement remains binding at M10. No ledger, DB, migration, pricing or
cost engine is proposed here. Future M4 readiness must explicitly distinguish the
user-scoped local M3 exits from that original deferred criterion; no silent waiver.

### Objective, inspected architecture and reuse

Observe every actual model/provider request with one metadata contract: provider,
model/version, agent/operation, mode, latency/TTFT availability, attempts/retries,
finish/outcome/error/timeout/cancellation and authoritative usage, attached to the
correct active M2 trace when applicable. Includes generation, image inference and
real embedding model calls. No agent/tool/Graph spans, timeout redesign, API/UI,
watchdog, financial database, prices, billing integration or M4 work.

Reuse M0 Stage/RunStatus/ErrorCode/Attribute/schema versions, central redaction and
bounded metric registry; M1 explicit Runtime/tracer/meter/log providers, resource
release/environment attributes, safe processors/export queues/Health; M2 TurnTrace,
current_turn, trusted run/session correlation, late genuine root invocation binding,
terminal-context guards and native safe phase/root parentage. ChatService remains
sole root owner. Do not use legacy perf registries as a model-call identity store.

Actual model configuration: five singleton agents use get_shared_llm(settings.gemini_model),
lru-cached registry resolution to one BaseLlm per name, with lazy Gemini Client.
Installed ADK 1.33.0, google-genai 1.75.0, OTel API/SDK 1.41.1 and semconv 0.62b1;
M1 schema URL 1.40.0. requirements.txt pins ADK and OTel; GenAI version is currently
transitive, so adapter compatibility must be explicitly pinned/checked without a
silent upgrade. No provider/credential/client construction was executed for audit.

Existing perf_timing before/after callbacks record prompt/candidate counts and
terminal-aware timing, but pending identity is keyed by run, not concurrent call.
They do not cover direct warmup, ingestion image interpreter, embeddings or hidden
SDK attempts. Keep compatibility logs/tests; canonical M3 data must not depend on
this registry or count callbacks as requests. No production Gemini price calculator
was found in the inspected backend paths; existing frontend quota/demo data is not
financial truth. Agent prompts/configuration/routing/authority remain unchanged.

### Explicit model-call inventory

Legend: **T** = user-turn runtime-relevant and potentially billable when provider
is reached; inherits active M2 turn through existing awaits/tasks, using outer root
run/session identity rather than disposable nested session IDs. **S** = independent
system workload; no user-turn correlation. **G** = Gemini GenerateContent raw response
usage plus ADK LlmResponse usage/model_version/finish_reason available if provider
returns it; missing usage is unknown. **E** = embedding statistics.token_count and
metadata.billable_character_count where supplied; no guaranteed generation usage.
All retry descriptions below are business behavior; SDK transport retries are
separate and shared by all real provider requests.

| Owner / actual path | Agent | Operation / purpose | Mode | Existing business retries | Usage | Parent / billing classification |
|---|---|---|---|---|---|---|
| backend/agents/team_manager/agent.py; chat_service._build_runner/main run | team_manager | orchestration; routing/source classification; final synthesis | Outer run explicitly SSE, including synchronous API's same generator | ADK tool/model cycles; bounded specialized remediation below | G | T; current orchestration/turn |
| team_manager/agent.py presentation_team_manager; chat_service._build_presentation_runner | team_manager | synthesis/presentation of trusted continuation result | Outer SSE | _retry_trusted_presentation_once below | G | T; same outer turn |
| chat_service._retry_trusted_presentation_once | team_manager | synthesis; empty presentation repair | Nested Runner default nonstream | At most one presentation retry; never repeat read execution | G | T; disposable retry session must not replace root |
| backend/agents/incident_manager/agent.py via team_manager.MultimodalAgentTool | incident_manager | specialist_reasoning; Teams analysis including vision | Nested Runner default nonstream | ADK tool cycles; compliance below | G | T; nested specialist still same trace |
| team_manager/direct_read_fast_path.py _fast_path_incident_manager | incident_manager | specialist_reasoning if provider reached | Nested nonstream | Inherits existing callback behavior | G only on real request | T; callback-returned synthetic LlmResponse emits ZERO model calls |
| direct_read_fast_path._run_trusted_presentation; _fast_path_team_manager | team_manager | synthesis | Nested nonstream | At most one extra trusted presentation attempt | G | T; original active root |
| team_manager/read_continuation_execution.py _CONTINUATION_INCIDENT_MANAGER | incident_manager | specialist_reasoning for continuation agent path | Nested default nonstream | Existing bounded flow; preserve | G | T |
| read_continuation_execution._SYNTHESIS_ONLY_INCIDENT_MANAGER / synthesis runner | incident_manager | synthesis after deterministic read | Nonstream | No new retry; deterministic read failure can skip model entirely | G | T; read itself is not a model call |
| team_manager/source_requirements_completion._declaration_only_agent | team_manager | classification; source-requirement declaration remediation | Nonstream | Existing one remediation invocation; internal tool/model cycles | G | T; planning-related, no invented standalone planner |
| team_manager/governed_knowledge_completion.py remediation Runner | incident_manager | specialist_reasoning; governed completion repair | Nonstream | Existing IM bounded compliance path | G | T |
| incident_manager/provenance_compliance._compliance_retry_incident_manager | incident_manager | specialist_reasoning; provenance compliance retry | Nonstream | Exactly one retry runner; after_agent_callback removed to prevent recursion | G | T; separate billable requests when tools cause another round |
| backend/agents/technical_authority_engineer/agent.py; agent_tool specialist Runner | technical_authority_engineer | specialist_reasoning; governed troubleshooting | Nested default nonstream | Existing structured regeneration/action reselection below | G | T |
| technical_authority_engineer/agent_tool.py _run_tool_free_message / _regenerate_structured_output | technical_authority_engineer | structured_output_repair | Nonstream | One structured-output regeneration; tools removed, same specialist session | G | T; transport success and parse failure are distinct |
| technical_authority_engineer/agent_tool.py _reselect_procedure_action through tool-free finalizer | technical_authority_engineer | structured_output_repair; procedure selection restatement | Nonstream | Existing bounded reselection, no new retry | G | T; do not authorize/change selection using telemetry |
| backend/agents/problem_manager/agent.py; problem_manager/agent_tool.py | problem_manager | specialist_reasoning; problem/RCA analysis | Nested ADK AgentTool default nonstream | Existing ADK tool/model rounds | G | T when feature enabled |
| backend/agents/automated_operations_engineer/agent.py; automated_operations_engineer/agent_tool.py | automated_operations_engineer | specialist_reasoning; automation artifacts | Nested ADK AgentTool default nonstream | Existing ADK rounds | G | T when feature enabled |
| backend/config/model_warmup._run_warmup_request | system (registered extension) | warmup; direct shared BaseLlm call | Explicit stream=False | Once per process; asyncio.wait_for existing warmup cap, default 30s | G | S; fresh independent model operation, platform/shared allocation later |
| backend/knowledge_ingestion/gemini_image_interpreter._interpretation_agent/interpret | km_image_interpreter (registered extension) | image_interpretation | Nonstream, no tools | No application retry loop | G | S by default; independent ingestion, not a chat root; explicit caller scope only if truly turn-triggered |
| backend/tools/knowledge/dense_similarity.VertexEmbeddingSimilarityProvider._embed | knowledge_retrieval (registered extension), initiating agent separately if known | embedding_query / embedding_document | Nonstream; document batches concurrent under semaphore | SDK only; cache hits make no provider request | E | T when in retrieval tool; S if standalone; preserve inherited turn/initiator |
| backend/knowledge/embeddings/service.VectorEmbeddingService.embed_text / embed_many | knowledge_embedding (registered extension) | embedding_document or embedding_query only if supplied by caller; otherwise embedding | Nonstream; embed_many currently serial calls | SDK only; failure catches and local deterministic fallback | E | T/S according to active trusted caller; local fallback/empty/sync pseudo-vector emits ZERO provider requests |

Repository search covered non-test Python for Agent/LlmAgent/Gemini constructors,
get_shared_llm, Runner/run_async, generate_content*, embed_content, predict,
count_tokens, google.genai/vertexai imports and model_copy/clone variants. The two
embedding sites create direct clients outside shared BaseLlm. No independent planner,
model-selection inference, configured alternate fallback model, live/bidi/interactions,
context-cache creation or LLM compaction model was found in application construction.
M2 planning phase is deterministic route/context work, not proof of a planning model.
The Team Manager mixes routing and synthesis in a real call; use orchestration when
server-owned phase cannot distinguish purpose. Do not inspect output text to guess.
Unconfigured SDK capabilities are coverage guard cases, not invented production calls.

### Central instrumentation design and implementation sequence (future only)

1. Add immutable ModelOperation/ModelAttempt/ModelUsageObservation contracts and
   bounded attribution enums in observability. Call UUID exists before invocation;
   per-call attempt counter is local, never a shared per-run pending slot.
2. Wrap the existing registry-resolved BaseLlm through one delegating adapter in
   get_shared_llm, preserving cache/client identity and lazy initialization. Forward
   requests/stream flags and yield original objects unchanged; close underlying
   generators under the existing Aclosing semantics. No prompt/config edits.
3. Use supported ADK model callbacks as attribution-only hooks: observer returns
   None and never short-circuits. Compose as the final before-model observer so
   earlier synthetic-return callbacks produce no provider telemetry. Obtain agent
   and actual invocation ID from callback context, not model text. A narrow shared
   callback composer avoids bespoke telemetry in agents. model_copy variants that
   replace callback lists must retain the composer. Direct warmup/embedding paths
   use the same explicit context adapter, not ADK callbacks.
4. Capture raw Google GenAI generate_content / generate_content_stream responses
   before ADK aggregation for usage, finish/version and first provider event. Use
   one instance-owned delegating client/model facade; never consume extra chunks,
   prefetch, parse/log contents or replace returned business responses. SDK may split
   embeddings into multiple HTTP operations: count each actual provider attempt.
5. **Attempt seam requires a pinned compatibility adapter:** ADK public callbacks
   do not expose SDK attempts. Installed GenAI _async_request_once is below Tenacity
   and branches to both httpx/aiohttp. Plan a narrowly scoped, per-client delegating
   adapter at this seam, wrapping the returned response stream until exhaustion.
   This is a PRIVATE SDK seam, not a claimed supported ADK hook. Keep SDK code and
   process-global methods untouched; assert exact version/signature and test both
   branches with fakes. Forward arguments/return/exception unchanged, preserve
   credential refresh, backend choice, request timeouts and Tenacity policy. Do not
   force httpx or retry_options changes to obtain telemetry. Public custom transport
   injection is possible but does not by itself cover the existing aiohttp branch
   or decoded usage; no transport replacement is authorized by this design.
6. Integrate the two direct embedding clients with this same central client adapter;
   observe each real SDK request and authoritative embedding metadata, never vectors.
   Minimal callsite context differentiates query/document/background work.
7. Extend SDK safe span/event/metric/log projections and BOTH Collector source
   allowlists in lockstep; validate local Collector with positive metadata survival
   and negative content tests. No Collector deployment.
8. Run listed coverage/privacy/parentage/behavior/retry/overhead gates and regressions;
   update individual outcomes. No later milestone progression in this task.

Adapter compatibility failure must report safe M1 health degradation and delegate
business execution unchanged. Such a run is a coverage failure, not a passing M3
acceptance result. Implementation cannot claim completeness if actual attempts remain
opaque. Telemetry exceptions never turn a successful model request into failure.

### Model spans, semantics, parentage and attribution

Use CLIENT **gen_ai.request** once per actual provider attempt (explicit safe name
accepted by user), with optional INTERNAL **slopanoc.model.operation** parent for
logical operation/retry backoff and processing lifetime. These are model-only spans;
no M4 agent/tool spans. Both registered in tracing.Operation. Standard semantic
fields come from installed 0.62b1 constants; retain schema URL 1.40.0:

- gen_ai.provider.name: gcp.vertex_ai / gcp.gemini per actual client backend;
- gen_ai.request.model; gen_ai.response.model when provider supplies model_version;
- gen_ai.operation.name: generate_content or embeddings (standard operation);
- gen_ai.response.finish_reasons: bounded normalized SDK enum array;
- gen_ai.usage.input_tokens / output_tokens / cache_read.input_tokens;
- error.type: bounded canonical error classification when applicable.

SLOPANOC-only fields: slopanoc.agent, slopanoc.model_operation (business purpose),
slopanoc.request_mode (streaming/non_streaming), slopanoc.streaming (bool),
slopanoc.attempt (1-based), slopanoc.retry_count (attempt-1), slopanoc.workload
(user_turn/warmup/ingestion/background), slopanoc.status/outcome/error_code,
slopanoc.duration_ms, slopanoc.ttft_ms and ttft_boundary/availability;
slopanoc usage total/thought/tool-input component counts where standard equivalent
is absent. slopanoc.model_version can remain the existing release-correlation alias
of a trusted response model version; do not fabricate missing versions.
Lifecycle uses existing model.request.started/first_token/completed/failed/timeout
Stage values. Cancelled ends once with CANCELLED and TURN_CANCELLED; no invented
MODEL_CANCELLED vocabulary. Add only the minimum compatible typed schema extensions.

Agent names follow doc 03 canonical values, including technical_authority_engineer
and automated_operations_engineer (not shortened names from illustrative headings).
M0 schemas currently type agent as bounded Code, with no AgentName enum; add the
minimum shared bounded registry/enum rather than claiming one already exists.
Register explicit system/km_image_interpreter/knowledge_retrieval/knowledge_embedding
extensions rather than misattribute to a chat specialist. Planning/synthesis are
operations, not invented agent owners. Business operations: orchestration, planning,
classification, specialist_reasoning, synthesis, structured_output_repair, warmup,
image_interpretation, embeddings with query/document subdivisions. No primary/supporting
role invented in M3 where server TurnPlan does not provide it; agent ownership and
business operation are required, optional actual authority role remains separate.

Parent model operation to a validated currently exported SLOPANOC phase/model context
or active M2 root. ADK call_llm spans are foreign and FILTERED by M1; do not parent
to their invisible IDs. Add a minimal TurnTrace safe-parent accessor rather than
reusing its shared _phases stack for concurrently active model requests. Each model
adapter attaches/detaches tokens in its owning task around awaited provider work,
and restores previous context before yielding control to consumer. Async generator
closure/cancellation must not strand context or spans. No shared-client mutable agent
field. Root invocation is late-bound: callback-owned outer agent invocation may bind
only through verified canonical outer context; nested disposable invocation IDs
remain separate trace-only metadata, never replace root turn/session. Early spans
join by run/trace; M2 execution-origin fallback remains unchanged.

Warmup/background scopes explicitly clear inherited turn/baggage and create independent
logical model spans, not a second slopanoc.turn. Post-terminal copied contexts cannot
attach to ended roots or reopen progress. Detached live child tasks keep their own
captured root association only while genuinely active; unrelated jobs detach explicitly.
Tests assert graph closure, not merely matching run attributes.

### TTFT and latency

Use monotonic clock. Logical latency includes SDK retry/backoff and aggregation;
provider-attempt latency covers actual attempt through body/stream termination.
Model TTFT is request start to first RAW provider output event, measured before ADK
aggregation and callback/SSE relay. Empty/usage-only/control packets do not establish
first token. If only an output event boundary can be identified, label boundary
provider_output_event; literal token arrival is not claimed. Only inspect safe
structural presence flags; never serialize text, arguments or thought content.
A first thought-only/control packet is not public output. First valid function-call
output event may qualify as event TTFT with its explicit boundary.
Nonstream/embeddings/no valid boundary: TTFT absent, availability unavailable;
never use response completion latency as TTFT. Retried logical first-output timing
and winning-attempt TTFT are separate fields. Emit model.first_token at most once
per applicable attempt. SSE first event/browser render remain separate M2/future data.

### Usage extraction and FinOps handoff

GenerateContent raw usage / ADK LlmResponse exposes:
- prompt_token_count -> input_tokens, includes cached input;
- candidates_token_count -> candidate output component;
- cached_content_token_count -> cache_read input subset, never add again to input;
- total_token_count -> authoritative total, not reconstructed input+output;
- thoughts_token_count and tool_use_prompt_token_count -> numeric components only;
- modality token counts and traffic type -> typed bounded optional components.

Output semantics must retain candidate and thought components separately; map the
standard output count to provider-reported generated output including thoughts when
both component counts are available and disjoint per SDK contract. If a complete
output total cannot be established, keep standard output unavailable/partial and
record known candidate/thought components with explicit completeness. Do not infer
missing components as zero or treat thought count as thought content. Never sum
cached and prompt counts or re-add tool-input counts into a total that includes them.

Streaming snapshots are cumulative per provider response: retain authoritative final
snapshot; repeated ADK accumulated terminal responses do not add usage. Distinct
attempts remain distinct; truncated streams retain last known snapshot as incomplete.
Unknown usage/status of billing remains unknown, including timeouts after submission.
Usage parse failure degrades telemetry, not response execution. Integer bounds,
finite/negative/bool rejection and inconsistent-component status tested. Embeddings
use available per-item token stats and billable characters; integral float counts
may normalize only when finite/nonnegative/exact. SDK vectors/text not exported.
No text-length tokenizer estimates or fabricated token counts.

Define inert ModelUsageObservation carrying schema_version, logical_call_id,
provider_request_id if safely validated, deterministic attempt usage_event_id,
parent/retry relation, timestamp, provider/service/model/version, agent/operation,
workload/initiator, mode/attempt/retry, status/error/finish, latency/TTFT availability,
input/candidate/output/cached/total/component quantities and completeness/source,
run/session/optional root-turn/native trace/span correlation and environment.
Model call UUID created before first request; usage_event_id derived from call UUID,
provider, operation, attempt (independent of sampled/native span availability).
System calls use workload call identity without fictional run/session IDs.

One finalized observation per actual provider attempt regardless of trace sampling,
including failures/unknown usage. A single sink interface receives metadata; test
sink verifies exactly-once emission and retry identities. No production memory queue
is represented as reliable accounting, no temporary DB/spool/cost database. M3's
handoff is NOT durable and cannot recover process-loss usage. M10 must implement
uniqueness/durable outbox, recovery, append-only corrections/late usage and persist
these identities using the one accounting path, independently of exporter sampling.
Existing strict UsageEvent requires turn correlation and financial fields; do not
construct fictional UsageEvents for early/system calls. Add separate compatible
model-observation contract that future M10 maps to it or extends it explicitly.
No prices, estimated/billed cost, EUR conversion or financial truth in instrumentation.

### Retry, errors, timeout and failure isolation

Gemini.retry_options defaults None. Installed SDK retry_args(None) makes ONE attempt;
its documented five-attempt exponential defaults apply only when retry options exist.
Direct Client construction likewise must be checked against effective options without
forcing defaults. SDK retries are inside _async_request around _async_request_once;
request-level override, backoff and HTTP/auth transport preserved.

Logical operation owns attempt spans, ordered counts and eventual outcome. Each
new provider submission gets separate attempt/observation even on eventual success.
Application remediation is a NEW logical operation, linked to prior call/reason where
server knows it, not miscounted as a transport retry or repeated tool execution.
ADK tool loops create new logical calls. No callback short circuit/cache/pseudo-vector
creates a billable attempt. Never assume failed/cancelled requests cost zero.
Raw usage from an attempted request that returned it stays on that attempt; transport
errors with no returned usage are incomplete, not silently absent. No inferred retry
charges: M10 reconciles unknown quantities against provider billing.

| Condition | Canonical classification / planned observation |
|---|---|
| TimeoutError/asyncio timeout, httpx timeout, SDK HTTP 408/504 | MODEL_TIMEOUT, TIMEOUT; distinguish existing warmup cap from provider timeout |
| SDK APIError status 429 | MODEL_RATE_LIMIT, FAILED attempt; preserve eventual successful logical result |
| SDK 401/403/authentication exception from model request | MODEL_AUTH_ERROR; approved type/code only |
| SDK 5xx/network/provider failure | MODEL_PROVIDER_ERROR; code and known exception category only |
| Provider malformed envelope / empty invalid model response | MODEL_INVALID_RESPONSE; bounded response validation category |
| Structured JSON/schema validation after successful transport | Provider attempt remains completed with usage; logical validation observation MODEL_INVALID_RESPONSE, linked call and repair relation |
| Cancellation / GeneratorExit / stream abandoned | CANCELLED/incomplete as applicable; cleanup in finally, re-raise identical cancellation; do not swallow it |
| Policy/source gap/safety-block finish | Safe finish/outcome classification; not automatically infrastructure failure |

Do not stringify raw exception, payload/details, body, request URL or headers.
No automatic record_exception/status descriptions/raw stack exports. Known exception
class maps to bounded category; unknown class is provider_error, not a free-form label.
M3 observes existing timeouts: warmup 30s cap; dense retrieval existing caller cap;
main generation currently has no newly enforced SLOPANOC_MODEL_TIMEOUT_SECONDS cap.
Do not activate the M0 120s future timeout setting; remaining budget/retry controls
belong M6. Preserve SDK timeouts and cancellation; add no await for remote telemetry,
no hidden wait/retry and no telemetry-owned background work blocking model completion.
Span/usage/metric/log failures are individually guarded and report M1 Health counters
(trace/metric/log failed/filtered as applicable), with bounded safe fallback; no recursive
logging or secondary model invocation. Business response/yield/raise semantics primary.

### Privacy and Collector compatibility

Default metadata-only. ADK 1.33.0 has ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS and
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT; raw trace_call_llm/request/response
logging can hold contents unless disabled. Plan explicit effective capture=false
policy at runtime initialization before model use, with local config parsing through
Settings; do not rely solely on content filtering after SDK capture. No new GenAI
auto-instrumentor installed/enabled. Native ADK/SDK scopes remain excluded by M1.
ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS defaults TRUE in installed ADK, so this
explicit source-level disable is required in addition to M1 filtering. Production
structured formatter still must never evaluate raw SDK log args/message.
When observability is disabled, M3 must still prevent automatic content capture in
any initialized SDK exporter; inert imports/lazy client construction remain unchanged.

Both Collector configs currently admit M2 safe names/typed IDs/counts and M1-only
health metrics. They do NOT admit required model semantic metadata, agent arrays,
new model names/instruments or histogram units. Add exact safe model names/events,
provider/model/agent/operation enums, integer counts, boolean modes, bounded finite
latencies/attempts/statuses/finish arrays; positive tests must prove survival. Model
name values from Settings use validated bounded pattern/registry, unknown metric
values map to registered other; never allow arbitrary provider strings through.
Extend model histograms/Sums with appropriate units/point validation in both SDK
HealthMetricExporter and Collector rather than blindly keeping value-based gauge code.
Keep M1 health schema/labels unchanged with per-instrument projections.

Explicit content denials: gen_ai.prompt/completion/input.messages/output.messages/
system_instructions/tool.call.arguments/tool.call.result/tool.definitions/
retrieval.documents/retrieval.query.text plus SDK gcp.vertex.agent.llm_request/
llm_response and arbitrary events/log bodies. No gen_ai.* wildcard. No tool names,
arguments/results, prompts, conversation/system histories, thought content, Teams,
knowledge/document/attachment bytes, credentials, auth headers or foreign baggage.
Restricted call/request/run/session identifiers only in safe trace/handoff fields,
never metrics. Use current typed server-owned identifiers, no free-form strings.

### Model metrics

ModelRequest count per actual attempt; logical-operation count separately if needed.
Histograms: request duration (seconds), available TTFT (seconds), authoritative token
usage; counters: retries, failure/timeouts, missing/incomplete usage. Prefer installed
semantic instrument names gen_ai.client.operation.duration and
 gen_ai.client.token.usage where supported; choose documented slopanoc.model.* names
for business/TTFT/retry/availability measurements without standard equivalents.
Register exact names/units/buckets in one metrics owner; validate Sum/Histogram types,
boundaries, count/sum/finite values and clear arbitrary exemplars before encoding.
Allowed dimensions: environment, bounded provider/model/agent/business operation/status,
error_code only on failures; token.type only input/output on token instrument if
explicitly added to canonical cardinality contract (or separate input/output instruments).
No version/request/call/run/turn/session/user/chat/case/fault IDs; no raw errors/text.
Metric model values use configured finite registry plus other, not dynamically
expanding discovered model versions. Unknown counts do not add zero samples;
missing-usage counter records availability gap. Cached subset measured separately,
never added to token total. No dashboards/SLOs/alerts in M3. No financial cost metric.

### UI impact: five required decisions

1. M3 creates safe model/agent/operation latency, TTFT availability, tokens, retries,
   finish/errors for future Overview, Tracing and Diagnostics; financial summaries
   remain unavailable until M10.
2. Internal typed model/handoff contracts support later M7 API/persistence and M8 UI.
   No current support/history query API or durable record is added; traces are not
   a replacement for Cloud SQL operational/accounting records.
3. No UI belongs to M3; preserve existing SLOPANOC Settings/layout/progress/SSE.
4. Future server RBAC applies: Operator/Developer-SRE diagnostics subject to scope;
   FinOps numeric usage/cost does not grant conversation/Teams/knowledge/case contents.
   No telemetry grants new user capability now.
5. Effective tracing/model-capture policy is read-only environment/backend/IaC-owned.
   No runtime editor, IAM mutation, UI deployment/config write or Terraform bypass.

### Explicit files expected to change during later implementation

New central modules:
- backend/observability/model_instrumentation.py — delegating model operation lifecycle;
- backend/observability/model_provider.py — pinned GenAI client/attempt/stream adapter;
- backend/observability/model_context.py — immutable task-local attribution/composer;
- backend/observability/model_usage.py — pure typed usage extraction/sink handoff.

Existing foundation/config:
- backend/observability/{schemas,attributes,redaction,tracing,metrics,logging,runtime}.py
  — compatible metadata/events/metrics/privacy projection and lifecycle registration;
- backend/observability/turn_trace.py — minimal safe-parent accessor only;
- backend/config/settings.py — shared model adapter + explicit privacy/compatibility
  policy via existing Settings reader, no provider/config behavior redesign;
- backend/config/model_warmup.py — explicit detached warmup attribution;
- requirements.txt — pin current google-genai 1.75.0 if needed for private adapter;
  no ADK/OTel upgrades or new auto-instrumentors.

Minimal attribution integration:
- backend/agents/{team_manager,incident_manager,technical_authority_engineer,
  problem_manager,automated_operations_engineer}/agent.py — compose shared observer;
- backend/agents/team_manager/direct_read_fast_path.py and
  read_continuation_execution.py — callback-overriding variants and operation context;
- backend/agents/team_manager/source_requirements_completion.py,
  governed_knowledge_completion.py; backend/agents/incident_manager/provenance_compliance.py
  — explicit classification/remediation context around existing calls;
- backend/agents/technical_authority_engineer/agent_tool.py — context at existing
  regeneration/reselection boundaries and validation link, no governance changes;
- backend/api/chat_service.py — presentation/retry operation context only if not
  supplied by agent variant; existing root/executor/SSE untouched;
- backend/knowledge_ingestion/gemini_image_interpreter.py — system attribution;
- backend/tools/knowledge/dense_similarity.py and backend/knowledge/embeddings/service.py
  — central client delegation and bounded attribution, preserve dependency boundaries.
  Generic knowledge domain/retrieval packages remain ADK-free.

Validation/config/docs:
- backend/tests/test_observability_model_instrumentation.py (new);
- backend/tests/test_observability_model_provider.py (new);
- backend/tests/test_observability_model_usage.py (new);
- backend/tests/test_observability_model_coverage.py (new);
- existing test_observability_{contract,policy,logging,exporters,collector,runtime}.py
  and test_chat_service_root_trace.py — projection/lifecycle/privacy compatibility;
- infra/observability/collector/{config.yaml,config.local.yaml,README.md};
- docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md and backend/observability/README.md
  — semantic mapping, ownership and explicit user ledger deferral;
- .agent/OBSERVABILITY_EXECUTION.md — plan/results and per-exit evidence.

Only edit these where required; update this file before any additional path. No
Alembic/schema/migration/ledger/prices/Terraform/frontend/deploy files expected.
Do not replace legacy perf_timing logs/tests opportunistically; compatibility audit
required if canonical telemetry makes a narrowly scoped bridge necessary.

### Validation plan (NOT RUN for M3)

Use isolated in-memory SQLite, fake models/SDK transports/Runner/gateway/secret clients,
private M1 providers and credentials-free loopback Collector. Reinspect/recreate the
local launcher/guard before use; no live GCP/Graph/model/ADC/remote DB tests. Provider
fixtures use actual installed SDK with fake httpx/aiohttp responses to prove the
retry seam rather than a toy wrapper that assumes one attempt.

Coverage gate compares static inventory/AST provider seams to explicit scenario
fixtures: all 20 inventory rows, clones/nested runners, generation streaming/nonstream,
embeddings split/batched/cache/fallback, no-model callback synthetic return, disabled
features, warmup and independent ingestion. No obvious provider call remains outside
central adapter. Future added model seams must fail inventory coverage until mapped.

Parentage tests assert complete exported native span graph under M2 root for normal,
concurrent different/same-session, nested specialist, finalizer/remediation, planning
classification, concurrent embedding batches and streaming; no foreign orphan parent.
Early genuine outer invocation binding, late root IDs, nested temporary sessions,
copied live/detached/post-terminal contexts, explicit background detachment and cancellation
must not exchange root/agent/call identities. Correct operations from server context;
no free-form text-derived routing/labels. Exact one end per attempt/logical operation.

TTFT fixtures separate provider-start/first output packet/ADK aggregation/SSE first
message with injected clock; usage-only/empty/thought/control packets do not fabricate
TTFT. Function-call boundary marked event; nonstream/failed-before-output unknown.
Streaming cancellation and generator close retain incomplete known usage.

Usage tests: authoritative input/candidate/thought/cached/tool/total and embedding
counts; missing vs zero; conflicting/partial/invalid/negative/NaN/bool/overflow; repeated
cumulative chunks and ADK final aggregation; no double counting cache/thoughts/batches.
Retries: options=None single attempt, explicit retry success/exhaustion/backoff,
request-level overrides, timeout/cancel, failed attempts with usage/unknown usage;
exact distinct deterministic observations even when traces sampled/dropped/disabled.
Business repairs linked separately and no new retry/tool execution/prompt/config change.

Error fixtures cover taxonomy table, successful transport followed by structured
validation failure, recoverable retry then success and expected policy source gap.
Inject span/create/end/usage parsing/metric/log/export faults; provider yields/exception
identity/order unchanged and M1 safe health reports degradation. Timeout settings
must not accidentally activate. SDK fingerprint mismatch remains coverage failure.

Privacy sentinels in prompt/system/history/model/Teams/knowledge/attachment/tool args/
results/credentials/SDK errors go through real adapter + SDK + production formatter
and actual Collector. Inspect attributes/events/descriptions/links/exemplars/all log
bodies/decoded OTLP; none may contain sentinels. Exercise capture envs set true and
verify enforced false metadata-only policy; positive metadata must survive. Both
Collector 0.160.0 configs validate; run ONLY credentials-free local config.

Planned commands after the new test files exist:
```sh
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_runtime.py backend/tests/test_observability_logging.py backend/tests/test_observability_exporters.py backend/tests/test_observability_collector.py backend/tests/test_observability_turn_trace.py backend/tests/test_observability_active_runs.py backend/tests/test_chat_service_root_trace.py backend/tests/test_observability_model_instrumentation.py backend/tests/test_observability_model_provider.py backend/tests/test_observability_model_usage.py backend/tests/test_observability_model_coverage.py
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_p2_model_call_correlation.py backend/tests/test_p2_activity_truthfulness.py backend/tests/test_p4b2_synthesis_compression.py backend/tests/test_model_warmup.py backend/tests/test_multimodal_agent_tool.py backend/tests/test_tae_structured_output_recovery.py backend/tests/test_p5_1j_provenance_compliance.py backend/tests/test_p5_1j_source_requirements_gate.py backend/tests/test_operational_continuation_routing.py backend/tests/test_read_continuation_regression.py backend/tests/test_read_continuation_trust_boundary.py backend/tests/knowledge/test_ingestion_image_interpretation.py backend/tests/knowledge/test_vector_embeddings.py backend/tests/knowledge/test_dependency_boundary.py
.venv/bin/python -m compileall -q backend/observability backend/agents backend/config backend/api backend/tools/knowledge backend/knowledge_ingestion backend/knowledge/embeddings
.venv/bin/python -m pip check
git diff --check
```
Also run exact 27-file M2 regression selection in the preceding M2 plan (app/chat/
streaming/lifecycle/cancel/rewind/persistence/ownership/command/approval/broker/thread/
settings/startup) plus clarification and TAE negative-selection regressions. Reuse
its exact commands, not an unsafe unguarded full pytest invocation. Frontend unchanged:
if a shared contract is touched, run preceding six-file 211-case frontend command
and npm run build; otherwise record not applicable. No skipped required local gate.

Performance: injected fake provider waits/stream chunks, disabled baseline vs enabled
none vs enabled local-exporter; alternating order, warmups excluded, >=80 measured
iterations per scenario. Include nonstream, stream, retries, parallel/nested calls,
embedding batch and failure/cancel. Measure wrapper-only time/CPU separately from
fake wait, response timing, memory/call-state cleanup and queue depth; report median/
p95 added synchronous ms with <10ms initial local target. Do not claim production
CPU <3% or network/cloud overhead from synthetic evidence; full-load/production M16.
No real model calls/billing for benchmarks.

### M3 exit criteria (individually pending implementation)

| Criterion | Required evidence | Current result |
|---|---|---|
| Every real model path covered | Inventory scenarios and actual SDK request counts, including nested/embedding/system and zero-call synthetic paths | NOT RUN |
| One canonical attempt span | Correct logical/attempt identity, span closure, sampled/export behavior | NOT RUN |
| Correct run/agent/operation attribution | Native graph, concurrency/nested/root-late IDs; independent warmup/ingestion | NOT RUN |
| Streaming TTFT honest | Raw boundary vs SSE/aggregation fixtures; unavailable where unmeasurable | NOT RUN |
| Authoritative usage correct | Counts/components/completeness/deduplication and embedding metadata tests | NOT RUN |
| Retries and future accounting path | Every actual attempt finalized once to single sink, stable identities; usage unknown explicit | NOT RUN |
| Errors/timeout/cancel correct | Exact taxonomy and transport-vs-parse observations; eventual-success retries | NOT RUN |
| Metadata-only privacy | SDK/JSON/OTLP/real Collector sentinel absence with positive field survival | NOT RUN |
| Bounded metrics | Registered labels/instruments, valid histogram export, no high-cardinality IDs | NOT RUN |
| Failure isolation / unchanged business | Fault injection, same prompts/config/yields/results/exception/retry/tool/authority | NOT RUN |
| Imports and applicable regressions | Focused M0–M3, backend/governance/SSE and conditional frontend commands pass | NOT RUN |
| Collector compatibility | Both configs validate; local pipeline exports safe spans/logs/histograms | NOT RUN |
| Local overhead measurement | Representative synthetic benchmark with limits explicitly scoped | NOT RUN |
| Scope / safety / hygiene | Source/diff/hash review; no M4+/DB/ledger/cloud/UI/unrelated code | Planning only PASS; implementation pending |
| Original roadmap durable row + 100% persisted ledger | Actual durable M10 ledger/outbox/accounting validation | DEFERRED BY EXPLICIT USER SCOPE — NOT SATISFIED |

No M3 runtime criterion is marked PASS from documentation. User-scoped implementation
can be locally validated after all applicable rows pass; original durable criterion
must remain explicitly deferred, not asserted implemented. M4 remains blocked now.

### Planning validation actually performed

- Entry records/specifications/source/installed SDK hooks, retries/usage/privacy,
  Collector filters and tests audited; no live provider construction/import of app.
- Official ADK callback and GenAI SDK documentation read as supplemental confirmation:
  https://adk.dev/callbacks/types-of-callbacks/ and
  https://googleapis.github.io/python-genai/ . Current web docs may describe newer
  releases; local installed source is authoritative for 1.33.0 / 1.75.0 behavior.
- Reinspected /private/tmp/slopanoc-m1-test-guard/{run_tests.py,sitecustomize.py}:
  clears inherited SLOPANOC variables, SQLite memory, Vertex/warmup off, non-loopback
  connections denied. Fresh command:
  `.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_turn_trace.py backend/tests/test_observability_active_runs.py backend/tests/test_chat_service_root_trace.py`
  **137 passed, 1 existing Starlette deprecation warning, 3.10s.** No M3 tests exist
  or were run; full M1/M2 prior acceptance evidence remains historical above.
- This pass actual files changed: **.agent/OBSERVABILITY_EXECUTION.md only**.
  Repository file SHA256 snapshot captured before edit for final preservation check;
  final repository SHA256 comparison PASS: only this record changed, zero new/deleted
  files and every preexisting non-plan file preserved. git diff --check PASS; entry
  tracked/untracked status reviewed (existing M0–M2 local work preserved).
- Failures/repairs in this planning pass: none. No architecture implementation.

### Cloud status, limitations, risks and readiness

M3 required remote/cloud/external operations: **NONE**. No deployment, migration,
IAM/billing/API/configuration/Terraform-state/Graph mutation, cloud credential use or
live model inference performed. Collector changes are future LOCAL source edits.
M3 cloud deployment status: **NOT EXECUTED — no M3 remote operation required**.
Inherited M1 production deployment: **NOT EXECUTED — USER APPROVAL REQUIRED**; exact
pending operations remain recorded above, and this plan grants no approval.

Risks to resolve in local implementation tests: private SDK seam compatibility and
stream-body closure; raw-vs-aggregated TTFT; preserving lazy shared model/client
identity; ADK callback synthetic returns and nested attribution reset; foreign
filtered parent spans; numeric thought/tool/cache components; embedding request
splits/fallbacks; histogram export through current health-only filter; explicit
content-capture disable policy. Missing provider usage remains unknown. Main-model
remaining-budget enforcement belongs M6. Full durable accounting/process-loss recovery
belongs M10 under explicit scope override, never supplied by this telemetry sink.
No known blocker to preparing local M3 implementation under this scoped plan;
compatibility/coverage failures discovered during implementation must be repaired
before local acceptance. No claim of production overhead or live cloud validation.

**Ready To Implement M3: YES** — plan prepared; a subsequent implementation request
is required. **Ready for Next Milestone: NO.** M3 is **PLANNED — NOT IMPLEMENTED**;
M3 validation **NOT RUN**; M4 **BLOCKED**. Stop after planning.


## M3 implementation authorization — 2026-10-07

Approved M3-only implementation request received; M0/M1/M2 local gates rechecked.
All M3 exits pending validation; M4 blocked. Durable accounting ledger: **OWNED BY
M10 — NOT IMPLEMENTED IN M3 BY DESIGN** (explicit approved architecture, not a
blocking M3 criterion). No remote operations authorized or needed.

Reinspection discovered SDK aiohttp internal retry inside _async_request_once, and
embedding SDK request splitting: physical transport submissions must be observed.
Design refinement: prefer public ADK BaseLlm delegating wrappers with immutable
agent attribution, rather than composing callbacks whose order/short-circuit semantics
are business-sensitive. Variants bind a bounded operation on the wrapper. Shared
underlying registry BaseLlm/client remains cached and lazy. Central private adapter
will observe per-instance transport request/send and dynamically created aiohttp
sessions plus decoded HttpResponse streams; no class/global SDK patches, source
edits, transport substitution or retry changes. Compatibility checks precede install.
This catches aiohttp retries and split embedding requests individually.
Privacy capture disable is fixed policy before SDK use, even in disabled mode.
Additional expected file: backend/observability/model_metrics.py (bounded instruments
and histogram export policy kept separate from M1 health). No new agent/tool spans.
All other scope/invariants/tests/UI decisions in the approved plan remain applicable.

Additional expected file: backend/observability/model_adapter.py — public BaseLlm
delegation; private SDK access stays exclusively in model_provider.py.

M3 intermediate validation: 175 passed / 2 failed. Repairs: removed Settings import
from metrics to preserve package dependency direction; bounded known-model registry
uses other for unsupported models. Existing warmup identity assertion is updated to
verify the SAME cached underlying provider through public wrappers, not two wrappers
with different ownership. Added expected change: test_model_warmup.py. Original
warmup prompts/options/retry/client reuse remain required and tested.

Inventory refinement: TAE selection-contract/negative-selection/progression/gap
remediation messages are additional existing model operations in agent_tool.py;
all pass through _run_specialist_message and now receive bounded remediation
attribution via an observation-only helper. Actual SDK 1.75.0 embed_content uses
one batch request for the audited contents path (planning's splitting assertion
was an assumption, corrected). Per-SDK-request identity separates any multiple
submissions from true retries; only the latter increment retry metrics.

## M3 — implementation and local acceptance — 2026-10-08

**IMPLEMENTED AND LOCALLY VALIDATED. All blocking local M3 exit criteria PASS.**
This result supersedes the historical M3 planning/pending status above. User scope
explicitly assigns durable accounting to M10; no criterion for that functionality
is marked PASS here. M4 is not implemented or authorized by this completion.

### Implementation performed and architecture preserved

- Central public ADK BaseLlm wrapper delegates original requests/options/results and
  shared cached lazy providers. Immutable agent wrappers survive clones without
  callback changes. Explicit bounded model_activity scopes label existing business
  purposes only; no authority, provenance, evidence, command or approval decision
  reads telemetry. Warmup/image ingestion detach from user-turn context.
- ModelOperation owns INTERNAL slopanoc.model.operation; each actual transport
  submission owns exactly one CLIENT gen_ai.request. Safe M2 root/phase parentage
  avoids filtered ADK parents. Context restores before generator yield and closes
  on error/cancel/abandonment. Late/post-terminal contexts cannot reopen roots.
- Sole private SDK boundary model_provider uses exact installed ADK 1.33.0 / GenAI
  1.75.0 fingerprints/signatures and per-instance methods/transports. SDK auth,
  backend, pools, timeouts, retry/backoff and response aggregation stay owned by SDK.
  HTTPX and aiohttp internal retry tested. Partial installation rolls back; shape
  mismatch degrades health and delegates execution, never passing coverage silently.
- Request/submission/call identities distinguish retries, parallel/nested operations
  and application remediation. Successful transport followed by invalid structured
  output records a separate linked validation span, with no duplicate usage event.
- TTFT is monotonic request-start to first qualifying RAW nonthought output event,
  explicitly first_provider_output, before ADK aggregation/SSE. Empty/usage/thought/
  malformed empty function/inline packets do not establish it. Function-call presence
  can qualify without retaining arguments. Nonstream/embedding TTFT stays null.
- Provider numeric snapshots replace cumulative snapshots. Candidates/thoughts stay
  separate; output combines them only when both known; cached input is not added
  again. Missing/invalid/conflicting/truncated quantities remain UNKNOWN/PARTIAL,
  including failed attempts. Available embedding statistics/characters are retained;
  input text/vectors are excluded. No tokenizer estimates, costs or prices.
- One immutable UTC-aware ModelUsageObservation per finalized physical attempt
  goes to the single injected synchronous UsageObservationSink independently of
  sampling, disabled tracing and exporter outage. No default persistence, queue,
  writer, replay or accounting-grade idempotency. Failures are isolated/health-reported.
- Exact model metadata/event/metric allowlists extend Python and BOTH Collector
  configurations. Six finite metric dimensions and valid counters/histograms only;
  unknown models map to other; exemplars/content/foreign events/headers denied.
  ADK/GenAI capture switches forced false before runtime/model use, also disabled.
- No M4 agent/tool spans, M5 dependency spans, M6 deadlines, M7 storage/APIs, M8 UI
  or M10 financial functionality. Existing SSE, rewind and session ownership remain.

### Final inventory / coverage results

Every row below is **COVERED** by the central provider layer when an actual request
occurs. Static AST gate rejects newly introduced bypasses; actual installed SDK fake
transport tests exercise generation, both embedding callsites, image and warmup.
Business regression suites verify the existing bounded retry/governance paths.

| Final existing path | Agent / bounded purpose | Evidence |
|---|---|---|
| Team Manager main | team_manager / orchestration | Real configured wrapper + parallel root graph + canonical ChatService/Collector |
| Presentation-only Team Manager | team_manager / presentation | Clone ownership + purpose graph + continuation regressions |
| _retry_trusted_presentation_once | team_manager / presentation | Existing decorated boundary + synthesis/continuation regressions |
| Fast-path trusted presentation | team_manager / synthesis | Decorated boundary + native purpose graph + read regressions |
| Incident Manager main/nested | incident_manager / specialist_reasoning | Real wrapper + real ADK AgentTool nested provider/tool/response rounds |
| Fast-path Incident Manager | incident_manager / specialist_reasoning | Clone/callback-order gate; real ADK synthetic result emits zero requests |
| Incident Manager continuation | incident_manager / specialist_reasoning | Clone gate + operational/read/ambiguous-selection regressions |
| Incident Manager synthesis-only | incident_manager / synthesis | Rebound clone + native purpose graph + read regressions |
| Source-requirement declaration | team_manager / classification | Decorated boundary + native purpose graph + source-requirement regressions |
| Governed-completion remediation | incident_manager / remediation | Decorated boundary + purpose graph + governed/provenance regressions |
| Provenance-compliance retry | incident_manager / remediation | Decorated boundary + purpose graph + bounded retry regressions |
| TAE main | technical_authority_engineer / specialist_reasoning | Real configured wrapper + concurrent owner/root graph + authority regressions |
| TAE structured regeneration | technical_authority_engineer / structured_output_repair | Boundary + purpose graph + full structured recovery regressions |
| TAE action reselection | technical_authority_engineer / action_reselection | Boundary + purpose graph + structured/negative-selection regressions |
| TAE selection/discovery/progression/acquisition/gap/resume remediation | technical_authority_engineer / remediation | All additional existing helper callsites mapped before acceptance + purpose/recovery regressions |
| Problem Manager | problem_manager / specialist_reasoning | Actual configured wrapper executes installed SDK; concurrent root/agent graph |
| Automated Operations | automated_operations_engineer / specialist_reasoning | Actual configured wrapper executes installed SDK; concurrent root/agent graph |
| Shared-model warmup | system / warmup / warmup workload | Actual _run_warmup_request + original warmup prompt/cap/client reuse tests |
| Image interpreter | km_image_interpreter / image_interpretation / ingestion | Actual configured wrapper with fake SDK and inline image; no user-root/content leakage |
| Dense retrieval embeddings | knowledge_retrieval / embedding_query or embedding_document | Both actual _embed paths + concurrent embedding SDK calls + retrieval dependency regressions |
| VectorEmbeddingService | knowledge_embedding / embedding | Actual embed_text provider call and failed-provider fallback; empty/sync pseudo-vector produces zero observations |

No configured live/bidi/alternate fallback/compaction/planning-only inference provider
was found. Existing default mixed orchestration purpose is honest rather than inferred
from text. Actual pinned SDK embedding path batches into one submission; a batch is
not a retry. Independent SDK submissions have independent IDs/attempt counters.

### Actual M3 files changed (41; compared to entry SHA256 snapshot)

New central modules:
- backend/observability/model_adapter.py
- backend/observability/model_context.py
- backend/observability/model_instrumentation.py
- backend/observability/model_metrics.py
- backend/observability/model_provider.py
- backend/observability/model_usage.py

Existing foundation and model integration:
- backend/observability/{logging,metrics,redaction,runtime,tracing,turn_trace}.py
- backend/agents/automated_operations_engineer/agent.py
- backend/agents/incident_manager/{agent,provenance_compliance}.py
- backend/agents/problem_manager/agent.py
- backend/agents/team_manager/{agent,direct_read_fast_path,governed_knowledge_completion,read_continuation_execution,source_requirements_completion}.py
- backend/agents/technical_authority_engineer/{agent,agent_tool}.py
- backend/api/chat_service.py (M3 adds only presentation attribution; entry M2 work preserved)
- backend/config/model_warmup.py
- backend/knowledge/embeddings/service.py
- backend/knowledge_ingestion/gemini_image_interpreter.py
- backend/tools/knowledge/dense_similarity.py
- requirements.txt (compatibility comment on existing exact GenAI pin; no version upgrade)

Validation/source documentation:
- backend/tests/test_observability_model_{coverage,instrumentation,provider,usage}.py (new)
- backend/tests/test_observability_collector.py
- backend/tests/test_model_warmup.py (assert same underlying provider through wrappers)
- backend/observability/README.md
- docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md
- infra/observability/collector/{config.yaml,config.local.yaml,README.md}
- .agent/OBSERVABILITY_EXECUTION.md

### Validation performed and results

Guard launcher/environment reinspected before tests: inherited application variables
cleared, disposable SQLite memory, Vertex/warmup off, all non-loopback connections
denied. Actual SDK transports use fake HTTPX/aiohttp and fake API keys, never ADC or
live inference. Collector runs only config.local.yaml with minimal credentials-free
environment; production config is validated only. Sandbox loopback-bind escalation
was auto-approved for these isolated tests, not a remote/cloud mutation approval.

1. Exact 14-file focused command in approved M3 validation plan:
   **278 passed, 3 existing/dependency warnings, 17.33s.** Both pinned native
   Collector 0.160.0 configs validate. SDK → local Collector → decoded OTLP sink
   proves safe metadata/histograms survive; direct malformed OTLP bypass additionally
   proves independent Collector content denial. M0/M1/M2 regression gates still pass.
2. Guard invocation combining the exact 27-file M2 command and 14-file M3-specific
   regressions above, deduplicated, plus test_applicability_clarification_outcome_independence,
   test_clarification_service, test_clarification_retrieval_resumption,
   test_clarification_continuity and test_tae_negative_selection_completion:
   **746 passed, 204 warnings, 27.28s.** No skips/failures. Warnings include existing
   Starlette/ADK deprecations and SQLite fixture worker cleanup after loop closure;
   no runtime/provider/telemetry assertion failure. No shared database was touched.
3. After private-adapter rollback hardening, provider/model/TAE structured recovery/
   negative-selection follow-up: **114 passed, 86 warnings, 5.06s.** After final
   TTFT empty-event refinement, provider/model follow-up: **45 passed, 3 warnings,
   2.94s.** Scope-specific image/both embedding live branches: **23 passed, 2.32s**
   (included in the 278-case focused result, not additional distinct coverage).
4. Planned compileall of observability/agents/config/api/knowledge integration:
   PASS. pip check: PASS, no broken requirements (local pip-cache warning only).
   git diff --check: PASS. No frontend/shared wire contract changed, so conditional
   frontend tests/build are NOT APPLICABLE; no frontend completion claimed.
5. Temporary guarded benchmark command:
   `.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py /private/tmp/test_slopanoc_m3_overhead.py`
   **1 passed, 2 dependency warnings, 8.04s** on final model code.

### Failures encountered and repairs

- M0 dependency AST gate rejected Settings dependency in metric registry: removed
  that application import; finite static model registry maps unlisted names to other.
- Warmup exact Agent.model identity assertions updated for distinct ownership
  wrappers while preserving/test-proving identical cached underlying provider.
- Aiohttp fixture initially failed SDK ClientResponse type check; repaired fixture
  to use actual response subclass. Actual SDK internal retry now exercised unchanged.
- SDK embedding split assumption corrected by real installed SDK batching evidence;
  no fabricated per-item attempts or retry counter increments.
- Sampling fixture changed tracer sampler (the already-created tracer retains its
  sampler); production sampler behavior unchanged. Disabled/export-failure handoff
  subsequently verified independently.
- Broad model event addition initially broke M2 privacy regression: model events
  now scoped to model request/validation spans in BOTH Python and Collector filters.
- Safe model projection integration and Collector histogram/type/unit admission
  repaired; positive fields now survive and adversarial fields remain absent.
- Logical SDK timeout mapping reordered to inspect APIError code before classifying;
  known unrelated usage fields such as trafficType no longer invalidate token counts;
  conflicting subset/total metadata is preserved as partial, not asserted complete.
- Private adapter partial installation rolls back instance changes. Empty/ambiguous
  function/inline packets cannot fabricate TTFT. Validation observability failures
  isolated by a guard. Existing exact GenAI pin retained once, with no dependency change.
- Local Collector sockets require sandbox escalation; only local isolated tests
  rerun under automatic approval. No required local gate skipped or waived.

### Performance measurement (synthetic local evidence only)

80 measured iterations/scenario/mode after 8 excluded warmups. Rotate ordering of
raw unwrapped baseline, observed-disabled, enabled-none and enabled-local. Actual
installed SDK fake transports, identical request/config and 1ms injected provider
wait; nonstream, stream, two-attempt retry, parallel three-model calls, embedding
batch and real task cancellation. Wall/process CPU collected separately; paired
net wall differences exclude measured fake transport wait (parallel uses maximum
concurrent wait, not incorrectly summed waits). Retry backoff/SDK work remain in
the measured path; measurements are producer-path proxies, not literal production
CPU or inference-time estimates. Export flush is outside timed calls.

| Scenario | Local vs disabled median / p95 added ms | None vs disabled median / p95 added ms | Local process CPU median / p95 ms |
|---|---|---|---|
| Nonstream | 0.4295 / 0.6279 | 0.1371 / 0.2016 | 1.3105 / 1.8940 |
| Stream | 0.4959 / 0.7199 | 0.1580 / 0.2763 | 1.5760 / 2.1140 |
| Retry | 0.7819 / 1.2756 | 0.2418 / 0.4722 | 1.9320 / 3.1380 |
| Parallel 3 | 0.5213 / 0.7135 | 0.1292 / 0.1779 | 3.6820 / 4.1800 |
| Embedding batch | 0.4029 / 0.5618 | 0.1225 / 0.1621 | 0.7500 / 1.0160 |
| Cancel | 0.3287 / 0.8178 | 0.0589 / 0.3018 | 1.1255 / 1.8400 |

Local vs raw added median 0.3451–0.9296ms; worst p95 1.9326ms. Both comparisons
pass initial <10ms local target. All exporter queue depths zero after flush; local
capture retains 1496 spans within fixed 2048 capacity. 792 attempt handoffs in each
instrumented mode (none has 80 further memory-probe calls, total 872); raw emits zero.
Context cleanup asserted after every execution. Separate tracemalloc probe over 80
enabled-none calls after GC: retained delta 8555 bytes, peak delta 326571 bytes.
No durable/infinite call registry. Not proof of long-running leak freedom or <3%
production CPU; real network/Cloud Run/export/load qualification remains M16/deployment.
Benchmark/results remain temporary at /private/tmp/test_slopanoc_m3_overhead.py and
/private/tmp/slopanoc-m3-overhead.json; method/results persist here without payloads.

### Individually evaluated blocking M3 exit criteria

| Criterion | Result | Objective evidence |
|---|---|---|
| Every real provider/model path covered | PASS | Final inventory above; configured agents/clones, real nested ADK, actual image/warmup/two embedding callsites, AST bypass gate |
| One canonical CLIENT span per actual provider request | PASS | Fake SDK physical request/span/observation equality; logical-vs-attempt graph; HTTPX and aiohttp retries |
| Correct root/agent/operation attribution | PASS | Five owners under simultaneous isolated roots; real nested ADK; bounded purpose graph cases; server-owned boundaries |
| Honest streaming TTFT | PASS | Raw packet clock test; thought/usage/empty/ambiguous events excluded; first_provider_output boundary; nonstream/embedding null |
| Authoritative usage semantics | PASS | Known/zero/missing/invalid/overflow/conflict/component/cache/embedding tests; cumulative snapshots replace; cancellation partial |
| Every real attempt can emit unsampled M10 observation | PASS | One sink path, unique call/submission/deterministic attempt observation IDs; sampling/disabled/export outage tests |
| True retries individually observable | PASS | None-option single attempt, explicit retry eventual success/exhaustion, unchanged aiohttp internal retry/backoff; batch not retry |
| Warmup separately observable | PASS | Actual warmup helper retains shared provider/prompt/options/cap; no user run/trace/log correlation |
| Embeddings and image interpretation safely instrumented | PASS | Actual configured image model and both embedding callsites; cache/pseudo/empty paths not treated as provider requests |
| Error/timeout/cancellation mapping | PASS | HTTP/SDK code taxonomy, logical timeout, real cancellation/stream abandonment/error closure; separate structured validation outcome |
| Metadata-only privacy | PASS | SDK captures forced false; response/prompt/config unchanged; sentinel-negative span/handoff/JSON/decoded OTLP/independent Collector |
| Bounded model metrics | PASS | Fixed six dimensions and model registry; correct counters/histograms/units; ID/dynamic model/NaN/bucket rejection; exemplars cleared |
| Telemetry failure cannot alter model behavior | PASS | Usage/metric/log/sink/span-start/end/export failures + interface mismatch/partial install rollback; health degradation; original output/cancel preserved |
| Collector compatibility | PASS | Both native configs validate; only local config executed; positive model/health/turn metadata and histogram export |
| Imports and required regressions | PASS | 278 focused + 746 selected regressions + targeted final follow-ups; compileall/pip check; no applicable frontend change |
| Local performance measurement | PASS | 80 measured repetitions per scenario/mode, all added p95 <10ms; CPU/memory/context/queue limits explicit |
| Scope/safety/repository hygiene | PASS | Entry hash comparison 41 scoped changed files, no deleted file; tracked/untracked/diff review; unrelated local M0–M2 preserved; no remote mutations |

Durable accounting ledger: **OWNED BY M10 — NOT IMPLEMENTED IN M3 BY DESIGN**.
This is not PASS and not a blocking M3 local criterion under the user's approved
architecture. M10 must supply durable identity uniqueness/outbox/recovery, corrections,
pricing and accounting completeness through this one observation producer path.

### Known limitations; UI/Settings impact; cloud status

Provider usage may be missing/inconsistent; failed attempts are not assumed free.
Observations/sink/exporters are not crash-durable and a failing sink can lose usage;
M10 owns recovery/accounting. Private SDK upgrades require explicit compatibility
review and tests; unsupported SDK shape means telemetry coverage unavailable and
cannot be a passing acceptance state. No new deadline enforcement (M6), operational
durability/RBAC/API (M7), UI (M8), financial functionality (M10), production profiling
or live GCP validation. Performance evidence is synthetic local only.

Five required Settings decisions:
1. Safe model agent/purpose/usage/TTFT/retry/error metadata supports future Overview,
   Tracing and Diagnostics; no monetary cost summary exists yet.
2. Internal typed contracts support later protected M7/M8 projections. No API,
   durable record/history or cross-instance truth is added.
3. M3 has no UI work. Existing chat layout/progress/SSE/Settings remain unchanged.
4. Future server RBAC applies; FinOps numeric access does not grant raw content/case/
   Teams/knowledge access. No new user capability or role grant is introduced.
5. Effective capture/tracing configuration stays read-only backend/environment/IaC
   policy. No Settings editor/configuration mutation or Terraform bypass.

Cloud deployment status: **NOT EXECUTED — no M3 remote operation required**.
No GCP/Graph/shared DB/IAM/billing/GCS/Terraform/external-service mutation, deployment,
migration, credential provisioning or live billable model call executed. Collector
filters are local source only. Inherited M1 deployment remains **NOT EXECUTED — USER
APPROVAL REQUIRED**; its exact pending operations remain recorded above, and M3 grants
no deployment approval. No live-cloud acceptance is claimed from local validation.

**Ready for Next Milestone: YES — all blocking LOCAL M3 gates pass.**
**STOP. M4 has not begun and requires a separate user request.** No cloud mutation
or later milestone authorization follows from this readiness result.

## M4 — Agent and Tool Instrumentation: planning only

Planning date: 2026-10-08 (Europe/Bucharest). Authorization: attached M4 PLANNING
ONLY request. **M4 — PLANNED — NOT IMPLEMENTED.** This section supersedes the
historical M3 STOP instruction solely to permit this planning pass. M5 remains
blocked. No implementation, deployment, migration or remote operation is authorized.

### Gate and current-state evidence

- M0: VALIDATED COMPLETE; M1/M2/M3: IMPLEMENTED AND LOCALLY VALIDATED, equivalent
  to completion of their applicable local gates under the recorded approved split.
- M3 validation: PASS; individually evaluated criteria and 278 focused / 746
  regression results remain in the M3 completion record. Ready for Next Milestone:
  YES at M3. Inherited M1 cloud deployment is pending; no live-cloud completion claimed.
- **Durable accounting ledger = OWNED BY M10 — NOT IMPLEMENTED IN M3 BY DESIGN.**
- Actual current source inspected: canonical M2 TurnTrace/phases/active registry;
  M1 runtime/export/redaction/log/metric machinery; M3 ObservedModel, attribution,
  logical/physical operations, provider adapter, usage observation and bounded metrics.
  No central M4 agent/tool modules or canonical agent/tool spans currently exist.
- requirements.txt pins google-adk 1.33.0 / google-genai 1.75.0. Installed ADK source
  agrees with the proposed interception surfaces described below. No version change
  is planned. Existing private SDK provider adapter remains M3-owned.
- Team Manager agent factories, feature gates, copies, specialist AgentTools,
  direct/disposable runners, compliance/remediation calls, tool implementations,
  approval service/policy gate, routing enforcement, activity projections and gateway
  were inspected. backend/conversation does not exist in this tree. No live TurnPlan
  primary_agent/supporting_agents runtime field was found; primary_agent in the
  observability schema is a future contract, not execution evidence.
- Working tree contains substantial existing tracked/untracked M0–M3 and governance
  work. Preserve all of it. Do not derive M4 scope from the aggregate git diff.

Read AGENTS.md, .agent/PLANS.md and the prior execution record first. Authoritative
specifications read: docs/Telemetry/README.md, 00_MASTER_BUILD_CONTRACT.md,
01_TARGET_ARCHITECTURE.md, 02_TELEMETRY_DATA_CONTRACT.md,
03_INSTRUMENTATION_AND_RUNTIME.md, 04_RELIABILITY_TIMEOUTS_ERRORS.md,
11_SLOPANOC_INTEGRATION_MAP.md, 12_IMPLEMENTATION_ROADMAP.md,
13_TEST_VALIDATION_ACCEPTANCE.md, 14_CODEX_EXECUTION_GUIDE.md,
15_DEFINITION_OF_DONE.md, 18_STRUCTURED_LOGGING_STANDARD.md,
22_RELEASE_CONFIG_CORRELATION_CI.md. Also read 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md.

### Objective, implementation sequence and invariants

Observe each actual agent execution and logical tool invocation, retaining the M2
root and M3 model/usage instrumentation. Answer who ran, which tool, duration and
outcome. Do not trace every helper. Implementation sequence, when separately authorized:
1. Register bounded M4 names/attributes/result classifications/metrics and filters.
2. Add one central agent scope and one central tool scope using existing runtime.
3. Install a pinned, reversible, idempotent ADK telemetry-only adapter; add narrow
   direct-tool adapters and trusted role/purpose adapters where necessary.
4. Extend M2's model parent resolver to recognize trusted live M4 execution scopes.
5. Prove real boundary coverage, privacy, parentage, closure and failure isolation.
6. Run regression/Collector/import/performance gates and individually evaluate exits.

Preserve Team Manager sole user-facing presentation, specialist ownership, routing,
source requirements, authority/provenance, knowledge selection, ProcedureAction,
Command Authority, approval, execution, session/rewind/SSE/cancellation behavior.
Telemetry is OBSERVABILITY ONLY — NEVER EVIDENCE. It cannot invoke a tool, drive
routing, select evidence, grant/reject authority or retry business execution.
No M5 dependencies, M6 deadlines/retry/watchdog, M7 storage/API, M8 UI, M10 accounting
or M18 release pipeline work. Known configuration/version fields are reused only
when server-owned; absent versions are not invented.

### Agent execution inventory

The semantic execution unit is one BaseAgent.run_async invocation, not one model
request and not one wrapper helper. Each runner re-entry produces a separate agent
span; nested compliance execution is a real child execution even with the same name.
An AgentTool operation can contain several actual agent executions. A deterministic
retrieval failure before Runner creation produces a tool span and ZERO agent spans.
All rows use M3 models when a provider request actually occurs; synthetic fast-path
responses may generate no provider request. Warmup/embedding are not agent executions.

| Caller / real boundary | Agent / variant | Trusted role / purpose | Parent | Available tools / repeated execution | Cancellation and error semantics |
|---|---|---|---|---|---|
| chat_service._run_turn_events; _runner_factory; get_team_manager/get_fast_path_team_manager | team_manager outer, including feature-gated clones | coordination / orchestration | M2 orchestration phase | TM roster below; model may loop and delegate several times | Existing task cancellation/SSE cleanup; exception propagates to canonical safe turn handling |
| _presentation_runner_factory, chat presentation path | presentation_team_manager | coordination / presentation | current M2 phase | none; same conversational owner | Existing stream closure; no new retry |
| chat_service._retry_trusted_presentation_once | presentation clone in disposable session | coordination / presentation | current phase | none; one existing bounded re-entry | Existing bounded retry and safe failure; close runner/session |
| direct_read_fast_path._run_trusted_presentation | presentation_team_manager under nested IM fast path | coordination / synthesis | active IM execution if nested; otherwise phase | none; actual nested Team Manager execution, distinct from outer TM | Existing failure handling; restore outer IM attribution |
| source_requirements_completion.request_source_requirements_declaration | declaration-only team_manager clone | coordination / classification | current finalization phase | record_source_requirements only; one bounded remediation | Existing declaration capture/fail-closed and disposable cleanup |
| MultimodalAgentTool.run_async, called as incident_manager TM tool | _fast_path_incident_manager clone | unknown unless trusted routing supplies role / specialist_reasoning | incident_manager delegation TOOL span | full IM roster; synthetic model shortcut possible; multiple calls allowed except existing guards | Native Aclosing over nested Runner; original state forwarding/schema validation unchanged |
| standalone ADK Runner/root_agent | base incident_manager | unknown / specialist_reasoning | no user root when standalone | full IM roster | No fabricated run identity; same integrity callbacks |
| read_continuation_execution._run_specialist_and_collect from model-driven continuation | _CONTINUATION_INCIDENT_MANAGER | unknown / specialist_reasoning | current phase | get_resolved_chat_messages replaces read; no list or write tools | Existing fail-closed validation, cleanup, continuation semantics |
| same helper from deterministic retrieval, including IM fast-path callback | _SYNTHESIS_ONLY_INCIDENT_MANAGER | unknown / synthesis | active IM if nested; otherwise phase | none; retrieval happens before execution | No execution if retrieval fails; nested re-entry still separate span |
| governed_knowledge_completion.enforce_governed_knowledge_at_completion | base incident_manager in disposable session | unknown / remediation | current phase | full IM roster; one existing completion remediation | Expected no_result valid; unexpected Runner error propagates |
| IM after_agent integrity -> provenance_compliance._run_compliance_retry | compliance-retry IM clone | inherit same trusted specialist role, otherwise unknown / remediation | parent IM agent span | knowledge_select_evidence only; after_agent integrity disabled to avoid recursion | One existing bounded retry; finally closes/deletes internal session |
| TechnicalAuthorityAgentTool.run_async -> _run_specialist_message | technical_authority_engineer | primary only for server-forced governed route; otherwise unknown / specialist_reasoning | technical_authority_engineer TOOL span | knowledge_search, knowledge_select_evidence, procedure_action_catalog | Original validation, progression and fail-closed contract; Aclosing runner iteration |
| TAE _run_model_remediation -> _run_specialist_message | same TAE Runner/session re-entry | inherit trusted role / remediation | owning delegation tool (previous agent invocation has ended) | same TAE tools | Existing bounded selection, discovery, acquisition, progression, gap and resume remediation; each actual call separate |
| TAE _regenerate_structured_output -> _run_tool_free_message | _finalization_agent clone | inherit role / structured_output_repair | owning TAE TOOL span | none; regeneration once per existing recovery contract | Errors caught and return no valid output; trace failed invocation before caller fail-close |
| TAE _reselect_procedure_action -> _run_tool_free_message | tool-free TAE clone | inherit role / action_reselection | owning TAE TOOL span | none; existing MAX_ACTION_ID_RESELECTIONS | Invalid answer remains fail-closed; no telemetry reselection |
| PM AgentTool.run_async from feature-enabled TM | problem_manager | unknown / specialist_reasoning | PM TOOL span | tools=[] today; output schema validation at delegation boundary | Native nested session lifecycle, propagation/error/cancel unchanged |
| AOE AgentTool.run_async from feature-enabled TM | automated_operations_engineer | unknown / specialist_reasoning | AOE TOOL span | tools=[] today; output schema validation at delegation boundary | Same native lifecycle; no scheduler/action capability invented |
| ingestion GeminiImageInterpreter Runner | km_image_interpreter | system / image_interpretation, ingestion workload | independent background trace, never copied user root | tools=[] | Optional background agent observation, isolated from user agent scopes; preserve M3 ingestion exclusion |

There is no configured native sub_agents transfer/live-agent execution in this roster.
Historical troubleshooting_manager is the SAME TAE object/tool, not a sixth specialist.
Feature-disabled PM/AOE produce no spans; include enabled-feature fixtures to prove
coverage. Do not infer supporting roles from agent names or response prose. TM role
coordination is execution metadata, not a change to final ownership. No reliable
supporting plan exists presently: unknown is intentional and must be tested. Trusted
forced TAE routing can establish primary without constructing a competing TurnPlan.

### Complete tool inventory and invocation layers

ADK layer = google.adk.flows.llm_flows.functions._execute_single_function_call_async
-> record_tool_execution -> business callbacks -> BaseTool.run_async / FunctionTool.
Sync FunctionTools currently run on the event-loop thread; optional ADK tool pool
copies context. Do not move them to threads for M4. Direct calls below bypass ADK.
R0 = no tool-level automatic retry; attempt=1 per new logical invocation. Provider
retries are M3 and transport retries/dependencies M5; pagination is not a retry.
T0 = no independent agent/tool deadline; observe surfaced TimeoutError only. G =
PowerAutomateClient uses existing requests timeout, no automatic retry, but translates
Timeout into generic run_failure. Do not parse userMessage or invent TOOL_TIMEOUT.
K = existing async knowledge services/repositories and optional embedding internals;
no new outer timeout/retry. Result payloads are never emitted, including safe-looking
nested diagnostic dicts. Counts below are nullable bounded nonnegative integers.

| Actual tool name | Owner / layer / mode | Category | Nested calls; retry / timeout | Safe result classification / permitted count | Sensitive input/result; side effects |
|---|---|---|---|---|---|
| incident_manager | TM / MultimodalAgentTool / async | internal_coordination | nested IM Runner, tools and callback TM synthesis; R0/T0 | ok, no_result, selection_needed, proposed, executed, policy_blocked, error, unknown (closed adapter); evidence count only if validated | question, images, Teams/knowledge/answer content; child operations can write |
| technical_authority_engineer (historical alias same object) | TM / TechnicalAuthorityAgentTool / async | internal_coordination | nested TAE Runner/re-entry, governed search, progression, authority/control plane; existing bounded remediation not tool retry; T0 | completed, source_gap, policy_blocked, error, unknown; validated evidence count | symptoms, commands, knowledge, case/fault and answers; governed progression/session/database writes, no automatic command execution inferred |
| problem_manager | TM / AgentTool / async | internal_coordination | PM Runner/model; R0/T0 | completed, error, unknown | problem/RCA/answer content; no implemented PM tools today |
| automated_operations_engineer | TM / AgentTool / async | internal_coordination | AOE Runner/model; R0/T0 | completed, error, unknown | schedule/digest input/output; no implemented AOE action tools today |
| record_case_analysis | TM / FunctionTool / async | incident_case | CaseService membership validation and durable case repository; R0/T0 | recorded, policy_blocked, invalid, error, unknown | narrative/confidence/supporting IDs; governed durable write |
| record_conversation_target | TM / FunctionTool / sync | internal_coordination | trusted turn target capture; R0/T0 | recorded, invalid, unknown | target text; turn-local state only |
| record_source_requirements | TM and declaration-only clone / FunctionTool / sync | governance | SourceRequirementsCapture/event; R0/T0 | recorded, invalid, unknown | boolean declarations only; capture/state, never telemetry-driven source selection |
| teams_list_chats | IM / FunctionTool / sync | teams | gateway list, exact/ambiguous resolution and pending selection; R0/G | matched, no_result, selection_needed, invalid, error; chat count | topics/questions/chat IDs/members; read external, local selection state writes |
| teams_get_messages | IM / FunctionTool and direct deterministic continuation / sync | teams | gateway pagination, HTML/system filter, evidence mailbox; R0/G | completed, empty, invalid, error; message count | chat IDs, bodies, ranges, hosted-content IDs; read external, run/session bookkeeping |
| get_resolved_chat_messages | continuation IM / FunctionTool / sync | teams | delegates teams_get_messages once with trusted binding; R0/G | completed, empty, policy_blocked, invalid, error; message count | retrieved bodies/destination; read plus one-use marker; ONE outer span, no duplicate helper tool span |
| teams_get_hosted_content | IM / FunctionTool / sync | teams | fetch_and_validate_hosted_content gateway/image validation; R0/G | completed, invalid, policy_blocked, error | IDs/image bytes/content; read external, bounded image store |
| teams_get_all_hosted_content | IM / FunctionTool / sync | teams | exhaustive authoritative IDs, internal image fetch loop; R0/G | completed, partial, invalid, policy_blocked, error; image count | images/body metadata; read external, bounded image store; one invocation, not spans per helper/image |
| teams_get_members | system, authoritative contributor finalization / direct asyncio.to_thread / sync | teams | gateway getMembers; R0/G | completed, empty, error; member count | participant names/emails/IDs; read-only external; not registered to any agent |
| get_current_time_context | IM/continuation / FunctionTool / sync | utility | clock/timezone computation; R0/T0 | completed, invalid, unknown; no content | timezone/state values; read-only, do not export arbitrary timezone text |
| teams_propose_create_chat | IM / FunctionTool / sync | approval | payload normalization/create_action_proposal; R0/T0 | proposed, invalid, policy_blocked | title/members/approval content; local pending proposal write, no external write |
| teams_propose_send_message | IM / FunctionTool / sync | approval | same proposal service; R0/T0 | proposed, invalid, policy_blocked | message/chat/proposal content; local pending proposal write |
| teams_create_chat | IM / FunctionTool / sync | command_action | normalize -> authorize_write -> gateway -> consume_proposal; R0/G | executed, approval_rejected, invalid, error | title/members/payload; approval-controlled external write; never retry because telemetry fails |
| teams_send_message | IM / FunctionTool / sync | command_action | same authorize/gateway/consume chain; R0/G | executed, approval_rejected, invalid, error | full message/chat/payload; approval-controlled external write |
| knowledge_search | IM, TAE and direct TAE _server_governed_search / async | knowledge | governed search/ranking/applicability/repository/optional M3 embedding; R0/K | completed, empty, invalid, error; item count | query/document bodies/diagnostics; external reads and AVAILABLE run-state only |
| knowledge_select_evidence | IM, TAE and IM compliance clone / FunctionTool / async | governance | validates current-run selection, updates selected-evidence store; R0/K | accepted, explicit_empty, policy_blocked, invalid; selected count | knowledge/version/section IDs; governed run-state write, never trace-based selection |
| procedure_action_catalog | TAE / FunctionTool / async | troubleshooting | ensure_governed_scope, selected evidence, server-issued actions; R0/K | completed, empty, source_gap, unknown; action/unavailable count | command templates/parameters/source content; governed issued-action state, no command execution |

Direct/bypass coverage and exclusions:
- teams_get_messages in _execute_via_deterministic_retrieval requires one narrow
  central scope at the call site, before any IM Runner. Owner system/coordination;
  not a fabricated IM execution. Calls inside get_resolved_chat_messages remain
  helper work under that outer invocation, rather than another logical tool call.
- _server_governed_search directly awaits knowledge_search before/between TAE
  messages. One central scope with server-owned TAE coordination attribution. No
  fictitious agent execution; parent is TAE delegation TOOL or live phase.
- source_reference.resolve_authoritative_contributors directly uses
  asyncio.to_thread(teams_get_members,...). Scope encloses await in caller task;
  copied context reaches worker. Caller ownership is system, not the last model agent.
- source_images.py directly fetches/validates a hosted-content HTTP resource for a
  separate API request. This is a resource API dependency, not an AgentTool or agent
  execution. No M4 tool/agent span; M5 owns dependency instrumentation there.
- approve_proposal/reject_proposal/consume_proposal/authorize_write are trusted
  services, NOT model tools. ProcedureAction resolution, Command Authority,
  progression/control-plane/egress are governance helpers, not fabricated tools.
  Use only safe decision/lifecycle projections where an actual current boundary
  exposes them. M4 must not build a generic function profiler or remote action tool.
- RunTraceTranslator contains historical/capability names; not a tool registration
  inventory. In particular teams_get_members is system-only; Outlook/SharePoint/PM/
  AOE future tools are absent. No toolset/transfer tools are configured today.

### Central agent and tool instrumentation architecture

Preferred narrow adapter: installed ADK already encloses every BaseAgent.run_async
(including its before/after callbacks) in telemetry._instrumentation.
record_agent_invocation(ctx, agent), and every resolved async function invocation in
record_tool_execution(tool, agent, function_args). Replace ONLY these telemetry
context-manager implementations for the pinned supported shape, delegating execution
unchanged through their single yield. This is an explicit private ADK adapter, not
an unsupported assumption that public paired callbacks have finally semantics.

Agent scope:
- Start one INTERNAL slopanoc.agent span per actual invocation, using registry-
  validated canonical agent, execution role, bounded ModelPurpose and trusted root.
- Hold scope through before/after agent callbacks, nested compliance and model/tool
  loops. Callback short circuit is a real invocation but may have no model children.
- BaseException/finally classify/close once; observe cancellation/GeneratorExit.
  Restore both native context tokens and bounded execution descriptor in owner task.
- Do not wrap Runner and BaseAgent simultaneously. Install once; clones/model_copy/
  clone need no per-agent callback edits because they traverse the same boundary.
- Standalone/ingestion workloads have no user correlation or copied user parent.
  Warmup and direct embeddings remain outside all user agent spans.

Tool scope:
- Start one INTERNAL slopanoc.tool span around the actual ADK logical invocation,
  including callback enforcement, tool body/error callback, final response boundary.
- Yield a compatible TelemetryContext object with otel_context and mutable
  function_response_event: functions.py explicitly assigns this after invocation.
  Never export that object or function_args. Use shallow closed-field result adapters
  at completion, never str()/repr()/model_dump() of arguments/results.
- Before callbacks can prevent execution. Distinguish invocation from execution:
  known trusted guard outcome -> policy_blocked/not_executed; where no reliable
  signal exists -> execution_disposition unknown. Never claim the body ran solely
  because a callback result exists. Actual nested child agent/tool spans establish
  their own executions. Unresolved unknown-tool error precedes this boundary; it is
  not an actual tool invocation and must not receive a fabricated execution span.
- The direct call sites above use the same scope API, not another timer/context
  registry. They do not also wrap the tool callable itself, preventing duplicates.
- No tool arguments are retained by telemetry; no native trace_tool_call delegation
  that serializes payloads. The existing native telemetry helper metrics/logs are
  replaced for these two scopes, leaving ADK execution and M3 provider hooks intact.

Adapter gates: verify installed version/signatures and required TelemetryContext
shape; atomically install both surfaces; retain originals for explicit uninstall;
repeat initialization/shutdown safe; rollback partial installation; report bounded
telemetry.degraded on unsupported shape. Unsupported shape preserves business
execution through existing behavior, protected by M1 foreign-scope export filters
and existing capture=false policy, but is an M4 coverage FAIL, never a passing skip.
No business callback ordering, argument mutation, return value, retry or exception
handler changes. Agent/tool instrumentation failures cannot enter business error
callbacks or replace responses; scope lifecycle failures must independently degrade
health and preserve original exceptions, including BaseException cancellation.

### M2/M3 context reuse and trace hierarchy

Reuse model_context.Attribution/ModelAgent/ModelPurpose, _override and model_context
as the canonical agent/purpose context. Extend the same owner with an immutable
execution descriptor for live safe agent/tool span handles, role and trusted root
identity; this adapter is necessary because M3 attribution currently has no span
or primary/supporting field. No competing agent identity variable/registry. Tool
scope consults this context and server-owned owning agent. M3 matching-agent override
rule remains: outer TM purpose never overrides a nested IM/TAE model's ownership.

Important source fact: ModelOperation explicitly calls turn.model_parent(), whose
current implementation accepts only M2 _phases; simply attaching a current agent
span WILL NOT parent M3 models correctly. Narrowly extend TurnTrace.model_parent
through a trusted live execution-parent accessor. Prefer nearest live M4 tool/agent
for the SAME root, otherwise existing phase/root fallback. Reject closed scopes,
foreign ADK spans and terminal/copied late-turn handles. Do not rewrite
model_instrumentation/model_provider/usage or provider accounting. Models executing
inside knowledge tools (embeddings) are tool children with their existing retrieval/
embedding attribution, rather than misattributed specialist generations.

```text
slopanoc.turn
└── orchestration
    └── slopanoc.agent [team_manager, coordination]
        ├── slopanoc.model.operation
        │   └── gen_ai.request [M3 physical attempt]
        └── slopanoc.tool [incident_manager, internal_coordination]
            └── slopanoc.agent [incident_manager, unknown or trusted role]
                ├── slopanoc.model.operation
                │   └── gen_ai.request
                ├── slopanoc.tool [teams_get_messages]
                │   └── (Graph/gateway dependency children belong to M5)
                └── slopanoc.agent [incident_manager, remediation]
                    └── slopanoc.tool [knowledge_select_evidence]
```

TAE re-entry/repair spans are siblings under its delegation tool. Fast-path nested
TM presentation is child of the IM execution active when its callback invokes the
TM Runner; attribution restores to IM afterwards. Direct prefetched Teams retrieval
parents under the live phase/tool before synthesis. Never manufacture agent spans
for non-agent helpers. Workload exclusion creates independent ingestion trace even
if called with copied user context.

### Canonical semantics and narrow contract changes

Canonical span names: slopanoc.agent / slopanoc.tool (constant, INTERNAL). Reuse
existing agent.started/completed/failed and tool.started/completed/failed/timeout
stages; do not introduce dynamic names or new lifecycle statuses. COMPLETED, FAILED,
TIMEOUT, CANCELLED retained. Cancellation need not invent agent.cancelled/tool.cancelled
stage; use existing status/error fields. Registered scalar additions proposed:
slopanoc.execution_role = primary/supporting/coordination/system/unknown;
slopanoc.execution_purpose = bounded existing ModelPurpose;
slopanoc.tool_category = internal_coordination/incident_case/governance/teams/utility/
approval/command_action/knowledge/troubleshooting/other;
slopanoc.attempt (positive int, 1 unless actual retry owner supplies real attempt);
slopanoc.duration_ms (finite nonnegative);
slopanoc.result_category (closed inventory classifications);
slopanoc.result_count (nullable bounded nonnegative int);
slopanoc.execution_disposition = executed/not_executed/unknown;
slopanoc.timeout_observed (bool, omit when unknown).
Reuse slopanoc.agent/tool/status/error_code and safe trace-only root correlations;
thread correlation only from an existing trusted thread owner, never arguments.
Contribution category is an independent bounded structured projection (unknown when
unavailable), never agent answer text or telemetry-based authority. Define each
adapter's enumerated values in code/docs, map unregistered names to other/unknown.
Compatible additive schema extensions remain version 1 only if existing consumers
accept them; add tests/document compatibility before deciding. Breaking changes
require an explicit version update; do not silently waive M0 schema tests.

### Governance, approval, errors, timeouts and cancellation

- Classify unexpected specialist exceptions SPECIALIST_FAILED; unexpected tool
  exceptions/known failure envelopes TOOL_ERROR. Scope failures do not duplicate raw
  exception logs/stack traces. One business exception owner remains unchanged.
- Surfaced TimeoutError at tool -> TIMEOUT/TOOL_TIMEOUT; agent timeout -> TIMEOUT,
  preserve existing MODEL_TIMEOUT/TURN_TIMEOUT where reliably exposed. Do not invent
  SPECIALIST_TIMEOUT. Preserve M3 timeout codes on child model requests.
- Gateway G currently converts timeout to run_failure shared with other failures.
  Record generic error with timeout unknown. No error-message parsing, no arbitrary
  new timeout, no new gateway/dependency instrumentation. Honest limitation until M5.
- Cancellation/GeneratorExit -> CANCELLED/TURN_CANCELLED; parent root's existing
  terminal ownership unchanged. Catch for observation, then propagate the ORIGINAL
  BaseException. Async generator close, nested runner cancellation, user Stop and
  stream abandonment must close spans and reset context without double completion.
- Structured expected source_gap/selection_needed/authority or approval rejection
  -> completed invocation with safe policy result/code, OTel status UNSET (not
  infrastructure ERROR). Keep SOURCE_GAP/COMMAND_AUTHORITY_REJECTED/APPROVAL_REJECTED
  when reliable structured business outcomes exist. Invalid/unrecognized result
  stays unknown; do not infer success, authority or operational action success.
- teams_* authorization_error maps APPROVAL_REJECTED at that known write boundary,
  not model/provider auth failure; do not infer detailed denial reason from prose.
- Approval proposal tools project requested/proposed status; execute tools project
  trusted authorization/execution outcome only. approve_proposal/reject_proposal
  are separate trusted API lifecycles; minimal lifecycle events at their service
  boundaries may observe approval.requested/completed when root correlation exists.
  No span kept open waiting for a human across turns; no fabricated approval wait
  duration/history or M7 persistence. Service-call elapsed duration only.
- ProcedureAction resolution summary contains rendered commands, templates,
  parameters and free-text reason despite its diagnostic use. NEVER forward the
  whole summary to OTel. Project only validated enums/counts at existing boundaries.
  No new span around every authority helper; no telemetry value is consumed by gates.
- Sync tools can block cancellation until existing synchronous work returns;
  cancellation is not falsely advertised as interrupting requests.post. M6 owns
  deadlines/threading/reliability redesign, if needed.

### RunTraceRecorder and diagnostic convergence

RunTraceRecorder is a bounded application projection of translated ADK/tool activity
and fixed chat milestones; it emits trace.step with safe labels/counts and
consecutive deduplication. It lacks exhaustive nested lifecycle durations and cannot
be the agent/tool coverage detector. activity_translator maps known capability/tool
names; tools additionally report ActivityKind through activity_queue. Keep these
public SSE/diagnostic behaviors and tests. M4 technical timing comes from canonical
scopes only, not a second RunTrace timing source. No new parallel run registry or
technical OTel spans reconstructed from trace.step. Reuse matching safe outcome/count
projections when possible without replacing presentation semantics. diagnostic_trace
remains domain-owned OBSERVABILITY ONLY — NEVER EVIDENCE; never bulk-export entries.

### Privacy and metrics

Allowlist metadata at production, SDK processor and BOTH Collector configurations.
Do not record exception descriptions, raw argument/result payloads, commands,
knowledge/Teams bodies, incident narratives, user/agent text, attachment bytes,
tokens/credentials or chain-of-thought. Keep ADK and GenAI message capture false
before initialization/execution even when custom tracing is disabled. Do not trust
bounded strings as inherently safe. Safe adapters inspect only closed enum fields
and known sequence lengths; reject booleans-as-counts, NaN/overflow/nested metadata.
Restricted run/turn/session/thread IDs stay trusted trace-only; no metric IDs, URLs,
free-text errors, function IDs or task text. Foreign SDK scopes/content-bearing
native metrics do not become admitted by broadening M4 filters.

Proposed aggregate instruments (counters unit 1; duration histograms unit s):
- slopanoc.agent.executions, slopanoc.agent.failures, slopanoc.agent.duration;
- slopanoc.tool.invocations, slopanoc.tool.failures, slopanoc.tool.timeouts,
  slopanoc.tool.duration.
Agent dimensions exactly environment/agent/status; tool dimensions exactly
 environment/agent/tool/tool_category/status, all fixed registry values. Add
 tool_category explicitly to the canonical key registry. No unbounded tool names or
 identifiers; unknown names map other. Expected policy blocks are not failure
 counts; cancellation separate from infrastructure failures. Metrics are unsampled
 even with traces disabled/sampled; record independently using the existing runtime
 meter/export gate. Validate instrument type/unit/nonnegative finite values and
 histogram buckets; remove exemplars as existing M3 privacy policy requires.

### M5 attachment points (identify only; do not implement)

| Current M4 parent | Future M5 child boundary |
|---|---|
| Teams read/write/hosted/member tool | PowerAutomateClient._call HTTP gateway; actual Graph upstream only where technically visible, not a fabricated direct Graph request |
| knowledge_search / knowledge_select_evidence | Knowledge service/repository SQL/pool/vector/ranking/network boundaries; existing M3 embedding gen_ai spans remain |
| procedure_action_catalog | governed scope/evidence repository reads, storage/network where actually used |
| record_case_analysis | CaseService repository/transaction/pool boundary |
| TAE delegation tool before/between agent messages | progression/clarification/repository operations actually performed by that boundary |
| separate hosted-image API request | source_images.fetch_and_validate_hosted_content and gateway, outside user-turn agent tree |
| model operations | provider transport when M5 adds dependency detail, preserving M3 physical request and usage semantics |

No SQL statement/parameter, gateway secret URL, auth headers, signed GCS URLs or
remote request content is licensed for export by this attachment map.

### Settings/UI impact (five required decisions)

1. Future Overview/Tracing/Diagnostics need agent/tool identity, start/end/status,
   duration, sequence, parent span, safe outcome and errors. M4 creates metadata;
   does not promise cost summary, durable lookup or cross-instance truth.
2. Internal typed contracts support future M7/M8 projections. No persistence,
   API route, active-run database/history, SSE envelope or browser wire change.
3. UI work belongs to M8. Existing chat navigation/layout/progress/trace drawer/
   details/Settings remain compatible; no frontend work planned.
4. Future APIs must enforce server RBAC. Operator/SRE diagnostics and FinOps numeric
   access do not grant Teams/knowledge/case/conversation content access. No role grant.
5. Environment/backend/IaC-owned capture/configuration remains effective read-only.
   No runtime editor, Settings mutation or Terraform bypass.

### Exact expected implementation paths

New:
- backend/observability/agent_instrumentation.py — central agent scope/lifecycle.
- backend/observability/tool_instrumentation.py — central tool scope/result registry.
- backend/observability/adk_adapter.py — pinned telemetry CM adapter/install/rollback.
- backend/observability/execution_metrics.py — finite agent/tool instruments/export gate.
- backend/tests/test_observability_agent_instrumentation.py
- backend/tests/test_observability_tool_instrumentation.py
- backend/tests/test_observability_execution_coverage.py
- backend/tests/test_observability_adk_adapter.py

Existing expected edits:
- backend/observability/model_context.py — canonical execution descriptor adapter.
- backend/observability/turn_trace.py — trusted M4 parent resolver, existing fallback.
- backend/observability/tracing.py — Operation names and scoped lifecycle filtering.
- backend/observability/attributes.py — exact new attribute/dimension registrations.
- backend/observability/redaction.py — bounded scalar policies, no free-text widening.
- backend/observability/metrics.py — new instrument admission and validated points.
- backend/observability/runtime.py — initialization/shutdown install and metrics.
- backend/observability/logging.py — narrow validated agent/tool correlation, if needed.
- backend/observability/schemas.py — typed compatible execution contract.
- backend/agents/team_manager/read_continuation_execution.py — direct read scope only.
- backend/agents/technical_authority_engineer/agent_tool.py — direct governed-search
  scope and existing typed governance outcomes, no specialist refactor.
- backend/api/source_reference.py — direct member call scope around to_thread.
- backend/approval/service.py — safe lifecycle projection only if current root exists.
- backend/tests/test_observability_model_instrumentation.py — agent/tool parentage.
- backend/tests/test_observability_collector.py — positive/adversarial M4 admission.
- backend/tests/test_observability_contract.py — additive contract/compatibility gates.
- infra/observability/collector/config.yaml
- infra/observability/collector/config.local.yaml
- infra/observability/collector/README.md
- backend/observability/README.md
- docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md
- .agent/OBSERVABILITY_EXECUTION.md

Potential minimal callsite for trustworthy route-role binding: backend/api/chat_service.py
or backend/agents/team_manager/operational_routing.py only if the adapter cannot read
existing server route safely. No edits to specialist prompts/callbacks/rosters required
by central ADK interception. No M3 provider rewrite/dependency upgrade/frontend/
Terraform/database migration. Freeze an entry hash snapshot before implementation
because these paths already contain user work. Planning pass changes ONLY this record.

### Validation plan and commands

All validation is local, non-billable and isolated. Reinspect the existing temporary
/private/tmp/slopanoc-m1-test-guard/run_tests.py and sitecustomize.py before use:
clears inherited SLOPANOC configuration, memory-only disposable SQLite, warmup/Vertex
off and deny nonloopback sockets. Fake ADK models/SDK transports/gateway, not ADC or
live inference. Collector executes only config.local.yaml in credential-free minimal
environment; production config validate-only. If guard absent, reconstruct an
 equivalent guarded launcher before testing, never silently use application env.

Focused command after implementation:
```
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_agent_instrumentation.py backend/tests/test_observability_tool_instrumentation.py backend/tests/test_observability_execution_coverage.py backend/tests/test_observability_adk_adapter.py backend/tests/test_observability_contract.py backend/tests/test_observability_policy.py backend/tests/test_observability_settings.py backend/tests/test_observability_runtime.py backend/tests/test_observability_exporters.py backend/tests/test_observability_logging.py backend/tests/test_observability_collector.py backend/tests/test_observability_turn_trace.py backend/tests/test_observability_active_runs.py backend/tests/test_chat_service_root_trace.py backend/tests/test_observability_model_coverage.py backend/tests/test_observability_model_instrumentation.py backend/tests/test_observability_model_provider.py backend/tests/test_observability_model_usage.py
```
Regression command (same guard, isolated test doubles):
```
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_chat_service_streaming.py backend/tests/test_chat_service_function_call_continuation.py backend/tests/test_api_streaming_endpoint.py backend/tests/test_api_run_cancellation.py backend/tests/test_streaming_events.py backend/tests/test_multimodal_agent_tool.py backend/tests/test_multimodal_turn_context.py backend/tests/test_read_continuation.py backend/tests/test_read_continuation_enforcement.py backend/tests/test_read_continuation_regression.py backend/tests/test_read_continuation_trust_boundary.py backend/tests/test_deterministic_continuation.py backend/tests/test_operational_continuation_routing.py backend/tests/test_p5_1j_governed_completion_gate.py backend/tests/test_p5_1j_source_requirements_gate.py backend/tests/test_p5_1j_provenance_compliance.py backend/tests/test_team_manager_technical_authority_boundary.py backend/tests/test_technical_authority_engineer.py backend/tests/test_technical_authority_troubleshooting_repair.py backend/tests/test_tae_structured_output_recovery.py backend/tests/test_tae_negative_selection_completion.py backend/tests/test_tae_provenance_integrity.py backend/tests/test_procedure_action_tae_e2e.py backend/tests/test_procedure_action_grounding_provenance.py backend/tests/test_single_invocation_command_authority.py backend/tests/test_command_egress_boundary.py backend/tests/test_approval_security_contract.py backend/tests/test_api_approval_endpoints.py backend/tests/test_approval_service.py backend/tests/test_approval_policy_gate.py backend/tests/test_approval_canonical.py backend/tests/test_operational_policy_and_execution.py backend/tests/test_operational_control_plane_e2e.py backend/tests/test_acquisition_gap_continuation.py backend/tests/test_gap_recovery_and_continuation.py backend/tests/test_context_assembly.py
```
Re-run the preserved exact M2/M3 regression sets from their completion plans as well;
deduplicate test paths, retain every applicable case. Add all affected backend/tests/
knowledge regressions and image/clarification/gap/progression tests when those
boundaries change. Frontend build/tests conditional only if shared frontend contracts
change; none planned. Compile changed modules with .venv/bin/python -m compileall -q
backend/observability backend/agents backend/api backend/approval; .venv/bin/python
-m pip check; git diff --check. Inspect git status, tracked diff and untracked hashes.

Required fixtures/evidence:
- Real installed ADK Runner/AgentTool and each actual clone/feature flag; synthetic
  fast-path before_model response still records agent invocation without provider.
  Table-driven coverage of every inventory row and AST registry bypass gate.
- M2 root -> agent -> M3 operation/request and tool graphs; direct call scopes;
  callback TM-under-IM synthesis; TAE sibling re-entries/repair; nested IM compliance.
- Exactly one tool span/count per invocation; guard-blocked duplicate delegation
  produces no nested execution; no duplicate underlying read span; paginated/bulk
  images do not become artificial retries. Actual re-entry has separate agent span.
- Raised errors, returned SafeError, empty/partial/unknown results, structured
  invalid output, generic gateway timeout limitation, real surfaced timeout,
  authority/source/approval expected blocks. Uncaught and callback-converted errors
  tested separately; failure envelope cannot silently count as successful operation.
- Real task Stop, nested specialist/tool cancellation and generator aclose/abandon;
  verify closure once, original exception/output and all ContextVar restoration.
- Parallel roots/specialists/tools, same-agent nested execution, to_thread and ADK
  context-copy tool pool, late workers after terminal root: no identity/parent leaks.
- Sentinel arguments/results/user/agent content, command templates/parameters,
  approval text, Teams/knowledge/attachment/credentials: absent from spans/logs/
  metrics/decoded local OTLP and Collector independent adversarial input. Positive
  safe fields and durations must also survive BOTH filters.
- Telemetry start/attach/classify/metric/log/end/export/install failures leave
  exactly one original business execution and unchanged approval/source/authority
  decisions; no retries. Unsupported ADK shape fails coverage gate safely.
- Metrics exact dimensions/type/units and unsampled behavior with tracing disabled,
  sampling zero/exporter failure; unknown names finite other; no ID exemplars.
- Native Collector pinned-version validation of both files and local end-to-end
  decoded OTLP export. No prod Collector execution/export or cloud validation.

Performance command planned after implementation: guarded temporary pytest benchmark
/private/tmp/test_slopanoc_m4_overhead.py with output summary in
/private/tmp/slopanoc-m4-overhead.json (no sensitive payloads). Measure at least 100
iterations after 10 warmups, rotating unwrapped baseline / disabled / enabled-none /
enabled-local modes. Separate empty agent-scope and tool-scope cost from real ADK
runner/function dispatch; include sync/async, nesting, parallelism, cancellation.
Report median/p95/p99 added wall and process CPU, memory retained/peak, queue boundedness
and cleanup. Fake execution delay measured separately and excluded using monotonic
boundaries; parallel waits not summed incorrectly. Flush outside timed boundary.
Initial <10ms synchronous overhead target; <3% production CPU remains unproven until
production/load qualification. No production performance claim from local fixtures.

### Objective M4 exit criteria (all NOT RUN until implementation)

| Exit criterion | Required passing evidence / current status |
|---|---|
| All real agent executions visible | Each agent inventory row driven through installed ADK; feature on/off, clones, synthetic fast-path and all re-entries; NOT RUN |
| All actual tool invocation layers visible | Every 21-name roster row plus direct paths driven, no helpers falsely presented as tools; NOT RUN |
| Canonical bounded span/data semantics | Constant names, typed enums, narrow Python/Collector contracts and compatibility test; NOT RUN |
| Correct M2 parentage | Same trusted root and live nearest phase/agent/tool, terminal/foreign/copied context denied; NOT RUN |
| Correct M3 relationship | Actual model operation/request child graphs, unchanged attribution/usage/TTFT/retries; NOT RUN |
| Nested specialists and context cleanup | IM/TM synthesis, compliance, TAE repair/re-entry, disposable sessions and threads; NOT RUN |
| Tool uniqueness and honest attempts | Exactly one span/invocation, no body/helper duplicate, genuine retries only; NOT RUN |
| Errors/timeouts/cancellation | Exceptions and returned failures classified, original business semantics, generic timeout stays unknown; NOT RUN |
| Governance preserved | Policy outcomes distinct from infrastructure failure, authority/approval/provenance regressions; NOT RUN |
| Metadata-only privacy | Positive/negative sentinel and independent Collector tests, no content/exception payloads; NOT RUN |
| Bounded aggregate metrics | Exact finite labels, counts/durations/type/unit/exemplar validation, unsampled tests; NOT RUN |
| Concurrency isolation | Parallel/nested/thread-copied/late workers with trusted root and context cleanup; NOT RUN |
| Telemetry failure isolation | Broken scope/adapter/export leaves original invocation/result/exception, zero telemetry retries; NOT RUN |
| Imports/build and regression | Focused M0–M4 plus preserved required regression suites; no passing skips; NOT RUN |
| Local overhead measured | Agent/tool isolated and real ADK overhead, bounded queue/memory/context, honest limitations; NOT RUN |
| No M5 implementation or remote mutation | Scoped hashes/diff plus commands audit; future attachment points only; NOT RUN |

### Planning validation performed, limitations and readiness

Planning-only baseline command: the focused command above restricted to the 12
existing files contract/policy/settings/runtime/logging/turn_trace/active_runs/
chat_service_root_trace/model_coverage/model_instrumentation/model_provider/model_usage.
Guard and network blocker inspected before execution. Result: **267 passed,
3 dependency/deprecation warnings, 8.09s**. No M4 tests exist/run; no claim that
all M3 regression/Collector tests were rerun today. Prior M3 recorded broader
validation remains evidence. Static source/installed SDK boundary inspection and
working-tree review completed. git diff --check: PASS; final git status/untracked
review: PASS (existing work preserved); new M4 record whitespace check: PASS.

Risks to test during implementation: private ADK adapter drift; TelemetryContext
compatibility; async-generator yield/task ownership; paired callbacks failing to
close on exceptional paths; foreign ADK spans displacing exported parents; M3
phase-only parent resolver; sync tool cancellation latency; ambiguous safe errors;
feature-flag/clone/direct-call bypasses; policy short circuits counted as execution;
returned failure without exception; unsupported contribution/primary/supporting
semantics. All have bounded compatible plans; no current planning blocker.
Known honest limitations: primary/supporting unknown except trusted server route;
provider/gateway collapsed timeouts unknown; no durable current-agent/tool support
query, API, UI, ledger or production performance/live cloud proof in M4.

Actual planning changes: ONLY .agent/OBSERVABILITY_EXECUTION.md. No M4 runtime
implementation, module/test/filter/config edit or new architecture behavior performed.
Implementation exit criteria: NOT RUN. M4 implementation status: NOT IMPLEMENTED.
M4 local validation status: NOT RUN (foundation baseline above is separate).
Cloud deployment status: **NOT EXECUTED — no M4 remote operation required**.
Pending M4 cloud operations: NONE. Inherited M1 operations remain **NOT EXECUTED —
USER APPROVAL REQUIRED**, exactly as recorded earlier; this plan grants no approval.
No GCP/Cloud Run/Cloud SQL/BigQuery/IAM/billing/GCS/Microsoft Graph/Terraform state/
shared DB/external-service mutation or live billable model invocation occurred.

**Ready To Implement M4: YES** (planning complete; separate implementation request
required). **Ready for Next Milestone: NO.** M5 BLOCKED until every M4 applicable
implementation/validation exit passes. **STOP: planning only.**


## M4 implementation entry — 2026-10-08

Approved implementation request received; M4 ONLY. Existing M4 objective, specs,
inventories, reuse, expected paths, invariants, commands and exit gates above apply.
Entry SHA256 snapshot: /private/tmp/slopanoc-m4-entry.json. M5 remains blocked.
New explicit constraint supersedes planning adapter: NO global ADK monkeypatch.
Use ObservedAgent subclass and isolated per-flow function bindings (original pinned
ADK bytecode/dispatch with private telemetry context-manager facade). No installed
package edits, global dispatch changes or callback semantics changes. Expected
additional files: all five agent definition modules and ingestion image interpreter
(import ObservedAgent as Agent); clone semantics preserved. Compatibility failure
degrades/uses unmodified flow, never prevents execution; coverage must FAIL then.
No remote/cloud operations required or authorized.


## M4 completion — 2026-10-08

Status: **IMPLEMENTED AND LOCALLY VALIDATED**. All blocking local exit gates PASS.
This completion supersedes the historical planning-only status and proposed global
telemetry replacement above. The approved implementation request explicitly forbids
any global ADK monkeypatch: the final implementation uses a subclass and isolated
per-flow bindings. **M5 NOT STARTED. STOP after M4.** No deployment is required to
satisfy the approved local M4 gate; inherited cloud validation remains unexecuted.

### Final architecture and runtime compatibility

- `agent_instrumentation.Execution` owns the single agent/tool semantic scope,
  start/end, bounded metadata, classification and metric observation. It reuses
  M3 Attribution via an execution handle and M2 trusted TurnTrace; no second run
  registry or business diagnostic truth was created.
- `ObservedAgent.run_async` wraps each actual invocation exactly once, including
  before/after agent callbacks, and attaches context only while driving the original
  iterator's next/close. Consumer context restores at every yield. Clones/copies
  keep the class; all six definition imports use this local subclass.
- `adk_adapter` verifies google-adk **1.33.0**, four SHA256 source fingerprints
  (constants in that module) and TelemetryContext.function_response_event. Private
  surfaces are BaseLlmFlow._postprocess_handle_function_calls_async and functions.
  handle_function_calls_async / handle_function_call_list_async /
  _execute_single_function_call_async; the local body facade delegates the original
  functions.__call_tool_async. Original bytecode runs against COPIED globals and
  fresh flow-instance binding. There is no installed package edit or assignment to
  an ADK module/class/global dispatch. Ordinary ADK Agent remains unchanged.
- Only the local telemetry context manager and execution observer differ. ADK still
  owns scheduling, arguments, before/error/after callback ordering, return-value
  construction and retries. No user callback was replaced/reordered by telemetry.
- Shape failure uses the original flow, emits safe degraded health and preserves
  execution. It is explicitly **M4 COVERAGE FAIL** on that unsupported SDK; the
  negative compatibility test asserts this behavior, not a passing coverage skip.
  M3 google-genai **1.75.0** physical adapter remains unchanged. Model instrumentation
  only adds stale-scope-aware attribution and live-agent parent resolution for
  standalone/ingestion calls, preserving provider/usage behavior.
- M3 model_parent first recognizes the current trusted live Execution; M2 phase/root
  fallback remains. Copied descriptors after closure/terminality do not retain
  identity, including structured log trace correlation.
- Direct adapters surround only deterministic Teams prefetch, server-governed TAE
  search and authoritative member retrieval. Shared tool callables are not wrapped
  again, preventing central/direct duplication.
- Approval create/approve/reject service functions retain one original call and
  emit safe requested/completed events afterward. Duration is the service call,
  never a fabricated human-wait span. ProcedureAction/Command Authority observations
  are at their existing actual resolution/decision boundaries; none feed policy.

### Final agent and tool inventory

All agent inventory rows from the approved plan remain current: Team Manager,
Incident Manager, Technical Authority Engineer, Problem Manager and Automated
Operations Engineer plus system image interpreter. Presentation, source-declaration,
fast-path, continuation, synthesis, compliance/remediation, tool-free finalization/
action-reselection and feature-gated variants preserve the subclass. Runner re-entry
is a separate execution; pre-Runner deterministic failure creates no agent span.
Team Manager is coordination; ingestion is system; only server-owned forced routing
marks primary. Supporting/other specialist classification stays unknown where the
current application has no reliable ownership field. No classification from text.

The original 21 tools remain covered. One additional actual tool was discovered:
**set_model_response**, injected by ADK when output_schema and tools coexist. It
updates model output state, so it is included in side-effect safety validation.
Final 22-name registry (authoritative executable counterpart: tool_instrumentation.TOOLS):

`incident_manager`, `technical_authority_engineer`, `problem_manager`, `automated_operations_engineer`, `record_case_analysis`, `record_conversation_target`, `record_source_requirements`, `teams_list_chats`, `teams_get_messages`, `get_resolved_chat_messages`, `teams_get_hosted_content`, `teams_get_all_hosted_content`, `teams_get_members`, `get_current_time_context`, `teams_propose_create_chat`, `teams_propose_send_message`, `teams_create_chat`, `teams_send_message`, `knowledge_search`, `knowledge_select_evidence`, `procedure_action_catalog`, `set_model_response`.

The existing tool inventory category/owner/direct-call rows remain applicable.
set_model_response is internal_coordination, owned by the invoking canonical agent,
state-changing, ADK FunctionTool dispatch, no external dependency and no raw output
export. Registered side-effecting/state-mutating names: record_case_analysis,
record_conversation_target, record_source_requirements, teams_propose_create_chat,
teams_propose_send_message, teams_create_chat, teams_send_message,
knowledge_select_evidence, procedure_action_catalog, technical_authority_engineer,
incident_manager and set_model_response. Tools already doing dependency retries
retain one logical invocation/attempt=1; internal transport attempt detail is M5.

### Trace hierarchy and tested evidence

```text
slopanoc.turn
└── live orchestration/finalization phase where present
    └── slopanoc.agent team_manager
        ├── slopanoc.model.operation
        │   └── gen_ai.request
        └── slopanoc.tool <specialist delegation>
            └── slopanoc.agent <specialist>
                ├── slopanoc.model.operation
                │   └── gen_ai.request
                └── slopanoc.tool <logical tool>
```

No helper/callback/dependency spans. Direct server tools outside agents attach to
M2 phase/root without a fake agent. Tests inspect exported native span IDs/parents,
not a documentation-only diagram. Actual nested AgentTool tests cover each of
IM/TAE/PM/AOE, three M3 provider observations per graph, unchanged trusted run identity
and owner-specific model parents. All configured variants execute through real ADK
with synthetic models; business validation hooks are removed only in those pure
coverage fixtures, separately exercised by unchanged governance regressions.
Actual direct-call fixtures exercise members, TAE search and failed Teams prefetch:
exactly three tool spans and zero agent spans. The roster/AST gate rejects new
unadapted agent imports, unregistered tools and additional direct callsites.

Real installed ADK parallel function calls, synchronous FunctionTool execution,
thread copies and multiple root traces retain independent tool/agent context.
Nested cancellation closes parent agent, delegation tool and child agent as
CANCELLED once and propagates CancelledError. Error/timeout/GeneratorExit and stream
abandonment tests verify closure, canonical codes and restored context. Returned
source gap, authority rejection, approval rejection and empty governed catalog
are completed policy outcomes, not infrastructure failures. Generic gateway
run_failure cannot establish timeout: result remains error, timeout unknown.

Telemetry start/attach/result/metric/finalize failure is injected for EVERY registered
side-effecting tool, with fake invocation counters proving exactly one business
call and unchanged final response. ADK error callbacks may convert a genuine
exception into a safe result; the tool trace still records the real failure.
Unsupported adapter shape preserves execution without claiming instrumented coverage.
Existing RunTraceRecorder/PerfTimer are preserved, as is knowledge diagnostic_trace:
**OBSERVABILITY ONLY — NEVER EVIDENCE**. No authority or evidence selection uses
telemetry. Approval/command/provenance regressions pass unchanged.

### Privacy, metrics and local Collector

Python and both Collector configurations admit only the two constant span names,
closed lifecycle/governance events and validated M4 scalar metadata. No arbitrary
agent/tool attributes or gen_ai wildcard. Sentinels cover prompts/answers, tool
arguments/results, Teams/knowledge bodies, commands, approval text, errors and
secrets. Captured spans/events/structured logs/metrics and decoded local Collector
output contain no sentinel. Independent malicious OTLP bypass tests exercise
Collector denial of raw arguments and unregistered result_category values without
trusting Python. Positive tests retain safe tool identity/count/status and hierarchy.

Counters: slopanoc.agent.executions/failures, slopanoc.tool.invocations/failures/timeouts.
Histograms: slopanoc.agent.duration, slopanoc.tool.duration. Units 1/s respectively;
agent labels exactly environment/agent/status, tool labels add tool/tool_category.
Values come from closed registries; IDs, release versions and content never become
labels. Numeric shape validation and exemplar removal reuse M3 gates. ALWAYS_OFF
trace tests still record execution metrics; sampling is not accounting sampling.
No usage ledger or cost functionality was added. Both native Collector configurations
validate at contrib **0.160.0** and local SDK → Collector → decoded sink passes.
No claim that Google-built Collector ran on Cloud Run.

### Actual files changed (M4 delta only)

Existing local work is preserved. Entry hashes distinguish this M4 delta from the
aggregate git diff/untracked M0–M3 source. No requirements or Terraform file changed.

- `.agent/OBSERVABILITY_EXECUTION.md`
- `docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md`
- `backend/observability/README.md`
- `infra/observability/collector/README.md`
- `backend/agents/automated_operations_engineer/agent.py`
- `backend/agents/incident_manager/agent.py`
- `backend/agents/problem_manager/agent.py`
- `backend/agents/team_manager/agent.py`
- `backend/agents/team_manager/operational_routing.py`
- `backend/agents/team_manager/read_continuation_execution.py`
- `backend/agents/technical_authority_engineer/agent.py`
- `backend/agents/technical_authority_engineer/agent_tool.py`
- `backend/api/source_reference.py`
- `backend/approval/service.py`
- `backend/knowledge_ingestion/gemini_image_interpreter.py`
- `backend/observability/attributes.py`
- `backend/observability/metrics.py`
- `backend/observability/model_context.py`
- `backend/observability/model_instrumentation.py`
- `backend/observability/redaction.py`
- `backend/observability/runtime.py`
- `backend/observability/tracing.py`
- `backend/observability/turn_trace.py`
- `backend/tests/test_observability_collector.py`
- `backend/tests/test_read_continuation_trust_boundary.py`
- `infra/observability/collector/config.local.yaml`
- `infra/observability/collector/config.yaml`
- `backend/observability/adk_adapter.py`
- `backend/observability/agent_instrumentation.py`
- `backend/observability/tool_instrumentation.py`
- `backend/observability/execution_metrics.py`
- `backend/tests/test_observability_adk_adapter.py`
- `backend/tests/test_observability_agent_instrumentation.py`
- `backend/tests/test_observability_tool_instrumentation.py`
- `backend/tests/test_observability_execution_coverage.py`

### Validation commands, results, failures and repairs

Local guard clears inherited SLOPANOC settings, sets isolated SQLite/in-memory
sessions/knowledge and disables warmup/Vertex. Its sitecustomize denies nonloopback
socket connections. Fake models, provider transports and gateways were used; no
live/billable model calls, ADC, shared DB or remote exporter. Sandbox escalation
was used only for loopback Collector bind permissions, approved automatically;
it was not a request/approval for cloud mutation.

Final broad command expands the exact 155-file manifest below through the same
M1 guard. Final results after parentage repair: **2,717 passed, 479 dependency/deprecation warnings, 75.43s; zero
failures/skips**. Covers M0–M4, all previously required M2/M3 suites, applicable
knowledge/governance/authority/approval/command/continuation/SSE/cancellation/rewind
regressions and Collector tests.

```bash
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/knowledge/test_applicability_evaluation.py backend/tests/knowledge/test_applicability_normalization.py backend/tests/knowledge/test_applicability_separation.py backend/tests/knowledge/test_contracts.py backend/tests/knowledge/test_dependency_boundary.py backend/tests/knowledge/test_domain_artifacts.py backend/tests/knowledge/test_enums.py backend/tests/knowledge/test_governance_effectiveness.py backend/tests/knowledge/test_governance_lifecycle.py backend/tests/knowledge/test_governance_materialization.py backend/tests/knowledge/test_governance_provenance_artifacts.py backend/tests/knowledge/test_governance_separation.py backend/tests/knowledge/test_governance_versioning.py backend/tests/knowledge/test_governance_versioning_archived_activation.py backend/tests/knowledge/test_hybrid_governed_retrieval.py backend/tests/knowledge/test_hybrid_retrieval.py backend/tests/knowledge/test_ingestion_adapter_contract.py backend/tests/knowledge/test_ingestion_boundary_invariants.py backend/tests/knowledge/test_ingestion_contracts.py backend/tests/knowledge/test_ingestion_dedup_lineage.py backend/tests/knowledge/test_ingestion_emf_rasterization.py backend/tests/knowledge/test_ingestion_extraction_primitives.py backend/tests/knowledge/test_ingestion_extractors.py backend/tests/knowledge/test_ingestion_extractors_ole.py backend/tests/knowledge/test_ingestion_image_interpretation.py backend/tests/knowledge/test_ingestion_source_agnostic.py backend/tests/knowledge/test_metadata_hardening.py backend/tests/knowledge/test_models.py backend/tests/knowledge/test_processing_compound.py backend/tests/knowledge/test_processing_contracts.py backend/tests/knowledge/test_processing_reference_processor.py backend/tests/knowledge/test_provenance_boundaries.py backend/tests/knowledge/test_provenance_contracts.py backend/tests/knowledge/test_provenance_selection.py backend/tests/knowledge/test_provenance_service.py backend/tests/knowledge/test_repository_boundaries.py backend/tests/knowledge/test_repository_corruption.py backend/tests/knowledge/test_repository_crud.py backend/tests/knowledge/test_repository_list_all.py backend/tests/knowledge/test_repository_persistence_and_constraints.py backend/tests/knowledge/test_repository_roundtrip_and_genericity.py backend/tests/knowledge/test_repository_version_family_and_governance.py backend/tests/knowledge/test_retrieval_applicability.py backend/tests/knowledge/test_retrieval_boundaries.py backend/tests/knowledge/test_retrieval_contracts.py backend/tests/knowledge/test_retrieval_currentness.py backend/tests/knowledge/test_retrieval_ranking.py backend/tests/knowledge/test_retrieval_scoring.py backend/tests/knowledge/test_retrieval_source_precedence.py backend/tests/knowledge/test_serialization.py backend/tests/knowledge/test_tools_boundaries.py backend/tests/knowledge/test_tools_contracts.py backend/tests/knowledge/test_tools_service.py backend/tests/knowledge/test_vector_embeddings.py backend/tests/test_acquisition_gap_continuation.py backend/tests/test_ambiguous_selection_read_resume_regression.py backend/tests/test_api_app.py backend/tests/test_api_approval_endpoints.py backend/tests/test_api_chat_service.py backend/tests/test_api_ownership.py backend/tests/test_api_run_cancellation.py backend/tests/test_api_streaming_endpoint.py backend/tests/test_applicability_blocked_governed_action.py backend/tests/test_applicability_clarification_outcome_independence.py backend/tests/test_approval_canonical.py backend/tests/test_approval_policy_gate.py backend/tests/test_approval_security_contract.py backend/tests/test_approval_service.py backend/tests/test_case_progression_persistence.py backend/tests/test_chat_service_function_call_continuation.py backend/tests/test_chat_service_hardening.py backend/tests/test_chat_service_performance.py backend/tests/test_chat_service_rewind.py backend/tests/test_chat_service_root_trace.py backend/tests/test_chat_service_streaming.py backend/tests/test_chat_service_turn_context_lifecycle.py backend/tests/test_clarification_continuity.py backend/tests/test_clarification_retrieval_resumption.py backend/tests/test_clarification_service.py backend/tests/test_command_egress_boundary.py backend/tests/test_context_assembly.py backend/tests/test_d2_merge_adk_activity_events_context_lifecycle.py backend/tests/test_deterministic_continuation.py backend/tests/test_final_answer_persistence_and_consistency.py backend/tests/test_gap_recovery_and_continuation.py backend/tests/test_governed_continuity_runtime_blockers.py backend/tests/test_governed_recovery_action_extraction.py backend/tests/test_model_warmup.py backend/tests/test_multimodal_agent_tool.py backend/tests/test_multimodal_turn_context.py backend/tests/test_observability_active_runs.py backend/tests/test_observability_adk_adapter.py backend/tests/test_observability_agent_instrumentation.py backend/tests/test_observability_collector.py backend/tests/test_observability_contract.py backend/tests/test_observability_execution_coverage.py backend/tests/test_observability_exporters.py backend/tests/test_observability_logging.py backend/tests/test_observability_model_coverage.py backend/tests/test_observability_model_instrumentation.py backend/tests/test_observability_model_provider.py backend/tests/test_observability_model_usage.py backend/tests/test_observability_policy.py backend/tests/test_observability_runtime.py backend/tests/test_observability_settings.py backend/tests/test_observability_tool_instrumentation.py backend/tests/test_observability_turn_trace.py backend/tests/test_operational_continuation_routing.py backend/tests/test_operational_control_plane_e2e.py backend/tests/test_operational_policy_and_execution.py backend/tests/test_p2_activity_truthfulness.py backend/tests/test_p2_model_call_correlation.py backend/tests/test_p4b2_synthesis_compression.py backend/tests/test_p4b3_source_provenance.py backend/tests/test_p5_1_b6_fast_path_image_gate.py backend/tests/test_p5_1_b6_image_teams_km_integration.py backend/tests/test_p5_1_b6_selection_continuation_images.py backend/tests/test_p5_1_b7_governed_completion_image_evidence.py backend/tests/test_p5_1_b7_km_provenance_exact_duplicate.py backend/tests/test_p5_1j_governed_completion_gate.py backend/tests/test_p5_1j_provenance_compliance.py backend/tests/test_p5_1j_source_requirements_gate.py backend/tests/test_perf_timing.py backend/tests/test_procedure_action_grounding_provenance.py backend/tests/test_procedure_action_tae_e2e.py backend/tests/test_procedure_actions.py backend/tests/test_procedure_semantics_and_scope.py backend/tests/test_progression_architecture_e2e.py backend/tests/test_progression_controller.py backend/tests/test_progression_duplicate_prevention.py backend/tests/test_progression_resolution_gates.py backend/tests/test_read_command_family_classification.py backend/tests/test_read_continuation.py backend/tests/test_read_continuation_enforcement.py backend/tests/test_read_continuation_regression.py backend/tests/test_read_continuation_trust_boundary.py backend/tests/test_run_trace.py backend/tests/test_runtime_database_policy.py backend/tests/test_settings_knowledge_database_url.py backend/tests/test_settings_secret_caching.py backend/tests/test_single_invocation_command_authority.py backend/tests/test_startup_import_graph.py backend/tests/test_state_change_target_authority.py backend/tests/test_streaming_events.py backend/tests/test_structured_progression_policy.py backend/tests/test_tae_negative_selection_completion.py backend/tests/test_tae_provenance_integrity.py backend/tests/test_tae_structured_output_recovery.py backend/tests/test_team_manager_case_context_provider.py backend/tests/test_team_manager_technical_authority_boundary.py backend/tests/test_teams_image_retrieval_repair.py backend/tests/test_technical_authority_engineer.py backend/tests/test_technical_authority_troubleshooting_repair.py backend/tests/test_troubleshooting_fault_threads.py backend/tests/test_troubleshooting_progression.py
```

After that run, added two focused runtime evidence tests (no source behavior change):
real ADK parallel+sync tool dispatch and nested specialist/tool cancellation with
trusted primary metadata. Final focused command:

```bash
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_tool_instrumentation.py backend/tests/test_observability_agent_instrumentation.py
```

Result before parentage repair: **112 passed, 6 dependency/deprecation warnings,
5.14s**. Final agent/tool rerun includes standalone/ingestion parentage and actual
ADK-injected set_model_response preservation: **115 passed, 6 warnings, 5.49s**.
The injected-response test was added after broad collection, so its pass is in the
final focused run; broad count remains 2,717. Counts are not additive.
Earlier focused M4/contract run: 117 passed; earlier expanded agent/tool/adapter run:
114 passed. Final broad run supersedes prior intermediate failures:
- M0 dependency-direction test detected a telemetry import of application routing;
  removed that dependency and pushed trusted primary metadata from the existing
  application-owned route callback. Canonical contracts remain application-independent.
- Policy-result fixture used the wrong tool for actions[]; corrected to real catalog.
- Collector fixture accidentally shadowed emit and assumed two-member OTLP receiver
  tuples; removed local import and accessed the actual recorded tuple shape.
- Malicious OTLP fixture duplicated protobuf map keys, making even safe fields
  undecodable; now replaces the existing result_category value to test valid malicious
  input rather than invalid protobuf. Denial and safe-field survival both pass.
- Continuation trust-boundary test lacked a fake deterministic prefetch gateway.
  It passed alone with absent configuration but inherited fake URL in broader order.
  Added the existing fake gateway to that test; nonloopback guard remains enforced.
  This is test isolation only; no business retrieval/routing behavior changed.
- Final review found standalone/background and ingestion model/tool spans did not
  use active agent parents when no trusted user turn existed. Repaired matching
  workload/turn parent inheritance and inherited tool workload; ingestion still
  detaches from copied user correlation. Added real provider+tool graph assertions
  for standalone IM and ingestion invoked under an unrelated user root. Affected
  agent/tool/M3/contract rerun: **178 passed, 8 warnings, 6.65s**. Broad regression
  and overhead benchmark rerun after the repair, results below.

Additional validation: compileall of backend/observability, agents, api and approval
PASS; pip check PASS; git diff --check PASS; final git status, diff --stat, full diff
and untracked-file review PASS. No shared frontend contract changed, so frontend
build/tests are not applicable to M4. No local migration or Terraform operation needed.

### Performance measurement (local only)

Command: `.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py
/private/tmp/test_slopanoc_m4_overhead.py`. Final result **1 passed, 1 warning, 3.15s**.
Measurement output: /private/tmp/slopanoc-m4-overhead.json (metadata only).
100 measured samples/scenario/mode after 10 warmups, rotating mode order; vanilla
ADK baseline, disabled M4, enabled without exporter, enabled local exporter.
Real synthetic ADK dispatch plus isolated scopes, parallel tasks and cancellation.
Measured fake await excluded, process CPU measured separately, flush outside timer.

| Scenario | Raw median / p95 ms | Disabled median / p95 ms | Enabled no-export median / p95 ms | Enabled local median / p95 ms | Paired local-minus-disabled median / p95 / p99 ms |
|---|---|---|---|---|---|
| agent_scope | 0.001 / 0.001 | 0.044 / 0.045 | 0.083 / 0.089 | 0.237 / 0.248 | 0.194 / 0.203 / 0.244 |
| tool_scope | 0.001 / 0.001 | 0.049 / 0.050 | 0.094 / 0.096 | 0.249 / 0.256 | 0.200 / 0.207 / 0.230 |
| nested_scope | 0.001 / 0.001 | 0.092 / 0.094 | 0.174 / 0.188 | 0.489 / 0.529 | 0.397 / 0.433 / 0.903 |
| parallel_scope | 0.065 / 0.076 | 0.244 / 0.274 | 0.411 / 0.464 | 1.044 / 1.186 | 0.799 / 0.942 / 1.125 |
| cancel_scope | 0.064 / 0.077 | 0.160 / 0.181 | 0.245 / 0.344 | 0.563 / 0.660 | 0.402 / 0.505 / 0.742 |
| adk_dispatch | 1.473 / 1.945 | 1.722 / 2.310 | 1.845 / 2.620 | 2.237 / 2.685 | 0.496 / 0.820 / 1.477 |

Real ADK dispatch process-CPU median: raw 1.465ms, disabled 1.715ms, none 1.829ms, local 2.227ms.
tracemalloc after 100 unsampled nested calls retained 120 bytes, peak delta
35,648 bytes. Trace/log queues both zero after bounded flush; context restored per
iteration. Every measured paired p95 below initial 10ms target. These microbenchmarks
do not prove production latency or the <3% production CPU objective; production/load
qualification remains a later gate.

### UI / Settings impact and M5 attachment points

1. M4 creates safe agent/tool sequence, duration, result and error metadata required
   by future Settings → Observability & FinOps → Overview/Tracing/Diagnostics.
2. Native context/descriptor is sufficient internally; protected summary/API contracts
   and durable current-agent/current-tool query belong to M7, not this milestone.
3. UI belongs to M8; no Settings, normal-chat progress, SSE or frontend changes.
4. Future APIs enforce server RBAC. Safe metadata does not grant FinOps access to
   conversation, Teams, knowledge or sensitive case bodies. M4 creates no new route.
5. Tracing/export/configuration remains backend/environment/IaC-owned and must be
   presented effective read-only later. No runtime settings mutation was added.

M5 can attach children under the active tool's native OTel context or trusted
active_execution descriptor. Existing planned attachment sites remain: gateway
PowerAutomateClient._call (honest HTTP gateway, not fabricated direct Graph),
knowledge retrieval repositories/vector clients, approval/case/knowledge storage,
GCS hosted images/attachments and actual external transports. **No dependency
instrumentation implemented.** Separate system ingestion stays separate from user
turns. No M5 tests/implementation, M7 persistence/API, M8 UI or M10 ledger begun.

### Individually evaluated blocking M4 exit criteria

| Exit criterion | Result | Evidence |
|---|---|---|
| Every real agent execution path instrumented | PASS | Configured base/clone/factory/feature variants and AST gate; actual ADK invocations |
| Every known logical tool path instrumented | PASS | 22-name roster/registry dispatch and actual direct-call adapters |
| Exactly one logical agent span per execution | PASS | Real Runner counts, re-entry/nested/short-circuit/abandonment |
| Exactly one logical tool span per invocation | PASS | Table-driven 22 tools, real delegation and parallel/sync dispatch |
| No duplicate direct-call instrumentation | PASS | Actual direct three-span fixture; no shared callable double-wrap |
| Nested specialist context correct | PASS | Nested IM/TAE/PM/AOE graphs, remediation and cleanup |
| M3 model spans parent correctly | PASS | Provider-child native ID graph assertions and unchanged M3 suites |
| M2 root parentage correct | PASS | Trusted-root/native-parent assertions, ingestion independent, stale descriptor denial |
| Governance outcomes remain correct | PASS | Policy-result cases, real service observations, authority/approval/provenance regressions |
| Side-effect tools never replayed by telemetry | PASS | Every registered side-effecting name × five telemetry failures, invocation counters |
| Errors/cancellation/timeouts honestly classified | PASS | Direct and actual ADK exceptions/recovered exceptions/nested cancellation/GeneratorExit; ambiguous timeout unknown |
| Privacy validation | PASS | Python captures and independent malformed OTLP + positive Collector projections |
| Bounded metrics | PASS | Finite dimensions/numeric units/exemplar denial and unsampled tests |
| Concurrency isolation | PASS | Multiple roots, nested/disposable sessions, actual parallel tools, thread copies and late workers |
| Telemetry failure isolation | PASS | Scope/adapter/metric/finalizer failures, unchanged results/no retries |
| Traces readable | PASS | Semantic native graph only; no helpers/ADK internal export |
| Applicable regressions pass | PASS | 2,717 final broad passes plus 115 final focused passes; imports/pip/hygiene |
| M5 dependencies remain unimplemented | PASS | Entry hash review and scoped diff; attachment descriptions only |
| No cloud mutation occurred | PASS | Local guarded commands only; no remote operations |

Additional approved-plan gates: canonical bounded semantics PASS (Python/Collector
projection and versioned compatible doc); local overhead measurement PASS (table
above; production CPU explicitly unproven). Every applicable M4 roadmap exit is
covered; no silent criterion waiver or skip.

### Known limitations, cloud status and readiness

Private adapter intentionally supports the exact reviewed ADK shape; upgrades need
new fingerprints/tests before coverage can pass. Primary/supporting unavailable
ownership stays unknown. Generic gateway run_failure cannot reveal a timeout; sync
business requests retain existing cancellation latency. No durable active-agent/tool
records, authorized query APIs, UI, dependency children or production performance
proof in M4. Legacy diagnostic systems retain their existing scope; broad convergence
is deferred. The local upstream Collector is not evidence of Google-image deployment.

Local implementation: COMPLETE. Local validation: PASS.
Cloud deployment status: **NOT EXECUTED — USER APPROVAL REQUIRED** for any future
production rollout, including these source-only Collector changes. Exact pending
M4 cloud operations: **NONE (M4 requires no remote operation)**. Inherited M1 cloud
operations remain pending exactly as recorded earlier, with no new authorization.
No GCP/Cloud Run/Cloud SQL/BigQuery/IAM/billing/GCS/Microsoft Graph/Terraform state/
shared DB/external-service mutation occurred. No credentials/content committed.

**Ready for Next Milestone: YES. M5 NOT STARTED. STOP after M4.**


## M5 — Dependency + Infrastructure Instrumentation: planning only — 2026-10-08

**M5 — PLANNED — NOT IMPLEMENTED.** User authorization is planning only.
No M5 source/config/test implementation, dependency execution, deployment, migration,
cloud inspection or external mutation is authorized by this plan. M6 remains blocked.
Historical plans/completion reports above are retained; this section is current.

### Prerequisite gate and current-state audit

**Planning gate PASS.** M0 VALIDATED COMPLETE; M1–M4 IMPLEMENTED AND LOCALLY
VALIDATED with all applicable blocking local criteria PASS; M4 validation PASS and
Ready for Next Milestone YES. These are the established local COMPLETE gates,
not claims of production deployment. M1 live deployment is still unexecuted;
doc 02 explicitly assigns durable active status to M7 and accounting to M10 under
the previously approved scope. Neither is silently reassigned to M5.

Evidence inspected: completion sections for M0–M4, the current milestone matrix,
actual inert contracts/runtime/export queues, root/phase/active cache, model physical
attempt adapter/usage observations, semantic agent/tool scopes, production lifecycle
wiring and Collector source. M4 records 2,717 broad passes and 115 final focused
passes (overlapping, not additive), imports/pip/hygiene PASS. Historical tests were
not rerun in this planning pass; their recorded results are not new M5 validation.

Working tree already has 26 modified tracked files and substantial untracked
M0–M4/Telemetry/infra work. Preserve all of it. Baseline SHA256 inventory of 799
tracked/untracked nonignored files captured outside the repo before planning edits.
Only this execution record may change during this pass.

### Objective, authoritative specifications and inspected architecture

Objective: identify each real external wait and its safe dependency child beneath
M4's logical tool/agent or the legitimate M2 phase; expose latency, outcome,
existing retries/timeouts/rate limits and connection saturation without changing
execution, governance, persistence or provider accounting.

Read: AGENTS.md; .agent/PLANS.md; this execution record; docs/Telemetry/README.md;
00_MASTER_BUILD_CONTRACT.md; 01_TARGET_ARCHITECTURE.md; 02_TELEMETRY_DATA_CONTRACT.md;
03_INSTRUMENTATION_AND_RUNTIME.md; 04_RELIABILITY_TIMEOUTS_ERRORS.md;
11_SLOPANOC_INTEGRATION_MAP.md; 12_IMPLEMENTATION_ROADMAP.md;
13_TEST_VALIDATION_ACCEPTANCE.md; 14_CODEX_EXECUTION_GUIDE.md;
15_DEFINITION_OF_DONE.md; 16_PRODUCTION_TELEMETRY_BACKEND.md;
17_INFRASTRUCTURE_OBSERVABILITY.md; 18_STRUCTURED_LOGGING_STANDARD.md;
22_RELEASE_CONFIG_CORRELATION_CI.md; 23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md.
Also read 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md for dependency privacy.

Inspected actual owners: backend/gateway/power_automate_client.py; Teams message
pagination/chat resolution/hosted content/member/write paths; api/session_service.py;
cases/db.py and service/progression consumers; attachments/repository.py, storage.py
and api/attachment_service.py; knowledge/repository/sqlalchemy.py and compatibility
sqlite.py; knowledge/retrieval/service.py/scoring contracts; tools/knowledge/runtime.py,
dense_similarity.py and diagnostic_trace.py; knowledge/embeddings/service.py;
knowledge_ingestion/artifact_storage.py and gemini_image_interpreter.py;
config/settings.py/model_warmup.py; observability runtime/tracing/redaction/metrics,
turn_trace/active_runs, agent/tool/model instrumentation/context/provider adapters;
requirements.txt; existing local guard and regression file roster; Collector/IaC files.
Installed ADK 1.33.0 session constructor was read without importing/starting it.

### Explicit dependency inventory and coverage ownership

| ID | Actual family / boundary | Source / operations | M5 coverage and honest limits |
|---|---|---|---|
| DEP-01 | Power Automate HTTP gateway, upstream Microsoft Graph/Teams | backend/gateway/power_automate_client.py: PowerAutomateClient._call; requests.post; teams.listChats/getMessages/getMembers/getHostedContent/createChat/sendMessage | One CLIENT span per actual POST. Local HTTP status/timeout/connection error visible before SafeError conversion. No direct Graph client, Graph bearer token, MSAL or remote Graph hop exists locally. |
| DEP-02 | Application session database (ADK) | backend/api/session_service.py:create_session_service_backend; DatabaseSessionService creates its own async engine; create/get/list/delete/append event/rewind storage | Observe ADK-owned engine through supported SQLAlchemy events; no schema, backend, session ownership or private engine sharing changes. InMemorySessionService creates no DB request spans. |
| DEP-03 | Case/progression database | backend/cases/db.py:CaseDatabase engine/session; cases/service.py and progression_store.py | Query, connection acquisition, pool and transaction outcomes below actual case operations, including persistence phases. |
| DEP-04 | Attachment metadata database | backend/attachments/repository.py:AttachmentRepository engine/session and CRUD/link operations | Same central SQLAlchemy observer; retain atomic link/rollback semantics and lazy schema handling. |
| DEP-05 | Knowledge database | backend/knowledge/repository/sqlalchemy.py:SqlAlchemyKnowledgeRepository; add/get/replace/list_versions/list_all | Metadata/governed corpus lookup DB spans; get/list_versions/list_all already retry once on InterfaceError/DBAPIError with a fresh session. PostgreSQL pre_ping/recycle retained. sqlite.py only reexports this implementation. |
| DEP-06 | GCS chat attachment binaries | backend/attachments/storage.py:ChatAttachmentStorage.put_bytes/get_bytes/delete/exists | Upload/download/delete/object-exists SDK calls; no remote operation for bucket()/blob() handle creation or uri_for() formatting. api/attachment_service.py already offloads sync calls via run_in_threadpool. |
| DEP-07 | GCS knowledge artifact binaries | backend/knowledge_ingestion/artifact_storage.py:KnowledgeArtifactStorage.put_bytes_if_absent/get_bytes/exists | One object-exists call and optional upload: two distinct external operations when absent, one when present; no fabricated atomicity or duplicate upload. No delete method exists here. |
| DEP-08 | Knowledge retrieval stages, SQL-backed corpus and in-process vector ranking | backend/knowledge/retrieval/service.py:retrieve/_dense_similarities; tools/knowledge/runtime.py composition; dense_similarity.py:similarities | INTERNAL stage observations for metadata/sparse/dense/fusion/applicability; real DB children and existing M3 embedding children. No remote vector DB/search service: sparse/cosine/fusion/applicability are local computation over loaded corpus. |
| DEP-09 | Gemini/Vertex model + embedding transports | config/settings.py shared model; config/model_warmup.py; knowledge/embeddings/service.py; tools/knowledge/dense_similarity.py; knowledge_ingestion/gemini_image_interpreter.py | Existing M3 google-genai HTTPX/aiohttp adapter owns physical requests, retries, auth/provider errors and usage observations. Exclude from M5 external-request spans; surrounding retrieval stages may parent M3 operations. |
| DEP-10 | GCP Secret Manager | backend/config/settings.py:_cached_secret_value; SecretManagerServiceClient.access_secret_version | Controlled CLIENT span for actual cache-miss read, fixed operation access_secret_version and purpose gateway/session_db/knowledge_db/other supplied by trusted caller. No secret names/versions/payloads. Cache hits create no network span. |
| DEP-11 | SDK credential acquisition/refresh, ADC/metadata/OAuth | Lazy storage/Secret Manager/GenAI client construction and SDK internals | Local code does not explicitly call OAuth or Graph token endpoint. Capture bounded credential initialization failure at owner; M3 retains model auth classification. Hidden refresh requests are SDK visibility limitation, not invented Graph/model spans or broad auth monkeypatch. |
| DEP-12 | OTLP Collector exporter transport | backend/observability/exporters/otlp.py:CollectorSession; exporter queues/runtime | Already M1 health owner; exclude M5 dependency spans/metrics to avoid recursive telemetry. Existing queue/export/drop/error metrics and Collector self-telemetry remain. |
| DEP-13 | BigQuery | No runtime BigQuery client/import/call found | Future FinOps infrastructure only (M11+); no client/spans/jobs created. |
| DEP-14 | Cloud Run / Cloud SQL platform / frontend-SSE | Source-controlled deployment settings and src/api/client.ts, streamChat.ts | Future platform metric correlation contract below. Frontend calls existing backend; browser tracing remains later milestone, no M5 frontend implementation. |

Inventory method: repository-wide rg for outbound HTTP/SDK/database/vector clients,
followed by ast.parse of every backend Python file excluding backend/tests, including
lazy imports. Concrete network sinks found: requests.post, GCS blob methods,
SecretManager access_secret_version, four SQLAlchemy engine owners and M3 GenAI
transports. Pure google.genai types/Parts, urllib.parse, model classes and SQLAlchemy
models do not themselves imply requests. No additional direct HTTP, Graph, Redis,
Pinecone/Qdrant/Weaviate/Chroma/Elasticsearch, BigQuery or Python Cloud SQL Connector
was found; Cloud SQL access uses configured asyncpg connection/proxy, not a new client.
Alembic is a deployment/migration boundary, not a runtime dependency to execute.
An attempted scripts directory search found no such directory; no tools were run there.

Machine-verifiable implementation gate: add an AST coverage test mapping every
concrete sink and constructor above to owner M1/M3/M5/future/local-only, including
lazy imports and aliases; reject newly introduced unassigned network clients/calls.
Maintain the finite manifest in dependency_instrumentation.py, referenced by this
test. Static analysis is a coverage guard, not proof of every SDK-internal request.

### Foundations reused and instrumentation architecture

- M0: canonical ErrorCode/Stage/RunStatus; central allowlists, restricted identity
  rules, finite metric registries, schema versions and immutable config projection.
- M1: one lifespan/runtime/provider, SCOPE and pinned schema URL 1.40.0;
  SDK 1.41.1/semconv 0.62b1, safe pre-queue projections, asynchronous exporters,
  bounded shutdown, fallback health and source-controlled Collector filters.
- M2: trusted current_turn, phase/root model_parent, terminal ownership,
  ActiveRuns execution-local cache and SSE identity. Persistence phase remains M2.
- M3: guarded/degraded isolation, trusted workload attribution, model logical and
  physical ownership, existing embedding_request and unsampled usage sink path.
- M4: active_execution/runtime_for_execution/attached_scope with closed-handle
  rejection; one logical slopanoc.agent and slopanoc.tool. Do not overwrite the
  execution descriptor or create a second tool/agent for a dependency.

Prefer controlled manual client-boundary adapters plus supported SQLAlchemy events.
No broad requests/httpx/aiohttp/SQLAlchemy auto-instrumentation or new package is
planned: default HTTP/SQL capture conflicts with strict projection and M3 ownership.
Small central sync/async dependency scopes invoke the original business callable
ONCE outside telemetry exception catches. Setup/attributes/events/metrics/end failures
are isolated individually; never catch business errors and retry the operation.
Return object, exception identity/type and cancellation behavior remain unchanged.
Span names are finite registered operations: http.client, db.client, db.connection,
db.transaction, storage.client, secretmanager.client and knowledge.retrieval.
CLIENT for real external operations; INTERNAL for local retrieval/transaction grouping.
Register safe optional extensions in doc 02; retain version 1 only if compatible
optional additions and export tests prove it. No persisted schema changes.

### Parentage, blocked-child visibility and duplication prevention

Select parent from a live trusted M5 scope, otherwise active M4 execution, otherwise
correct M2 active phase/root, otherwise separate server-owned background/ingestion
workload root. Reject terminal/closed/copied unrelated user contexts. Preserve native
OTel context across asyncio tasks and existing threadpool copies; attach/detach in
finally. Never infer parentage from IDs, baggage, URLs or tool arguments.

Expected: turn -> agent -> tool -> http.client/storage.client/db.client.
Knowledge: tool -> knowledge.retrieval INTERNAL -> DB or existing M3 model operation
-> existing gen_ai.request. Persistence: M2 persistence.final_answer -> DB/transaction.
Startup Secret Manager reads without a turn are system observations, not fake tools.

Roadmap blocked-child exit cannot be satisfied by ended spans alone. Extend the
existing M2 execution-local snapshot narrowly with optional safe live dependency
handles (dependency/operation/span/start/agent/tool), registered on entry and removed
in finally. For concurrent dependencies expose a bounded tuple of live children and
a deterministic most-recent live current-child projection; do not overwrite M2 stage
or use a single shared stack that loses siblings. Snapshot elapsed uses monotonic
clock; restoration handles out-of-order finishes and stale late workers. This is
execution-local metadata only: no persistence/API/UI, cross-instance truth or watchdog.
Telemetry disabled may still provide bounded safe local state as M2 already does;
export remains gated. Test blocked doubles while still awaiting their release.

Each boundary has one owner: gateway POST M5; DB statement events M5; connection and
transaction spans describe different work; GCS SDK operation M5 (hidden SDK wire
attempt count unknown); GenAI request M3; Collector request M1. Idempotent listener
registration and disposal avoid repeated engine hooks on restart. Counting rules:
N gateway POSTs -> N CLIENT spans; N DB cursor executions -> N query spans; N GCS
SDK operations -> N storage spans. Distinct INTERNAL grouping does not count as a
second request. Handle creation, cache hits, validation rejection and URI formatting
create zero network-request spans.

### HTTP / Microsoft Graph design

Central HTTP adapter receives a trusted finite dependency/operation/method and
optional registered route template; emits method, bounded status class, safe numeric
status code (100–599), duration, attempt and observed error flags. Read only bounded
Retry-After when needed; never access headers wholesale. Transport connection/read
timeout distinction retained when the requests exception type proves it.
Unknown HTTP operations map to other; method maps to fixed allowlist or OTHER.

Gateway dependency is power_automate_gateway, with upstream family microsoft_graph
only as declared topology metadata; no fabricated downstream CLIENT span. Operations
are the six existing server-owned teams.* constants. Route is fixed gateway invocation,
never a parsed SAS URL. Chat resolution calls listChats and local matching; do not
invent resolve_chat HTTP. Pagination metrics count actual getMessages POSTs with
bounded ordinal/count, never cursors or chat/message IDs. listChats upstream paging,
Graph latency/status/auth/token refresh and remote gateway retry behavior are hidden.
Gateway 401/403/404/429/5xx are gateway statuses, not proof of identical Graph statuses.

Canonical GRAPH_* codes may describe the Teams/Graph connector boundary, accompanied
by error_origin=gateway_transport or gateway_http; they MUST NOT claim verified Graph
failure. For real downstream status only use an existing reviewed safe scalar provider
status if actually available (none found); otherwise downstream status stays unknown.
Use test-double Graph-shaped scenarios to verify code mapping, labelled simulated,
and actual gateway tests to prove honest visibility. Adding Graph instrumentation to
Power Automate, upstream propagation or gateway response changes is not M5 local scope.

### Cloud SQL / SQLAlchemy and pool design

Install controlled observers on each actual async engine's sync_engine: cursor start/
end/error, connection lifecycle, checkout/checkin and transaction begin/commit/rollback.
No SQLAlchemy auto-instrumentor, statement/parameter attribute or exception stringify.
Read compiled statement structural flags when available for SELECT/INSERT/UPDATE/
DELETE/DDL/other; do not export even normalized arbitrary SQL. Per-execution storage
correlates event callbacks and closes failures/cancellation exactly once. No new engine
or session truth. Session construction is local; queries/transactions are dependencies.

Application engines owned by cases/attachments/knowledge register directly. ADK's
installed public constructor accepts engine kwargs, and source exposes db_engine.
A narrow source/version-tested observer registration after factory construction may
observe that engine without reusing/sharing it, accessing _private members or changing
ADK persistence. Pin unsupported-shape behavior: preserve business execution but FAIL
coverage tests. Read-only engine options must share hooks without duplicate spans.

Connection acquisition is timed at a supported public Pool.connect forwarding seam
using an instance-owned compatible pool subclass supplied through engine kwargs,
only where current pool type supports it. Delegate super().connect once; preserve
StaticPool/async QueuePool dialect handling, size/overflow/pre_ping/recycle/timeouts.
Do not globally patch engine/pool classes or reach into queue _do_get internals.
Checkout events alone cannot measure time spent waiting BEFORE checkout.
Measured acquisition duration includes queue wait, connection creation and pre_ping;
record boundary=pool_acquisition, not pretend it is pure queue wait. A pool-wait metric
is explicitly defined as this acquisition upper bound unless a verified separate
queue wait observation exists; never subtract estimated connect time. Export separate
connect durations when observable. Implementation must prove this seam with installed
SQLAlchemy 2.0.49 and local saturated-pool doubles before it is accepted.

Gauges: pool size, checked-out, overflow, checked-in (where supported); active DB
connections from established-connect/close/invalidation accounting, not session count.
Pool identity labels use session_db/case_db/attachment_db/knowledge_db, never URLs.
StaticPool unsupported size/overflow remains unavailable; do not fabricate zeros or
clamp negative startup overflow into fake saturation. Public metrics must be documented
for actual semantics. Acquisition timeout/exhaustion counters remain separate from
query timeout. Transaction rollback is an outcome, not always failure (expected atomic
link rejection/uniqueness recovery preserved). Failed commit can be persistence error;
M2 alone decides overall terminal persistence outcome.

Three existing knowledge read retries stay exactly one retry and use original fresh
session behavior. Add safe attempt/retry observation to their existing except sites;
replace raw '%s', exc retry logs with bounded code/count projection at this boundary
because DBAPIError can contain SQL/params. No other DB retry introduced. Lazy schema
create_all is observed if the application calls it; tests use isolated DB only. No
migration/schema-init/deployment is executed by M5 work.

### GCS / storage and Secret Manager design

Central storage observer used by both existing wrappers at each actual blob operation:
upload/download/delete/object_exists. Artifact put-if-absent has one existence child
and optional upload child, keeping existing behavior unchanged. Byte counts from
existing input/result lengths only; no metadata fetch added for observability. Bucket
role chat_attachments or knowledge_artifacts, never actual bucket name/object/hash/
URI/signed URL. SDK-level timeout/error classification from typed exceptions/scalar
status only. Hidden GCS retries are retry_visibility=unavailable; do not report zero
wire retries or monkeypatch authenticated requests globally. logical SDK operation
span count is testable, physical HTTP attempt count is not claimed.

Secret Manager instrumentation surrounds only the cached function's actual SDK read;
client construction remains lazy and cache unchanged. Fixed operation/purpose, safe
latency/status/error classification; no secret resource path, IAM principal/version
or payload. Unknown external family failures use registered generic TOOL_ERROR with
explicit dependency metadata rather than inventing new canonical errors; dependency
name disambiguates it. No auth refresh inspection/body capture.

### Knowledge dependency and diagnostic design

Keep generic knowledge code free of vendor/application orchestration imports.
Optional inert observer injection into KnowledgeRetrievalService supplies stage
scopes from concrete tools/knowledge/runtime.py; default no-op and fail-open telemetry.
Never inject telemetry into applicability/evidence selection return values or routes.
Stage boundaries: corpus/metadata lookup, governed applicability/currentness, sparse
scoring, dense similarities/vector scoring and fusion/ranking. No per-document spans.
All local stages INTERNAL; actual SQL statements CLIENT; dense embedding request and
usage observation M3 alone. Distinguish cache hit/local vector math from network.
Existing wait_for dense timeout and lexical fallback remain unchanged; catch-site
observation reports timeout even when outer search succeeds. Zero-result / no eligible
source is expected no_result, not dependency infrastructure error; source-gap policy
belongs existing governance. Missing provider configuration is unavailable, not a
fabricated timeout. Do not create remote applicability queries absent from source.

Preserve diagnostic_trace.py OBSERVABILITY ONLY — NEVER EVIDENCE. Optional correlation
at producer time projects counts/bounded codes and dependency span identity only;
never export its snapshot, queries, titles/headings, command text, identities or nested
payload. No telemetry writes into KnowledgeRunEvidenceState, provenance or authority.
Legacy diagnostic data is not presumed safe because bounded. Export sentinel tests
cover both new observers and raw legacy DB error suppression.

### Retry, timeout, rate-limit and error mapping

Observe only existing attempts; no retry policy, deadline, sleep/backoff, watchdog,
heartbeat, circuit breaker or command-control implementation. M6 owns those.
Gateway has no application retry; message pagination is distinct page operations,
not retries. Knowledge DB has observed fresh-session retry. M3 provider retries
stay M3. SDK-internal GCS/Secret Manager/credential retries remain unknown if hidden.
Retry-After: accept only finite nonnegative bounded seconds (cap 3600); invalid values
omitted; future HTTP-date parsing must not export source strings. Rate-limit counts
are independent from failures and never authorize retry. Non-idempotent writes retain
existing invocation/SDK semantics.

| Observable cause | Canonical observation / distinction |
|---|---|
| Gateway typed Timeout / ConnectTimeout / ReadTimeout | GRAPH_TIMEOUT, origin gateway_transport, timeout subtype when proven |
| Gateway 401/403; 404; 429; 5xx | GRAPH_AUTH_ERROR; GRAPH_NOT_FOUND; GRAPH_RATE_LIMIT; GRAPH_PROVIDER_ERROR, origin gateway_http; downstream Graph status unknown |
| Gateway connection error/invalid response/other HTTP error | GRAPH_PROVIDER_ERROR with bounded connection/invalid_response/http classification; application SafeError unchanged |
| SQLAlchemy pool TimeoutError / explicit DB timeout | DATABASE_TIMEOUT; pool_timeout distinguished from query_timeout; no fabricated timeout from generic DBAPIError |
| Proven DB connection/invalidation failure | DATABASE_CONNECTION_ERROR; do not classify every DBAPIError as connection failure |
| Query failure / failed commit-persistence | DATABASE_QUERY_ERROR / DATABASE_PERSISTENCE_ERROR; raw exceptions/SQL excluded |
| Expected DB rollback/integrity business outcome | Safe transaction outcome; do not automatically fail root or inflate dependency availability failures |
| Dense existing timeout / provider failure | KNOWLEDGE_TIMEOUT / KNOWLEDGE_PROVIDER_ERROR; M3 owns child provider code; lexical fallback still may succeed |
| Zero result/source gap | no_result, optional KNOWLEDGE_NO_SOURCE only for proven missing-source observation; not infrastructure failure |
| Typed GCS timeout / other SDK failure | STORAGE_TIMEOUT / STORAGE_ERROR |
| Secret Manager typed timeout/auth/rate limit/unknown failure | TOOL_TIMEOUT or TOOL_ERROR plus finite failure_kind; no fabricated GRAPH/MODEL classification |
| asyncio cancellation / GeneratorExit | CANCELLED, preserve original propagation; not timeout/error or successful remote write |
| Unclassified exception | Bounded unknown failure_kind and applicable family provider/error code; no message-based guessing |

### Privacy / sanitization and metrics contract

Explicit typed allowlist applied before span creation, events/logging, metric recording,
queue serialization and Collector export. Fixed dependency/operation/stage/route sets,
method/status/error origin/kind, numeric status/count/bytes/duration/attempt/retry-after
and typed booleans only. Trace correlation reuses trusted M2/M4 identity, never copies
resource/user/object/request IDs from dependency arguments. No headers/cookies/bodies,
SQL or bound params, Teams content, prompts/responses, vectors/embeddings, knowledge
bodies/titles/queries, signed URLs, token/secret strings or unrestricted exception data.

URL sanitization is central and fail-closed: remove userinfo/query/fragment; reject
malformed/encoded/control-character paths; output ONLY registered route templates by
known dependency operation, not regex-redacted arbitrary paths/hosts. Unknown -> other
or field omitted. Tests include SAS URLs, bearer/query secrets, GUIDs, chat/message/
customer IDs, percent/double encoding, unusual delimiters and userinfo. Graph template
/chats/{id}/messages is safe only for a real matching direct client; current gateway
uses a fixed operation label, not that fictitious route. No URL metric label.

Proposed unsampled application metrics (unit 1 counters; s histograms):
- slopanoc.dependency.requests/duration/failures/timeouts/rate_limits/retries;
- slopanoc.dependency.pages (actual gateway read pages, not hidden Graph pages);
- slopanoc.storage.bytes_uploaded/bytes_downloaded (By counters);
- slopanoc.db.pool.wait (s, documented acquisition upper bound),
  slopanoc.db.pool.exhaustion (1), slopanoc.db.pool.connections/checked_out/size/
  overflow/checked_in gauges (actual supported semantics, signed overflow if needed);
- separate slopanoc.knowledge.stage.duration/no_result/fallback observations so local
  CPU stages do not inflate external dependency request denominator.

External request dimensions exactly environment/dependency/operation/status; bounded
method/status_class may be added only to HTTP-specific instruments with matching
views/tests, not arbitrary labels. DB gauges use environment/dependency plus operation
as the bounded engine role; no new URL/host/DB-name identifiers. Knowledge stage
metrics use finite stage operations. Unknown names -> other. Error codes in spans;
no raw error strings/IDs/object paths/model-generated values in labels. Register names,
units, views, runtime instruments and exporter validation plus Collector mirrors
atomically; strip exemplars. Count real operations even if traces sampled/disabled
where the existing metric runtime is enabled. Disabled runtime does not initialize
clients or emit exports. Success/cancel/no-result denominators documented separately.

### Infrastructure metrics contract and Settings / UI impact

Application-side future joins use existing resource service.name/environment,
cloud.region, Git SHA/release/Cloud Run revision when configured; no invented release
values and no run ID as metric label. Approved static engine/bucket/dependency roles
can be mapped in IaC to deployment resources later, without exporting credential URLs.

| Owner | Future platform signals / join |
|---|---|
| Cloud Run | Requests/latency/status/concurrency/CPU/memory/instances/startup-cold-start/failures; join service/environment/region/revision with application request/root latency, not duplicated as fabricated app gauges |
| Cloud SQL | CPU/memory/connections/utilization/storage/latency/IOPS/locks/deadlocks; join approved deployment DB mapping to application engine-role/pool/query/error signals |
| Collector (M1) | Queue/export failures/drops/retries/memory/accepted/refused/exported, CPU/memory and scrape latency from self-telemetry; exclude exporter traffic from M5 and use fallback when export path fails |
| Vertex/Gemini | Existing M3 attempts/usage/latency/rate limits; future provider platform health join, no second usage path |
| BigQuery | Future query bytes/slots/latency/failures/storage/cost; absent runtime now |
| GCS / Graph / knowledge | Safe application operation rate/latency/failure/timeout/throttle and declared attribution; upstream Graph health unknown beyond gateway observations |

Do not provision/scrape live metrics, define health thresholds or deploy dashboards in
M5. Record effective application contracts in observability README/doc 17; existing
Terraform remains unchanged. Dashboards/alerts/SLOs and health synthesis later.

Explicit five-part UI evaluation:
1. M5 creates dependency metadata required by future Integrations/Diagnostics/Overview:
   dependency/operation, elapsed duration, latency, failures/timeouts/rate limits.
2. Future M7 contracts must project permitted aggregates/live-child summaries, report
   freshness, missing data and gateway-vs-upstream visibility; M5 defines internal safe
   fields only. Do not persist or add APIs.
3. UI work belongs M8/later; no Settings/frontend/SSE/progress-label modification now.
4. Later APIs enforce server RBAC; FinOps receives usage/cost, not Teams/knowledge/
   SQL/content; no new routes or broader permissions in M5.
5. Infrastructure/tracing configuration remains backend/environment/IaC-owned,
   effective read-only later. No Settings health button triggers a remote probe/write.

### Exact files expected to change during future M5 implementation

ADD central helpers:
- backend/observability/dependency_instrumentation.py — finite inventory/scopes/live handles;
- backend/observability/dependency_http.py — route projection/HTTP classifications;
- backend/observability/dependency_database.py — controlled engine/pool/transaction hooks;
- backend/observability/dependency_storage.py — GCS and secret read observers;
- backend/observability/dependency_metrics.py — registered bounded instruments/views;
- backend/observability/knowledge_instrumentation.py — concrete observer adapter.

MODIFY existing telemetry owners:
- backend/observability/attributes.py;
- backend/observability/redaction.py;
- backend/observability/tracing.py;
- backend/observability/metrics.py;
- backend/observability/runtime.py;
- backend/observability/active_runs.py;
- backend/observability/turn_trace.py;
- backend/observability/README.md.

MODIFY actual integration boundaries only:
- backend/gateway/power_automate_client.py;
- backend/tools/teams/get_messages.py (safe final pagination count only);
- backend/api/session_service.py (ADK observer registration/compatible pool kwargs);
- backend/cases/db.py;
- backend/attachments/repository.py;
- backend/knowledge/repository/sqlalchemy.py;
- backend/attachments/storage.py;
- backend/knowledge_ingestion/artifact_storage.py;
- backend/config/settings.py (cache-miss secret read observer, no config-policy change);
- backend/tools/knowledge/runtime.py;
- backend/knowledge/retrieval/service.py (optional inert stage observer);
- backend/tools/knowledge/diagnostic_trace.py (safe correlation producer only).

ADD tests:
- backend/tests/test_observability_dependency_coverage.py;
- backend/tests/test_observability_dependency_instrumentation.py;
- backend/tests/test_observability_dependency_http.py;
- backend/tests/test_observability_dependency_database.py;
- backend/tests/test_observability_dependency_storage.py;
- backend/tests/test_observability_dependency_knowledge.py;
- backend/tests/test_observability_dependency_metrics.py;
- backend/tests/test_observability_dependency_safety.py;
- backend/tests/test_observability_dependency_performance.py.

MODIFY validation/docs/Collector:
- backend/tests/test_observability_collector.py;
- backend/tests/test_observability_active_runs.py;
- infra/observability/collector/config.yaml;
- infra/observability/collector/config.local.yaml;
- infra/observability/collector/README.md;
- docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md;
- docs/Telemetry/17_INFRASTRUCTURE_OBSERVABILITY.md;
- .agent/OBSERVABILITY_EXECUTION.md.

No requirements upgrade, migrations, ORM tables, Terraform resource/dashboard/alert,
M3 transport, business routing/authority refactor, API or frontend file expected.
If implementation discovers another necessary file, inspect/update this plan first;
not permission to broaden milestone scope. Actual file changed in this pass: execution
record only. Collector additions must deny arbitrary url.full/db.query.text/headers/
bodies, preserve M1–M4 projections and match Python typed rules.

### Sequential implementation plan (NOT executed)

1. Reverify M4 gate/source and document implementation authorization; establish safe
   contracts, finite manifest and fault-isolated central scopes/metric projections.
2. Add live-child observation to existing local M2 cache, with concurrent restoration,
   terminal cleanup and closed-context denial; no durable/query/UI features.
3. Instrument gateway at original POST/status/error boundary, pages and Secret Manager
   cache-miss SDK read. Preserve requests.post test seam and SafeError behavior.
4. Register controlled SQLAlchemy observers for all four owners; prove public pool
   seam and exact query counts with isolated engines/doubles; observe existing retries
   and sanitize their DBAPIError logging. Preserve ADK ownership and transactions.
5. Instrument two GCS wrappers at real operations; count conditional writes exactly;
   preserve SDK retry policy and offload/context behavior.
6. Inject no-op-default knowledge stage observer, preserve pure generic dependencies,
   existing dense timeout/fallback and M3 provider ownership; add safe diagnostic bridge.
7. Extend application + Collector projections and safe metric exporter together; run
   focused/new and existing regressions, independent malicious OTLP tests and overhead.
8. Review complete diff/untracked baseline and individually evaluate every exit; stop
   after M5 implementation. M6 only after all applicable gates pass and separate request.

### Validation plan and commands (future; NOT run in this planning pass)

Use .venv, fake SDK/gateway/model transports, in-memory/temp SQLite, loopback local
Collector only and a fail-closed network guard. The existing
/private/tmp/slopanoc-m1-test-guard/run_tests.py and sitecustomize.py were inspected;
verify/recreate isolated guard before reuse since /tmp is not persistent. Clear inherited
SLOPANOC/Google credential/provider environment, disable warmup/Vertex, inject fake
clients before construction. Guard socket connections before imports; do not run app
startup, migrations or generic full suite until each fixture is audited for isolation.
No ADC, real Graph/GCS/Secret Manager/model/DB/exporter requests or live cloud testing.

Focused command after the named tests exist:
```bash
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_dependency_coverage.py backend/tests/test_observability_dependency_instrumentation.py backend/tests/test_observability_dependency_http.py backend/tests/test_observability_dependency_database.py backend/tests/test_observability_dependency_storage.py backend/tests/test_observability_dependency_knowledge.py backend/tests/test_observability_dependency_metrics.py backend/tests/test_observability_dependency_safety.py
```

M0–M5 regression command (expand explicit test manifest after auditing fixtures):
```bash
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_*.py backend/tests/test_chat_service_root_trace.py backend/tests/test_power_automate_client.py backend/tests/test_teams_get_messages_pagination.py backend/tests/test_teams_execute_write.py backend/tests/test_no_secret_leak.py backend/tests/test_attachments_storage.py backend/tests/test_attachments_repository.py backend/tests/test_knowledge_artifact_storage.py backend/tests/test_settings_secret_caching.py backend/tests/test_api_session_backend_factory.py backend/tests/test_database_session_e2e_consistency.py backend/tests/test_case_persistence.py backend/tests/test_api_persistence.py backend/tests/test_live_defects_investigation_and_repair.py backend/tests/knowledge/
```
Retain the exact 155-file M4 broad manifest above and union the audited dependency,
Teams/storage/session/secret regressions listed here. Includes governance, approvals,
command authority/egress, continuation/provenance, SSE, rewind, cancellation and local
knowledge architecture boundaries. Deduplicate file list; retain command/result counts,
no silent skips. Additional backend regressions affected by actual integration changes
must pass. Frontend npm test/build only if shared contracts unexpectedly change (none
planned); update scope before such change.

Import/static/hygiene commands:
```bash
.venv/bin/python -m compileall -q backend/observability backend/gateway backend/cases backend/attachments backend/knowledge backend/knowledge_ingestion backend/tools/knowledge backend/api/session_service.py backend/config/settings.py
.venv/bin/python -m pip check
git diff --check
git status --short
git diff --stat
git diff
```
Do not compile/import code as a substitute for behavior tests. AST coverage scan does
not import business modules or resolve secrets. Collector validation uses existing
local binary/YAML fixtures, with independent adversarial OTLP positive/negative
projections and unchanged M1–M4 tests; no docker pull/deployment/live GCP invocation.

| Test group | Required objective assertions |
|---|---|
| Coverage | Every concrete dependency sink assigned once, aliases/lazy paths caught; unavailable/future/SDK-hidden boundaries explicitly distinguished |
| HTTP/gateway | Success, each status class, 401/403/404/429/5xx, invalid response, connection/read/unknown timeout, bounded Retry-After and unchanged SafeErrors; graph-vs-gateway origin honest |
| Pagination/retry | N gateway posts -> N spans/pages; pages not retries; existing DB retry two actual attempts, same fresh sessions; hidden SDK retries unknown; no new retries |
| Database/pool | Real isolated SQLite cursor/transaction hooks, fake asyncpg-compatible pool saturation with blocked acquisition, acquisition timing semantics, connection/query/pool timeout, rollback and failed commit, gauges, repeated registration/disposal |
| SQL privacy | Sensitive SQL literals, bound params, exception strings and DB credentials absent from spans/logs/metrics/Collector; operation class retained; existing raw retry log suppressed |
| GCS/secret | Upload/download/delete/exists exact SDK counters; artifact hit 1 exists/0 uploads, miss 1 exists/1 upload; bytes safe; SDK timeout/error; secret cache miss 1 read, hit 0; no object/bucket/hash/payload leaks |
| Knowledge | Lexical/hybrid/dense cached/unavailable/timeout/provider failure; expected empty/zero result; applicability/fusion counts; no query/body/vector/legacy snapshot; M3 physical span/usage counts unchanged |
| Parent/current child | Real tool/agent/phase native trace graph; blocked child identifiable BEFORE release; concurrent live set/out-of-order completion restores correct state; ingestion/background detaches copied user identity |
| Exact counts | Logical group vs physical request distinctions; no auto/manual/M3/exporter duplicates; no span on local validation/handle creation/URI/cache hit |
| Failure/side-effect isolation | Inject failures at begin/attach/redact/events/metric/log/end/export/cache registration; original exception/result unchanged; counter proves one POST/write/commit/delete, never telemetry replay |
| Cancellation/concurrency | Cancel async Graph-double/DB/retrieval/storage offload scenarios; spans close and no late cross-turn contamination; sync worker may outlive cancellation exactly as before, never claim remote abort; simultaneous two+ turns and tasks |
| Metrics/privacy | Finite labels/units/values, sampling independence, exemplar stripping; malicious URL/body/token/header/Teams/SQL/GCS/knowledge/vector sentinels absent across all new sinks |
| Regression/Collector | M0–M4 plus affected backend/governance/approval/authority/SSE/rewind suites pass; malicious OTLP blocked and valid M5 metadata survives Collector |

Performance command:
```bash
.venv/bin/python /private/tmp/slopanoc-m1-test-guard/run_tests.py backend/tests/test_observability_dependency_performance.py
```
Measure HTTP, DB/acquisition and storage/retrieval scopes: raw baseline, disabled,
enabled no-export and enabled local exporter; warmups then >=100 paired samples
with rotating mode order. Separate injected dependency wait from observer CPU/time,
flush outside timing, report median/p95/p99, retained/peak memory, queues and live
handle cleanup. Initial synchronous overhead target <10ms excluding waits/export,
report distributions; <3% typical production CPU is NOT proven by local microbenchmarks.
No production load/performance claim. Failed local agreed target blocks acceptance.

### Individually objective M5 exit criteria

All runtime criteria are **PENDING — NOT IMPLEMENTED / NOT VALIDATED** now.
1. Every real dependency family in DEP-01–12 is covered by its correct existing/new
   owner, with future/local-only/hidden boundaries explicit and static coverage gate PASS.
2. Blocked external dependency identifiable as active child during the wait, with
   correct tool/agent or phase parent and elapsed time; no API/persistence required.
3. Exact request/span counts match observable boundary granularity; no duplicate
   model embedding/provider spans, usage observations or Collector recursion.
4. Central URL sanitization survives malicious URLs; raw URL/query/header/body/SQL/
   params/secret/Teams/knowledge/vector/object content absent at Python and Collector.
5. Graph connector auth/not-found/rate-limit/provider/timeout scenarios visible with
   honest gateway origin and actual pagination; downstream Graph status not fabricated.
6. Four DB owners expose query/connection/pool/transaction failures, bounded pool
   gauges and honest acquisition-wait semantics; saturation/timeout demonstrated locally.
7. Both GCS stores and cache-miss Secret Manager calls visible with safe roles,
   bytes/errors/timeouts where observable and correct conditional operation counts.
8. Knowledge stages + real DB/embedding children visible; expected no-result and
   dense fallback preserved; diagnostics remain observability-only, never evidence.
9. Existing retry/timeout/rate-limit behavior observed without changed policy; hidden
   attempts stay unknown. No M6 reliability controls implemented.
10. Bounded unsampled metrics pass units/value/cardinality/privacy checks and Collector
    projections; tool and dependency durations remain separate concepts.
11. Cancellation and simultaneous Graph-double/DB/retrieval/GCS scenarios close spans,
    restore live-child/context state and show no cross-turn/late-worker contamination.
12. Telemetry failures do not change results/errors, cause duplicate remote writes,
    replay transactions or retry side effects; all counted fault-injection cases PASS.
13. Code imports/builds, focused tests, M0–M4 plus affected backend/governance/approval/
    authority/SSE/cancellation/rewind regressions and Collector validations PASS.
14. Local observer overhead measured and agreed local budget PASS; production limits
    explicitly unvalidated. Scoped Git/diff/untracked review PASS.
15. No new DB persistence/API/UI/ledger/future milestone functionality, no remote/cloud
    mutation, and no unrelated local work modified. User approval gate preserved.

### Risks / blockers, cloud status and planning validation

No blocking predecessor discrepancy found. Implementation risks to resolve in local
proofs: supported ADK engine observation/SQLAlchemy public pool seam; honest pool
acquisition semantics; SDK-hidden attempts/auth; knowledge observer injection preserving
pure architecture; synchronous thread cancellation with late workers; concurrent live
child restoration; Collector exact typed projections and legacy raw DB error logs.
Do not silently accept missing ADK/pool coverage or infer production health from local
passes. If a supported seam cannot meet an exit criterion, record FAIL and stop;
no global monkeypatch/private queue patch or cloud mutation as a workaround.

Direct Graph latency/auth/token/paging and hidden SDK wire attempts are inherently
unavailable in the current local architecture. This is an explicitly recorded visibility
limit; M5 covers the real gateway boundary rather than inventing upstream calls.
Full upstream instrumentation would require a separately approved gateway/provider
change. No such change is proposed/executed here. Pure queue wait may be unavailable;
metric explicitly reports measured acquisition upper bound, with saturation proved.

Local implementation: **NOT IMPLEMENTED**.
Local runtime validation: **NOT RUN (planning only)**.
Planning validation: specs/source/import AST inventory/historical gate audit PASS.
SHA256 comparison of 799 baseline files confirms only this execution record changed;
all other 798 files unchanged. Program status/matrix and full appended plan reviewed;
new-section whitespace check PASS; git diff --check PASS; tracked diff/status and
untracked roster reviewed. No pytest/app startup/SDK/Collector/Terraform run.
M5 exit criteria: **PENDING**, not PASS based on this plan.
Cloud deployment status: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Exact pending M5 cloud operations: **NONE — local M5 implementation requires none**.
Inherited M1 deployment operations remain pending under their earlier record; this
plan authorizes none of them. No GCP/Cloud Run/Cloud SQL/BigQuery/IAM/billing/GCS/
Graph/Terraform state/shared database/external mutation occurred.

**Ready To Implement M5: YES** — plan complete; wait for a separate implementation
request. **Ready for Next Milestone: NO. M6 BLOCKED. STOP after planning.**


## M5 implementation entry — 2026-10-08

User approved local M5 implementation/validation only. Approved planning section
above remains the implementation map; predecessor M0–M4 local gates PASS.
The implementation request supersedes two planning choices: gateway generic errors
MUST NOT map to GRAPH_* without trustworthy downstream origin; use TOOL_ERROR/
TOOL_TIMEOUT with gateway failure_kind instead. Connection acquisition is named
connection_acquire_duration, never pool_wait when not separable. M6 remains blocked.
No deployment, external calls, shared DB/migrations, Terraform or cloud mutation.
Implementation snapshot captured before edits; preserve existing local work.
Expected file list/architecture/safety/tests/criteria are the approved plan above.
Additional pure dependency_contract.py separates finite projection vocabulary from
runtime adapters; generic knowledge observer protocol/default stays vendor-free.
M3 parent resolver may receive a minimal live M5-parent hook (no provider/request/
usage changes) to satisfy embedding-under-dense parentage. Tests must prove exact
M3 ownership remains. All final tests use a deny-external-network isolated guard.


### M5 implementation performed / evidence (final validation in progress)

Authoritative scope: approved M5 plan above and 2026-10-08 implementation request;
roadmap M5, docs 02/03/09/11/13/17/18 and the master/execution/done contracts.
M0–M4 predecessor gates were inspected and preserved. Exactly M5 is implemented.
No M6 control, later persistence/API/UI/accounting, new SDK transport, provider
client, global monkeypatch, IaC mutation or external call was introduced.

Implementation:
- Pure dependency_contract finite span/role/operation/type projections reused by
  redaction/metrics/Collector. Exact CLIENT_OWNERS static SDK import/sink coverage
  assigns M1 exporters, M3 provider/embedding ownership and M5 concrete clients.
- Central guarded dependency scopes, native M4/M2 parentage and truthful failure
  classification; original business calls sit outside observer guards and execute
  once. Failures carry scalar codes/origins, never exception text/content. Logging
  uses explicit dependency correlation so ingestion cannot borrow a user identity.
- Gateway six real POST operations: constant server routes, observed HTTP status,
  401/403/404/429/5xx, bounded numeric Retry-After and typed connect/read timeout
  observed before SafeError collapse. TOOL_ERROR/TOOL_TIMEOUT exclusively; no
  downstream Graph status inferred. Pages count real getMessages calls; hidden
  SDK/gateway attempts remain unknown. Original timeout/write/pagination unchanged.
- Four SQLAlchemy owners (ADK/session, cases, attachments, knowledge): public
  instance pool subclasses/events and session-factory subclasses; one span per
  cursor execution without SQL/parameters/DSN; confirmed session commit/rollback
  and honest requested-only bare engine transactions. No replay. Inclusive
  connection_acquire_duration (wait/connect/pre_ping), real saturation/exhaustion,
  supported public pool gauges aggregated by role, weak engine/pool tracking and
  instance recreation without duplicated listeners. No private queue/ADK patch.
- Both GCS stores wrap actual SDK upload/download/exists/delete only; conditional
  exists hit emits no upload; construction/URI emits nothing. Bytes-only metadata.
  Secret Manager access_secret_version cache misses only; cache hits emit no span.
- Vendor-free optional retrieval observer composed from concrete runtime injection:
  metadata/applicability/sparse/dense/fusion stages, no-result and dense fallback.
  Retrieval ranking/evidence/diagnostic_trace semantics unchanged. A real DB query
  is parented under metadata; existing M3 embedding calls under dense with exactly
  three requests/observations for a two-search cache scenario; no HTTP duplication.
- TurnTrace/Progress add bounded weak execution-local concurrent live dependencies,
  safe descriptors/elapsed/parent/tool and restoration. Model parent resolver adds
  only the minimal live M5-parent hook; M3 provider/request/usage code unchanged.
- Unsampled finite metrics: requests/failures/timeouts/rate_limits/retries/pages,
  storage bytes, pool exhaustion/acquisition duration, supported pool gauges and
  retrieval no-result/fallback/stage duration. Seconds/By/1 units; finite labels only.
  Acquisition/transaction/local groups do not inflate external request counts.
- Both pinned Collector configs enforce exact M5 names/types/finite labels/units and
  direct malformed-OTLP denial. Python strips exemplars; Collector independently
  drops M5 datapoints containing exemplars, preserving safe siblings. No topology,
  sampling, resource discovery, exporter, platform collection or deployment change.
- Infrastructure doc encodes future Cloud Run request/count/CPU/memory/concurrency/
  instance/cold-start and Cloud SQL CPU/memory/connections/storage/platform latency
  integration; Collector self-metrics remain M1-owned, BigQuery future-only.

Actual files changed (compared with pre-M5 SHA256 implementation snapshot, not
Git HEAD; existing M0–M4 tracked/untracked work is preserved):
- `.agent/OBSERVABILITY_EXECUTION.md`
- `backend/observability/README.md`
- `backend/observability/active_runs.py`
- `backend/observability/logging.py`
- `backend/observability/metrics.py`
- `backend/observability/model_instrumentation.py`
- `backend/observability/redaction.py`
- `backend/observability/runtime.py`
- `backend/observability/tracing.py`
- `backend/observability/turn_trace.py`
- `backend/tests/test_observability_collector.py`
- `docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md`
- `docs/Telemetry/17_INFRASTRUCTURE_OBSERVABILITY.md`
- `infra/observability/collector/README.md`
- `infra/observability/collector/config.local.yaml`
- `infra/observability/collector/config.yaml`
- `backend/api/session_service.py`
- `backend/attachments/repository.py`
- `backend/attachments/storage.py`
- `backend/cases/db.py`
- `backend/config/settings.py`
- `backend/gateway/power_automate_client.py`
- `backend/knowledge/repository/sqlalchemy.py`
- `backend/knowledge/retrieval/service.py`
- `backend/knowledge_ingestion/artifact_storage.py`
- `backend/tests/test_api_session_backend_factory.py`
- `backend/tests/test_database_session_e2e_consistency.py`
- `backend/tests/test_live_defects_investigation_and_repair.py`
- `backend/tools/knowledge/runtime.py`
- `backend/tests/test_observability_dependency_http.py`
- `backend/tests/test_observability_dependency_storage.py`
- `backend/tests/test_observability_dependency_performance.py`
- `backend/tests/_m5_dependencies.py`
- `backend/tests/test_observability_dependency_safety.py`
- `backend/tests/test_observability_dependency_database.py`
- `backend/tests/test_observability_dependency_coverage.py`
- `backend/tests/test_observability_dependency_metrics.py`
- `backend/tests/test_observability_dependency_instrumentation.py`
- `backend/tests/test_observability_dependency_knowledge.py`
- `backend/observability/dependency_http.py`
- `backend/observability/dependency_instrumentation.py`
- `backend/observability/dependency_database.py`
- `backend/observability/dependency_storage.py`
- `backend/observability/dependency_metrics.py`
- `backend/observability/dependency_contract.py`
- `backend/observability/knowledge_instrumentation.py`

Additional necessary validation-fixture repairs beyond the initial file list:
ADK factory structural assertion now checks one construction/return; existing
session E2E fixture awaits service.close before deleting its disposable DB; existing
read-retry fake delegates the real context exit and closes the repository. These
repairs address connection cleanup exposed by M5 pool observation, not application
behavior or unrelated refactoring. Collector test stderr goes to a local tempfile
rather than an unread pipe, and reinjection waits count the relevant signal only.

Failures encountered and repairs (all must pass before final completion):
1. Observe-call parameter named name collided with Secret Manager SDK name=;
   positional-only adapter parameters fixed it without replaying SDK calls.
2. ConnectTimeout inherited ConnectionError; classifier now preserves timeout kind.
3. Existing source-shape factory assertion updated to reflect observed construction.
4. Metric gauge dimensions and static type-only TAE inventory expectation corrected.
5. Generated Collector bool rule accidentally expanded field list; narrowed/fixed;
   both pinned configs natively validated and runtime exercised afterward.
6. Sandbox prohibits local binding; automatic review allowed isolated loopback tests
   with cleared cloud/production environment and external socket rejection. No remote
   operation, cloud approval or mutation inferred from local sandbox escalation.
7. Broad Collector assertion counted legitimate DB children beyond its synthetic
   tool; filter by tested tool parent. Reinjection waits made trace-specific to avoid
   unrelated log/metric arrival races. Existing leaked DB fixtures cleaned up above.
8. Pool class-listener cleanup introduced async dispatch AttributeError (_set_asyncio),
   caught by real pool saturation/release/cancel tests. Explicit public asyncio=False
   registers synchronous class callbacks; real async pool behavior/counts now pass.
   Gauge test selects its DB role because gauges aggregate all live pools by role.
9. Empty-list exemplar setter validated syntactically but runtime rejected []interface
   rather than ExemplarSlice, causing metric export failures. Removed setter; exact
   M5 datapoint filter uses supported Len(exemplars), denies payload independently.
   Safe sibling metrics survive; final native + all five runtime variants PASS.

UI / Settings impact evaluation:
1. M5 creates safe dependency/status/elapsed/timeline/cost-independent operational
   facts required by future Settings → Observability & FinOps (Tracing/Reliability/
   Diagnostics/Integrations/Overview); no normal-user internal diagnostics added.
2. Future M7 APIs may project safe span/snapshot descriptors. No wire/API/schema/
   SSE contract changes, DB persistence or cross-instance active-run state added.
3. No UI work belongs to M5. Existing visual/navigation/interaction patterns preserved.
4. Future diagnostics require server RBAC (Operator/Developer/SRE/Admin/Auditor as
   authorized); FinOps gains no content access. M5 creates no new access surface.
5. Effective production config remains Terraform/environment/backend owned and
   view-only; no runtime setting/editor or IaC bypass added.

Validation commands (isolated guard clears SLOPANOC/cloud/proxy configuration,
uses disposable SQLite/fake clients and rejects non-loopback socket connections):
```bash
.venv/bin/python /private/tmp/slopanoc-m5-test-guard/run_tests.py backend/tests/test_observability_dependency_*.py
.venv/bin/python /private/tmp/slopanoc-m5-test-guard/run_tests.py backend/tests/test_observability_collector.py
.venv/bin/python /private/tmp/slopanoc-m5-test-guard/run_tests.py --manifest /private/tmp/slopanoc-m5-regression-manifest.json
.venv/bin/python -m compileall -q backend/observability backend/gateway/power_automate_client.py backend/api/session_service.py backend/attachments backend/cases/db.py backend/config/settings.py backend/knowledge backend/knowledge_ingestion/artifact_storage.py backend/tools/knowledge/runtime.py
.venv/bin/python -m pip check
git diff --check
git status --short
```
The 211-file regression manifest includes all 155 M4 manifest files and affected
Teams/Power Automate, ADK/session/DB/pool, storage/secret/knowledge, context/evidence/
provenance/governance/TAE/approval/authority/command/SSE/rewind/cancellation and M0–M5
telemetry suites. No frontend changes: frontend build/test not applicable. No cloud,
Terraform, remote/shared database, deployment or IAM command run.

Intermediate results: focused M5 208 PASS; actual knowledge DB + stages/embedding
13 PASS after additional exact-parent test; real DB/pool 13 PASS; final Collector
seven tests (two native configs + five runtime variants) PASS. Broad pre-final
config suite 3707 PASS, 476 warnings, 90.70s; final completed-source rerun pending.
Compileall PASS; pip check PASS; whitespace check PASS. Counts overlap, not additive.
No acceptance inferred from documentation; final criteria/results recorded below.

Known limits: no direct Graph/upstream auth/wire retries visibility, SDK-hidden GCS/
Secret Manager retries unknown, inclusive acquisition rather than pure queue wait;
bare engine transaction events report request not confirmed completion. Cancelling
asyncio.to_thread await cannot abort an SDK call already running: span ends on actual
worker completion, terminal live handles clear, writes never replay. Pool gauges are
process-local supported public stats, not Cloud SQL platform metrics. Live handles
cap 128 concurrent children; no durability/API. Platform health/production latency/
CPU/memory/load and actual deployed Google Collector remain unvalidated locally.
Production overhead target (<3% typical CPU) is not proven by microbenchmarks.
Broad warnings are chiefly existing ADK/Pydantic/Starlette deprecations/experimental
feature warnings; one non-failing pooled-connection cleanup warning appears during
an unrelated warmup test. Controlled M5 pool/fault/cancellation fixtures pass cleanly.
No runtime cloud-dependent M5 exit criterion exists; no M5 cloud operation required.
Cloud deployment status = NOT EXECUTED — USER APPROVAL REQUIRED.
Exact pending M5 cloud operations = NONE. Inherited M1 rollout remains separately
pending; this request neither executes nor authorizes it.


### Final M5 validation results — 2026-10-08

Local implementation = IMPLEMENTED.
Local validation = VALIDATED COMPLETE (all blocking M5 local criteria PASS).
Cloud deployment = NOT EXECUTED — USER APPROVAL REQUIRED.
Exact pending M5 cloud operations = NONE. M1 deployment remains separately pending.

- Full 211-file M0–M5/affected regression union: **3708 PASS, 487 warnings, 92.65s**.
  Final Retry-After non-finite Collector rule added after that run; all affected
  tests independently rerun against final configs, not assumed from syntax.
- Final focused M5 + Collector source gate: **216 PASS, 1 warning, 23.74s** (209
  M5 cases and 7 Collector cases). Includes both native 0.160.0 configurations,
  actual five SDK→Collector→loopback-sink variants, direct trace/metric/exemplar/
  NaN poisoning, positive safe sibling survival and M1–M4 metadata regression.
- Controlled actual DB/pool/knowledge/HTTP concurrency/GCS cancellation lifecycle
  gate with SQLAlchemy SAWarning, pytest unhandled-thread and unraisable warnings
  promoted to errors: **42 PASS, 1 unrelated Starlette deprecation, 2.77s**.
  This proves controlled M5 fixtures have no such lifecycle warning; broad legacy
  suite warnings are recorded and not suppressed or silently called clean.
- Compilation of all changed boundaries and new observers/tests: PASS.
- pip check: PASS (no broken requirements).
- Git whitespace/status/tracked diff and untracked roster review: PASS.
- SHA256 preservation audit: 799 pre-M5 files; 29 scoped modifications, 770
  unchanged, 17 scoped new files, 0 deletions. No unrelated existing work changed.
  The full roster above is authoritative; git HEAD includes earlier local work.

Broad warning detail: mostly existing ADK/Pydantic/Starlette deprecations and an
experimental SSE flag; a pooled-connection GC warning surfaces in the existing
warmup test, and aiosqlite event-loop-closed worker warnings surface in rewind/
procedure regression tests. Their originating fixture allocation is not isolated.
They are non-failing test-suite cleanup warnings, not waived failed assertions.
No broad zero-warning or production connection-health claim is made. The strict
controlled M5 lifecycle gate above passes without suppressing these warning types.

### M5 local performance evidence

Paired microbenchmark: 10 warmups + 100 samples per scenario/mode, rotating order;
raw baseline, disabled observation, enabled/no-export, enabled/local export.
Wall time excludes injected HTTP/storage wait; CPU is measured separately; flush
is outside timed regions. DB uses real disposable SQLite; knowledge uses real
retrieval with a local repository double. No live provider/network/load claim.

| Scenario | Mode | Median ms | p95 ms | p99 ms | CPU median ms |
|---|---|---:|---:|---:|---:|
| http | baseline | 0.0015 | 0.0021 | 0.0022 | 0.0180 |
| http | disabled | 0.0621 | 0.0747 | 0.0867 | 0.0790 |
| http | none | 0.1302 | 0.1480 | 0.1552 | 0.1460 |
| http | local | 0.2266 | 0.2765 | 0.3070 | 0.2430 |
| database | baseline | 0.2796 | 0.3445 | 0.5360 | 0.1960 |
| database | disabled | 0.4838 | 0.5960 | 0.8190 | 0.3930 |
| database | none | 0.6926 | 0.9082 | 1.0985 | 0.6050 |
| database | local | 0.9795 | 1.0784 | 1.2175 | 0.8870 |
| storage | baseline | 0.0017 | 0.0030 | 0.0033 | 0.0180 |
| storage | disabled | 0.0632 | 0.1074 | 0.1226 | 0.0790 |
| storage | none | 0.1302 | 0.1842 | 0.2288 | 0.1455 |
| storage | local | 0.2296 | 0.3113 | 0.4010 | 0.2460 |
| knowledge | baseline | 0.0460 | 0.0621 | 0.0702 | 0.0460 |
| knowledge | disabled | 0.2882 | 0.3113 | 0.3331 | 0.2885 |
| knowledge | none | 0.5711 | 0.6459 | 0.9104 | 0.5715 |
| knowledge | local | 0.9987 | 1.1257 | 1.2900 | 0.9985 |

Paired local-export minus baseline observer overhead (ms):

| Scenario | Median | p95 | p99 | Local p95 <10ms gate |
|---|---:|---:|---:|---|
| http | 0.2248 | 0.2749 | 0.3048 | PASS |
| database | 0.6820 | 0.7896 | 0.8936 | PASS |
| storage | 0.2278 | 0.3091 | 0.3982 | PASS |
| knowledge | 0.9518 | 1.0608 | 1.2198 | PASS |

1000-scope no-export tracemalloc check: retained delta 2896 bytes;
peak delta 15648 bytes; no live dependency remains.
Post-flush trace queue size = 0 in every mode. Local export snapshots are bounded.
These numbers demonstrate the agreed local observer budget, not the <3% production
CPU target; Cloud Run/provider/platform load and profiling remain unvalidated.

### M5 individually evaluated blocking exit criteria

| # | User blocking criterion | Result | Concrete evidence |
|---|---|---|---|
| 1 | Every real dependency family in scope covered | PASS | CLIENT_OWNERS AST coverage + real boundary tests; M1/M3 exclusions explicit |
| 2 | Power Automate / downstream Graph visibility truthfully separated | PASS | Six gateway ops × seven statuses; no GRAPH_*; explicit gateway origin |
| 3 | All four SQLAlchemy owners covered | PASS | ADK session + case/attachment/knowledge real SQLite integration |
| 4 | DB telemetry leaks no SQL/parameters | PASS | Statement/bound-value/DB-error sentinels absent from traces/logs/metrics |
| 5 | GCS dependencies covered safely | PASS | Both real stores with SDK doubles; upload/download/exists/delete exact counts |
| 6 | Secret Manager remote cache-miss path safe | PASS | Actual cache resolver double: one miss call/span; hit emits none |
| 7 | Knowledge dependencies/stages visible | PASS | Five stages + real metadata DB child + fallback/no-result/cancellation |
| 8 | M3 provider ownership not duplicated | PASS | Three actual fake embedding requests = three usage observations; no HTTP duplicate |
| 9 | Correct M4/M2 parentage | PASS | Tool/phase/root, independent roots, metadata DB and dense M3 child assertions |
| 10 | Exactly one dependency span per real call | PASS | Counted gateway/storage/secret/cursor boundaries; groups do not inflate requests |
| 11 | URL/route sanitization fails closed | PASS | Six malicious URLs × registered/unknown ops; direct poisoned OTLP route denied |
| 12 | Headers/bodies/secrets remain excluded | PASS | Python capture and independent Collector URL/SQL/token/body denial |
| 13 | Blocked-child concurrency visibility | PASS | Two roots/parallel children; real saturated DB wait; dependency/tool/elapsed/restoration |
| 14 | Blocked-child state remains execution-local | PASS | Weak bounded in-memory handles only; no DB/API/schema/SSE change |
| 15 | Retries/pagination/timeouts classified honestly | PASS | Typed connect/read/DB/storage timeouts; 429; actual pages; observed existing retry only |
| 16 | Bounded metrics | PASS | Finite dimensions/units/shapes; no IDs/exemplars; Collector safe sibling test |
| 17 | Telemetry failure cannot replay calls | PASS | Fault-injection invocation counters and original return/exception identity |
| 18 | Side-effect safety | PASS | Actual gateway write/upload/delete/secret/query/commit adapter fault matrix |
| 19 | Cancellation | PASS | Async wait/query/retrieval + actual synchronous GCS offload; no write replay |
| 20 | Local Collector sanitization | PASS | Two native config checks + five loopback runtime variants; malformed OTLP denial |
| 21 | Applicable regressions | PASS | 3708 broad + final 216 focused (overlapping); strict 42-case lifecycle gate |
| 22 | No M6 controls implemented | PASS | Scoped source audit: retry/deadline/watchdog/circuit-breaker policies unchanged |
| 23 | No cloud mutation | PASS | Only local source, fake clients, disposable SQLite and loopback Collector used |

All 15 approved-plan objective gates map to and are satisfied by the table above;
performance/cardinality/privacy/hygiene/UI impact evaluated explicitly. Roadmap M5
blocked-dependency current-child exit PASS. No blocking M5 defect remains known.
Visibility limits above are explicit architecture constraints, not claimed live
validation or silent exit waivers. No downstream Graph/platform state fabricated.

**Ready for Next Milestone: YES** (M5 local gate passed).
**STOP. M6 NOT STARTED; separate user request required.**

---

## M6 — Reliability, Timeouts, Watchdog and Error Control: planning only — 2026-10-08

**M6 — PLANNED — NOT IMPLEMENTED.** Authorization: planning and execution-record
update only. This section supersedes the historical M5 “M6 not started” instruction
for planning, without changing any M0–M5 implementation or acceptance evidence.
M6 implementation = NOT EXECUTED. M6 runtime validation = NOT RUN.
Ready for Next Milestone: **NO**. M7 remains **BLOCKED BY M6**.

### Gate, objective, specifications and evidence boundaries

Gate audit: **PASS for planning**. Matrix and final completion sections record M0
VALIDATED COMPLETE, M1–M5 IMPLEMENTED AND LOCALLY VALIDATED, all applicable local
blocking criteria PASS and readiness YES. Final M5 evidence: 3708 passing affected
regressions; final 216 passing focused M5/Collector cases; strict 42-case dependency
lifecycle gate; compilation, dependency and preservation checks PASS. These are
historical recorded results, not tests rerun in this planning pass. Actual local
M0 contracts, M1 runtime/exporters/Collector, M2 driver/lifecycle, M3 model adapters,
M4 agent/tool adapters and M5 dependency observers exist and were inspected.
No discrepancy with the recorded local gate was found. “COMPLETE” here means the
recorded applicable LOCAL acceptance, never deployed production completion. M1
rollout is explicitly pending separately; it is not a new M6 prerequisite or
permission. Do not convert historical planning NOs into current blocking gates.

Objective: establish bounded runtime enforcement answering whether a turn is
progressing, its blocked child, which deadline expired, whether work actually
stopped, and whether cleanup finished. Preserve conversation/governance semantics,
existing safe degradation, session persistence, rewind and command authority.
No second root lifecycle, replacement Runner, ledger, persistence/API or UI console.

Read authoritative sources: AGENTS.md; .agent/PLANS.md; this execution record;
docs/Telemetry/README.md; 00_MASTER_BUILD_CONTRACT.md; 01_TARGET_ARCHITECTURE.md;
02_TELEMETRY_DATA_CONTRACT.md; 03_INSTRUMENTATION_AND_RUNTIME.md;
04_RELIABILITY_TIMEOUTS_ERRORS.md; 06_SRE_SLOS_METRICS_ALERTS.md;
11_SLOPANOC_INTEGRATION_MAP.md; 12_IMPLEMENTATION_ROADMAP.md;
13_TEST_VALIDATION_ACCEPTANCE.md; 14_CODEX_EXECUTION_GUIDE.md;
15_DEFINITION_OF_DONE.md; 21_SLO_FORMULAS_AND_INITIAL_TARGETS.md;
22_RELEASE_CONFIG_CORRELATION_CI.md. Also inspected privacy/logging contracts
09_SECURITY_PRIVACY_RETENTION_SAMPLING.md and 18_STRUCTURED_LOGGING_STANDARD.md.
Implementation must reconcile optional extensions with these contracts first.

Evidence is static local source inspection, installed dependency source and Git
diff/status review. No settings secret resolver, app startup, model/gateway client,
shared DB, cloud CLI, Terraform, or remote service was invoked. Effective deployed
values are UNKNOWN: inventory below gives source defaults, not production settings.

### Current-state audit / foundations to reuse

- M0: exact Stage/RunStatus/ErrorCode enums, terminal set, finite attribute/error
  allowlists, effective config/read-only ownership and privacy/cardinality gates.
- M1: runtime, JSON formatter, safe log/event schemas, unsampled metric export
  validation and independent pinned Collector sanitization. Its exporter retries,
  queues and shutdown remain M1-owned; M6 registers its own bounded producers.
- M2: ChatService.execute_turn_events creates trusted run identity, TurnTrace and
  one independently owned _drive task. _drive holds session lock, drives generator,
  performs cleanup/release, then finish; completion_backstop covers cancellation
  before first task instruction. _background_turns/_run_tasks remain task owners.
  TurnTrace.finish is idempotent and terminal immutable; ActiveRuns weak cache,
  128-event timeline and snapshot are execution-local. No durable run repository.
- M3: ObservedModel, embedding_request, ModelOperation/physical attempts, existing
  content protection, first-token/usage observation and actual request ownership.
  Shared lazy clients remain shared; no global per-turn timeout mutation on them.
- M4: ObservedAgent, per-flow pinned ADK dispatch, tool scope, explicit direct-call
  wrappers and parent/role attribution. Enforce inside these boundaries, without
  adding another semantic span or moving governance callbacks out of their order.
- M5: dependency_scope, typed classification before SafeError collapse, observed
  pools/factories, storage/secret adapters, optional vendor-free retrieval observer,
  weak concurrent LiveDependency descriptors and child restoration. SDK spans end
  on actual worker completion. Observation guards do not enforce deadlines.
- Existing architecture facts: nested AgentTool paths normally run synchronous
  FunctionTools on the event loop. Only deterministic read-continuation Runner
  enables ADK ToolThreadPoolConfig. A watchdog cannot preempt an event loop blocked
  by requests/credential/secret I/O; moving identified blocking I/O is REQUIRED.
  Contributors are cancelled in several branches without awaited completion and
  have no encompassing task nursery. Generator close and cleanup DB writes can hang.
  TurnTrace.last_progress_at is wall time today; adding monotonic fields is required.
  Its event() currently changes stage/progress for every event, so heartbeat/stall
  emission must use a separate observation path rather than calling it as progress.

### Complete runtime timeout and wait inventory

Abbreviations: C = caller wait; U = underlying work. “None” means no application
cap in inspected source, not proof that a remote service has no independent limit.
SDK defaults are from installed local source, not live provider behavior. Every
row states owner/mechanism, exception/outcome, cancellation, retries and cleanup.

| Boundary / source owner | Current timeout / mechanism and raised outcome | Does cancellation stop U? | Existing retry owner / behavior | Current cleanup and M6 gap |
|---|---|---|---|---|
| Accepted whole turn — api/chat_service.py execute_turn_events/_drive | None; create_task + async iteration, including lock wait/finalization | Cooperative async cancellation; synchronous work can continue; event-loop sync calls delay even C cancellation | No whole-turn replay; presentation/remediation calls below | Aclosing + async lock exit + finish/backstop; awaits not bounded |
| Session ownership preflight — api/app.py SSE and chat_service.run_turn | None before canonical root; get_session wait | Async DB cancellation is driver-dependent; not confirmed remote abort | No explicit app retry | DB context cleanup; plan bounded preflight without new root/SLI admission |
| Session lock — api/execution_coordinator.py, ApiSessionService.lock_for | None; asyncio.Lock.acquire via async with; unbounded process-local lock map | Cancelled waiter normally removed; holder releases only on actual context exit | None | Sync release is immediate, but body/generator cleanup may hang; no distributed locking |
| Session load/create/list/internal sessions — api/session_service.py + installed ADK DatabaseSessionService | None at app boundary; pool/driver defaults below | Cooperative async wait; commit/query cancellation is not confirmation of remote outcome | No explicit app replay | Service/session contexts; internal delete awaits unbounded |
| Planning/orchestration/Team Manager — chat_service.py Runner, agents/team_manager | None; async stream + callbacks; ADK max_llm_calls=500 is count, not duration | Async stream can cancel; sync tool blocks loop; cleanup can delay | Existing distinct presentation/remediation policies; not transport retry | Merge/Runner teardown + domain state cleanup; no stage/agent budget |
| Specialists — observability/adk_adapter.py ObservedAgent, TAE agent_tool.py, IM/PM/AOE | None; iterate then iterator.aclose | Cooperative iterator cancellation; downstream threads not killed | TAE structured regeneration, IM compliance below | M4 finish once, but aclose awaits unbounded |
| Model generation — observability/model_adapter.py and model_provider.py; shared Gemini | Inert config.model_timeout_seconds=120, NOT enforced. No explicit per-request app timeout; GenAI HttpOptions.timeout=None maps transport timeout to None | Await cancellation may close local stream; does not prove remote generation stopped or unbilled | HttpOptions.retry_options=None -> one Tenacity attempt by default; configured options default 5 total, 1/2/4/8s + jitter, cap 60s. aiohttp has internal connection retry; auth transport varies | Stream/response close awaits unbounded; one M3 op/attempt observation preserved |
| Embeddings/dense model + ingestion image model — knowledge/embeddings/service.py, tools/knowledge/dense_similarity.py, knowledge_ingestion/gemini_image_interpreter.py | No separate app provider cap; dense caller 10s below; clients lazy | Local async cancellation; remote provider termination unknown | Same SDK-specific model policy; no extra app transport retry | M3 closes attempts; cache writes must reject late results after enclosing budget |
| Tools/direct calls — observability/adk_adapter.py call_tool, tool_instrumentation.py | None; sync FunctionTool default can block main loop | Coroutine cancellation cannot interrupt inline sync or already-running executor | No generic tool retry; specific business recovery only | M4 finish/restore; add enforcement without duplicate span |
| Six Power Automate gateway ops — gateway/power_automate_client.py | Settings.request_timeout_seconds default 10s, requests.post scalar connect/read inactivity timeout; NOT total operation deadline. Secret lookup before request timer | Inline sync blocks loop; offloaded call survives C cancellation | No application POST retry; fresh requestId is correlation, NOT proven remote idempotency. Gateway/downstream retry unknown | requests response handling; Timeout -> SafeError run_failure, rate limit -> rate_limited; M5 retains TOOL_TIMEOUT/TOOL_ERROR origin gateway |
| Teams pagination/hosted content/member reads — tools/teams, api/source_reference.py | Per gateway request 10s, no whole page-loop/contributor cap | Offloaded member fetch survives asyncio.to_thread cancellation; nested inline calls block loop | Pagination is additional real requests, not retry | Contributors may be cancelled but not awaited; cap pagination by existing parent, preserve counts |
| DB acquisition — four engines: session_service, cases/db.py, attachments/repository.py, knowledge/repository/sqlalchemy.py | No app acquisition cap; AsyncAdaptedQueuePool defaults 30s wait, pool_size 5 + max_overflow 10; StaticPool in-memory has no queue wait cap. CONNECT/pre_ping also included | Async pool wait cancellation cooperative; connection setup/driver cleanup must be tested | Knowledge pre_ping/recycle=300s; SQLAlchemy reconnect/pre_ping behavior SDK-owned, not statement replay | M5 public pool/connect observation; default pool wait may consume parent; acquisition must include setup/pre_ping |
| DB connect/query — same four owners | No app query cap; asyncpg.connect default 60s, command_timeout=None; SQLite driver busy timeout default 5s, NOT statement deadline; configured URL args may differ | Cancellation interrupts awaiting driver where supported; aiosqlite worker may still run; remote query/transaction abort requires confirmation | No generic write/query retry; knowledge read retry only | Async session/transaction exit; rollback/close may await; classify acquisition/query separately |
| Persistence/commit/final answer/provenance/title/cleanup delta — chat_service.py, session_service.py, case/attachment/knowledge repos | None at app operation; M5 sees COMMIT/ROLLBACK and query failures | Cancel during commit means outcome potentially unknown; never infer rollback from cancelled C | Writes not replayed by app | Reload and persist cleanup + finalize activity occur in Runner finally and can delay cancellation/lock release; bounded recovery needed |
| Whole knowledge retrieval — knowledge/retrieval/service.py, tools/knowledge/runtime.py | Whole retrieval none; config knowledge cap 10s inert. Metadata DB, sparse/applicability/fusion synchronous compute unbounded by time | Async metadata/dense cancel; CPU loops cannot preempt without yield; bound existing corpus limits and check budget at stages | Three repository read methods retry once; dense fallback to sparse already exists | Observer restoration; preserve evidence eligibility/ranking and no-result semantics |
| Dense retrieval — service._dense_similarities_impl | Settings.knowledge_dense_timeout_seconds default 10s; asyncio.wait_for | Cancellation can wait beyond timeout if callee cleanup/suppression; remote provider unknown | No extra dense retry; timeout returns unavailable:timeout and uses lexical fallback | Existing legitimate degradation; parent expiry MUST propagate instead of being swallowed as dense-only fallback |
| GCS chat upload/download/delete/exists — attachments/storage.py + api/attachment_service.py | No explicit app SDK timeout; installed blob default 60s per request; default api_core retry window 120s (1s initial, 60s max, multiplier 2). Upload may include multipart/resumable substeps | Starlette/AnyIO run_in_threadpool does not kill worker; default abandon_on_cancel=False shields AnyIO cancellation scopes, raw Task.cancel behavior requires tests | SDK DEFAULT_RETRY; upload/delete are not automatically safe merely because SDK has retries | Async orchestrator DB updates and compensating delete; SDK span closes at U completion; cap transport + retry window |
| GCS knowledge artifacts — knowledge_ingestion/artifact_storage.py | Same 60s/request + 120s retry default; exists then upload has no aggregate deadline | Sync caller blocks or offloaded worker survives | SDK-owned; exists-then-upload is not an atomic generation precondition/idempotency proof | No thread kill; no conditional exists hit upload; isolate ingestion budgets from turn |
| Secret Manager cache miss — config/settings.py _cached_secret_value | No explicit app cap; installed access_secret_version default 60s/RPC + 60s retry window; initial 2s/max 60s/multiplier 2, ResourceExhausted/ServiceUnavailable | Sync may block loop; offload continues on C cancel; cache hit no remote wait | GAPIC retry; process lru_cache; concurrent misses may each fetch | Client creation/credentials outside M5 RPC; bound miss including client init without logging secret/name |
| Lazy model/GCS/secret client construction and credential refresh | No app cap; transport/ADC/environment-specific behavior unknown | Synchronous setup can block loop; threads not terminated | Provider/auth transport internal; do not claim visibility | Initialize through bounded worker where synchronous; keep singleton construction safe, no new per-turn clients |
| ADK event producer/merge — chat_service._merge_adk_and_activity_events | asyncio.Queue() unbounded; put no capacity wait; queue.get/asyncio.wait FIRST_COMPLETED no time cap | Producer cancelled in finally, then awaited without bound | None; producer resumes ONE persistent generator task/context | Preserve same-task attach/detach; bounded producer backpressure + independent completion/error signal required |
| Business/SSE turn relay — execute_turn_events queue; app StreamingResponse | asyncio.Queue() unbounded; queue.get and ASGI send no app deadline | Relay close never cancels backend by itself; slow send can leave HTTP stream blocked | None; cannot replay live business events blindly | signal_done sentinel currently put_nowait; root survives disconnect; bounded relay/delivery needed |
| Activity projection — api/activity_queue.py | Queue maxsize=16, put_nowait; full silently drops optional activity; no timed wait at producer | Not a business task; copied run context exists in workers | None | discard channel; plan visible saturation and loop-safe worker reports, no business event loss |
| Background tasks — _drive, one ADK drain, per-item queue getters, contributor tasks; ADK tool tasks | No task lifetime cap; strong root task registry, local merge task refs; contributor refs replaced | cancel + await for merge; contributor branches only cancel; actual workers survive | ADK per-call parallelism; no new background business scheduler | Track children under owner; bounded joins, collect exceptions, no orphan stale context |
| Executors — asyncio.to_thread, ADK read-continuation, Starlette pool | Caller timeout absent; asyncio default worker count implementation-dependent with unbounded submission queue; ADK default max_workers=4, unbounded submit queue; AnyIO default limiter 40, waiting admission not deadline-bound | Already-running Python threads cannot be killed safely | Workers do not add retries; underlying SDK may | No detached-work registry or per-operation admission; capacity token must remain held until actual U completion |
| Trusted approved write route — api/execution_service.py + tools/teams/execute_write.py | No outer action/lock/persist deadline; blocking gateway offloaded through Starlette | Worker retains LIVE tool_context.state and calls consume_proposal AFTER remote return; unsafe to detach unchanged | No automated replay; error leaves approved proposal; run_failure currently retryable public code is not permission to retry a write | Existing unconfirmed outcome handling must survive; split transport from owner mutations; do not introduce new operational state-changing adapters |
| Authorized diagnostic read adapter — operations/execution.py, api/operational_execution_service.py | No adapter timeout; async registered adapter; none -> execution_unavailable; exception -> failed | Depends on registered adapter; remote abort unproven | No automatic execution retry | Keep signed authority/policy/target/source checks; timeout observation cannot become evidence or command authority |
| Startup warmup — config/model_warmup.py | Validated Settings model_warmup_timeout_seconds=30s; asyncio.wait_for | Async cancellation, cleanup can exceed cap; no actual provider termination proof | No app warmup retry; model transport as above | Best-effort catch timeout, independent warmup attribution; not a user-turn denominator |
| OTel export/shutdown — observability/config.py, runtime.py, exporters/queue.py; app lifespan | Export cap 2s, shutdown aggregate 5s, batch interval 5s, metric interval 30s; queues 2048/batch256 | M1 bounded worker shutdown, may leave delivery unconfirmed | M1 transport policy; MUST NOT be wrapped in turn retries | M1 health/drops separate; teardown offloaded in lifespan; unchanged business/FinOps ownership |
| Process-local synchronous metadata locks — turn/context/activity-related registries, retrieval/runtime, perf, observability | RLock/Lock without wait timeout; short critical sections intended, no network allowed within them | Cannot asynchronously interrupt held sync lock | None | Audit critical sections remain short; never export under held state lock or await there |

### Explicit hierarchy and central typed configuration

Doc 04 specifies categories, hierarchy and watchdog semantics but **NO numeric
hard/soft/stall timeout values**. Doc 21 p95 latency targets (8/20/30/45s) are NOT
hard timeout values. Existing M0 config defaults are inert scaffolding, not newly
approved production policy. All numbers in this proposal are **PROVISIONAL**.

Settings remains sole environment reader; extend existing ObservabilityConfig and
expose one immutable typed reliability projection through Settings. Enforcement
must remain active independently of OTel/export/sampling flags. Reject nonfinite,
zero/negative durations and invalid capacities; never silently disable protection.
Existing MODEL/KNOWLEDGE/DATABASE/STORAGE/WATCHDOG/HEARTBEAT suffixes are reused;
add TURN, SESSION_LOAD, SESSION_LOCK, PLANNING, ORCHESTRATION, AGENT, TOOL, GATEWAY,
DATABASE_ACQUIRE, DATABASE_QUERY, PERSISTENCE, CLEANUP, SSE_DELIVERY and
QUEUE_WAIT timeout suffixes consistently under SLOPANOC_. Existing GRAPH_TIMEOUT
is a compatibility alias/fallback for GATEWAY_TIMEOUT only; telemetry origin stays
Power Automate. Canonical-specific value takes precedence, legacy request/dense
settings fallback, then typed default. Record conflicting alias health metadata,
never export secret-bearing settings. DATABASE_TIMEOUT remains legacy aggregate
fallback; separate acquire/query caps still clamp to the same parent. Shared config
version hash includes sanitized reliability values, never endpoint/credentials.

| Provisional hard cap / threshold | Proposal | Rationale |
|---|---:|---|
| Accepted-to-terminal turn maximum | 180s | Above existing 120s inert model cap and complex-turn p95 target; finite initial ceiling, baseline tuning later |
| Cleanup reserve / aggregate cap | 5s | Bounded reserve including generator close/DB recovery/task joins; NOT 5s per cleanup item |
| Session lock / session load or preflight | 10s / 10s | Waiting contention/load cannot silently consume full turn |
| Planning / orchestration / agent | 30s / 150s / 150s | Multi-model orchestration bounded; all reduced by remaining parent |
| Model logical operation | 120s | Preserve scaffold cap initially, including retries/stream lifetime, not each chunk |
| Tool / gateway | 30s / 10s | Tool aggregation larger than individual gateway request; preserve current gateway default |
| DB aggregate / acquisition / query | 30s / 5s / 15s | Fast saturation diagnosis, separate wait/query; commit remains persistence-bound |
| Knowledge whole / dense child | 15s / 10s | Preserve sparse fallback on dense-only timeout while bounding metadata+ranking |
| Storage / Secret Manager | 30s / 10s | Below SDK retry windows; secret resolution cannot dominate turn |
| Persistence | 10s | Aggregate finalization write budget; cleanup recovery uses only cleanup reserve |
| SSE delivery / business queue put wait | 10s / 1s | Bound connected slow consumer; do not treat queue idle as execution failure |
| Stall / heartbeat cadence | 30s / 5s | Preserve inert config proposals; stall checks at coarse <=1s maximum tick |
| Soft deadline warning | 80% of owning cap elapsed | One warning per operation; metadata only, no extra execution deadline |
| Worker running / waiting admission / queues | 8 workers + 16 pending; relay256; ADK256 | Explicit per-process resource bound; provisional, measure before deployment |

Secret-specific cap/capacity names must be registered in config in implementation;
not magical literals. Model/provider server deadlines receive remaining values in
correct SDK units (GenAI milliseconds); timeout=0 must never mean unlimited.
Caps can exceed an individual parent configured cap safely only because effective
child deadline is always clamped; validate the runtime effective hierarchy.

Define `D_total = monotonic_at_acceptance + turn_cap`,
`D_work = D_total - cleanup_reserve`, and for each child
`D_child = min(D_parent, monotonic_now + category_cap)`.
A stage/agent/model stream receives ONE deadline for its complete lifetime; do
not re-arm at every event/token/page/retry. Acquisition, admission, retry sleeps,
credential setup and transport all consume the same child budget. Return
`max(0, D_child - now)`; at exactly zero admit no work and raise one typed
DeadlineExceeded carrying finite owner/category + existing ErrorCode, no payload.
A turn with 45s remaining cannot perform sequential 30+30+30s calls: later children
receive only what remains. Monotonic values never cross process/API boundaries.

Cleanup starts at `min(D_total, now + cleanup_cap)` on EVERY exit. Reserve is inside
accepted-to-terminal maximum to match doc 21, not “180s + unlimited cleanup”.
Execution timeout at D_work yields timeout cause then fenced cleanup, terminal
at/before D_total subject to event-loop scheduling; separately measure deadline
overshoot. Never claim a literal real-time bound if supported code blocks the loop.
Normal business persistence remains work; limited rollback/clearing stale state
can use the cleanup reserve. No new successful work starts in cleanup mode.

Deadline ContextVar references immutable budgets and one shared revocable execution
lease per turn. Binding/reset in originating task preserves ADK/OTel contexts;
parallel child caps remain independent. Enforcement resides OUTSIDE existing
best-effort `guarded`/`notify` telemetry wrappers. Cancellation for a known deadline
must be translated to typed timeout at that owning boundary; arbitrary user
CancelledError remains cancellation, never a guessed timeout. Earliest expired
ancestor wins, deterministic ties favor ancestor, explicit pre-existing cancellation
wins if observed first; terminal outcomes cannot change after finish.

### Watchdog, real progress, heartbeat and STALLED

One lightweight watchdog per canonical driver, strongly tracked and cancelled/joined
before root finish. Same controller also schedules absolute deadlines; it never
invokes agents/tools or chooses evidence. Run protection does not depend on weak
ActiveRuns admission or an enabled tracing SDK. Inspect snapshot under short lock,
then emit outside lock. Wake on material progress or nearest deadline/stall/
heartbeat timer, coalescing rapid updates and at most one coarse check per second
between changes; no token-frequency polling or per-child watchdog tasks.

Add monotonic start/stage-start/last-real-progress, immutable deadline and counters
on the existing lifecycle/progress view, plus bounded live agent/tool descriptors
from M4 (today M5 descriptors include agent/tool only when a dependency exists).
Reuse M5 concurrent blocked-child list, operation, parent tool/span and elapsed;
show all active children when concurrent, with deterministic current-child
selection, never falsely report only one blocked child as all work.

Real progress: actual phase transition, agent transition, provider output observed
by M3 (content discarded), tool/dependency completion/activity, page received,
persistence transition/completion. A socket alive, retry sleep, repeated identical
stage label, watchdog tick, heartbeat, warning or exporter activity is NOT progress.
Starts count once; an operation continuously in-flight does not repeatedly advance
last-progress. Coalesce model progress notifications without losing true freshness.

RUNNING -> STALLED emits Stage.RUN_STALLED/ErrorCode.TURN_STALLED once per episode;
STALLED is NONTERMINAL and does not close any span/cancel work. Genuine progress
resumes RUNNING and records stall duration/resume once. A deadline can expire from
either RUNNING or STALLED; M2 alone terminalizes TIMEOUT. Do not overwrite current
business stage with heartbeat/stall/cleanup event or make heartbeat clear stall.
The initial stall threshold may exceed a short child's cap; such operations time
out without first stalling, legitimately. Long child test uses stall < child cap.

Use internal Stage.HEARTBEAT for safe liveness observation WITHOUT invoking
TurnTrace.event's progress path. For SSE, add the doc-defined `processing.heartbeat`
envelope carrying trusted run ID, mapped PUBLIC stage and elapsed_ms only, every
5s while connected/active. Include no internal dependency/agent/tool details.
Minimal backend+TypeScript parser/type compatibility belongs to M6; no new screen
or diagnostics rendering. Heartbeat uses a coalesced control channel so it cannot
fill/delay required business events, and stops on terminal/disconnect. It is
backend-alive information, never proof of business progress or browser receipt.

Telemetry failure is best-effort with M1 safe health visibility; business proceeds
when enforcement is intact. Controller scheduling/calculation failure is NOT
silently swallowed: static health failure, retain independent driver-level deadline
backstop, bounded safe failure if protection cannot be restored. Config failure
fails validation before accepting execution. Test enforcement with SDK disabled
and injected watchdog/log/export failures.

### Synchronous containment, local mutation fencing and late completion

Use a bounded per-process executor with deadline-aware admission and bounded
pending requests, not unbounded submissions to default/ADK/AnyIO pools. Concurrency
permits remain held until the ACTUAL future finishes, even after caller timeout;
otherwise repeated timed-out callers create unbounded underlying work. Removal of
queued not-started future is permitted; running thread kill is NEVER permitted.
Each entry tracks finite component/category, actual start/end, caller disposition,
lease generation and optional trusted trace linkage. No payload in diagnostic entry;
only SDK execution owns necessary arguments for its finite lifetime.

Separate caller disposition (completed/timeout/cancelled), underlying disposition
(not_started/running/terminated/unknown) and side-effect outcome. Never mark
`dependency cancelled` when only C stopped waiting. Existing M5 operation span ends
when actual U completes; deadline observation records caller timeout on its existing
scope without ending that worker's span early or opening a replacement. If parent
has ended, child may finish later honestly: parent ending does not kill the child.
Late result has no root/progress mutation and cannot add another terminal event.
A late completion health/log event uses explicitly captured safe linkage with empty
ambient execution/OTel context; no reattachment of stale run/agent/tool for new work.

Do NOT offload every AgentTool to a new event loop. Narrow M4 per-flow dispatch
adapter to known blocking sync FunctionTools or their transport boundaries only;
keep async AgentTool scheduling and governance callbacks on the owning loop.
Reuse pinned shape/version validation; no class/global patch or SDK-source edits.
Default offload and deterministic read-continuation must use the same containment
policy; replacing only asyncio.to_thread(contributors) is insufficient.

Workers must not retain live canonical session state, mutable ToolContext or
run mailboxes on any path that can outlive owner. Audit concrete sync read tools:
use isolated invocation state for worker-local data and apply bounded state/result
changes only on owner-loop after lease check. Existing authority/provenance and
callbacks run in their original order with real results. Fence run-scoped content/
evidence/activity writes after cleanup; marshal optional activity to owning loop
(thread-safe scheduling, no direct asyncio.Queue mutation from workers). Do not
change generic knowledge package's vendor-free boundary: inject budget checks/
execution hooks from concrete runtime, separate from optional observation hooks.

CRITICAL write adaptation: execute_write currently consumes proposal inside worker
against live state. Preserve normalize -> authorize -> gateway -> parse -> consume
semantics, but run normalize/authorize/consume on owner, offload ONLY gateway
transport with immutable authorized input. Owner revalidates current approved
identity before applying confirmed result while still holding session lock; lease
expiry forbids consume/persist/emit from late completion. No new approval shortcut,
state-changing operational adapter or auto-retry. Make this narrow separation in
both trusted HTTP continuation and model tool facade; original sync entrypoints
remain compatible for non-async consumers/tests. Callback business state never
comes from telemetry. Confirmed remote success arriving late remains a recorded
late confirmed result for safe reconciliation, not a fabricated failed action or
new chat response. Reconciliation UI/durable execution receipts are not built here.

At deadline/cancel seal lease FIRST, cancel owned async children, discard queued
worker submissions, restore/close local spans/contexts in their originating tasks,
and await mutators within aggregate cleanup. Transport-only workers may detach
with strong bounded tracking/late-result discard and permit retention. They cannot
consume a proposal, persist cache/evidence/session state or emit SSE after sealing.
Known async DB/ADK mutators must unwind before release. Do not “solve” cleanup by
abandoning arbitrary generator/session mutators and releasing their lock. If a
supported mutator cannot be stopped/fenced within reserve, this is a blocking M6
implementation defect: no completion claim, safe health failure and fail-closed
session admission while repairing the boundary. Indefinite quarantine is NOT an
accepted substitute for the lock/no-indefinite-state criterion. No arbitrary
thread/coroutine termination or hypothetical production preemption claim.

Background tasks: driver canonical owner; watchdog controlled auxiliary; single ADK
drain and queue getters scoped children; contributors scoped best-effort enrichment,
never orphaned; ADK invocation/tool tasks tracked at adapter boundaries; retry
internal-session deletion and cleanup owned with same budget. Only pure transport
workers intentionally outlive turn, with cleared ambient context, bounded tracking
and no business mutation capability. Executor shutdown uses bounded drain/health;
no waiting forever for worker threads in a turn cleanup or app shutdown.

### Side effects, command uncertainty, retries and error ownership

Outcome classification is an orthogonal finite reliability field:
CONFIRMED_SUCCESS / CONFIRMED_FAILURE / NOT_STARTED / OUTCOME_UNKNOWN.
Not-started requires proof (deadline before admission/egress); once remote egress
may have occurred, timeout/cancellation/network loss means OUTCOME_UNKNOWN absent
trustworthy confirmation. Unknown is NOT a new RunStatus or command authority.
Apply to approved Teams writes, uploads/deletes and ambiguous commit/persistence;
local cancellation does not prove remote rollback. Confirmed response must be
interpreted using real dependency semantics; gateway 429 alone cannot universally
prove a downstream action never happened without a trustworthy gateway guarantee.
Current execution_service comment assumes such a guarantee; M6 must retain safe
uncertainty when that assumption is not verified, without inventing Graph status.
Telemetry never selects evidence, grants approval or causes duplicate execution.

Retry inventory / owner:
- Gateway app requests.post: one attempt, no retry loop; remote retries unknown.
- GenAI SDK: one default Tenacity attempt when retry_options=None; explicitly
  configured options can allow five total; aiohttp connection/auth attempts separate.
  M3 physical attempt observations remain the source for actual submitted calls.
- GCS SDK: current default retry potentially includes uploads/deletes without
  generation precondition. Pass a bounded safe retry policy per operation; reads
  retry-safe, ambiguous non-idempotent writes not retried automatically. Do not
  present exists-then-upload or random requestId as idempotency guarantee.
- Secret Manager: GAPIC read retry, cap retry window and per-RPC timeout to budget.
- DB: knowledge get/list_versions/list_all catch InterfaceError/DBAPIError then
  retry once with new session (max two); SQLAlchemy pre_ping/reconnect separate.
  No write replay. Remaining time gates the existing read retry only.
- Presentation: chat_service one retry, direct_read_fast_path one retry, IM
  provenance_compliance one retry with distinct no-recursive agent. They rerun
  presentation/compliance, NOT trusted operational read or write.
- TAE structured_output RegenerationBudget one regeneration and action_id_recovery
  one tools-disabled recovery; source-requirements declaration and forced governed
  completion are bounded recovery invocations, not generic transport retries.
- Startup warmup no outer retry; orchestration does not create new retry policy.

One retry owner per physical dependency operation. M6 adds no general retry wrapper
around SDK retries or business recovery; typed policy records existing max attempts,
retryable codes and proven idempotency. If remaining budget cannot cover backoff +
configured minimum useful attempt window, decline retry and report retry exhausted
without starting it; proposed minimum window=1s PROVISIONAL configurable, no success
guarantee. Clamp waits to deadline, check before physical submit, include admission/
cleanup reserve. Preserve valid tools-disabled business recovery, fail closed when
its budget is exhausted. Per-request SDK option overrides only, no modification of
shared client defaults while parallel turns run. If SDK retry hooks cannot bound
physical submits/backoff, use supported explicit retry_options disabling inner
retry and one reviewed equivalent owner for previously safe attempts; never
silently multiply or remove retries. No breaker initially: docs say “consider” /
“only where justified”, and local audit supplies no outage evidence. Revisit only
with evidence; no breaker in command execution or future milestone functionality.

Deterministic taxonomy reuses M0, never parses error strings:

| Reliability fact | Canonical code / classification |
|---|---|
| Turn budget | TURN_TIMEOUT with expired category/owner; canonical root TIMEOUT once |
| Planning budget | PLANNING_TIMEOUT; root TIMEOUT if propagated, ancestor cause retained |
| Model timeout / 429 / auth / provider | MODEL_TIMEOUT / MODEL_RATE_LIMIT / MODEL_AUTH_ERROR / MODEL_PROVIDER_ERROR |
| Tool or gateway timeout / error | TOOL_TIMEOUT / TOOL_ERROR; gateway origin, no inferred GRAPH_* |
| DB timeout / connect / query / persist | DATABASE_TIMEOUT / DATABASE_CONNECTION_ERROR / DATABASE_QUERY_ERROR / DATABASE_PERSISTENCE_ERROR |
| Knowledge / storage | KNOWLEDGE_TIMEOUT or KNOWLEDGE_PROVIDER_ERROR; STORAGE_TIMEOUT or STORAGE_ERROR |
| Specialist failure | SPECIALIST_FAILED; agent-cap timeout uses TURN_TIMEOUT + agent category (no unregistered AGENT_TIMEOUT) |
| Explicit cancellation | TURN_CANCELLED, canonical CANCELLED; preserve BaseException propagation |
| Stall | TURN_STALLED + nonterminal STALLED event/condition |
| Unexpected internal runtime/controller error | Root FAILED; safe public internal_error; optional canonical error_code absent if no applicable M0 code; finite internal_failure/controller_failure reason |
| Cleanup failed / timed out | Secondary cleanup outcome failed/timeout; original root cause retained; deadline uses TURN_TIMEOUT when terminal deadline exceeded, DB recovery uses DATABASE_PERSISTENCE_ERROR if applicable |
| Ambiguous side effect | OUTCOME_UNKNOWN finite outcome field alongside applicable TOOL_TIMEOUT/STORAGE_TIMEOUT/DATABASE_TIMEOUT etc.; never invent ErrorCode.OUTCOME_UNKNOWN |
| Relay disconnected / missing wire completion | SSE_DISCONNECTED / SSE_COMPLETION_MISMATCH; separate delivery outcome, not automatic backend FAILED |

Extend bounded reliability enums/metadata in same contract owner for cleanup,
controller health, deadlines, caller/U and outcome semantics; no free-form labels.
No new ErrorCode unless normative docs explicitly require it. Optional fields are
compatible schema-v1 extensions if proven by tests; breaking wire/schema changes
require proper versioning, not a silent bump or migration in this planning pass.
Keep detailed exception ownership at root for unexpected execution failures, or
concrete dependency for original provider failure; child layers attach safe code/
origin and propagate, not repeated stacks. No raw exception text/locals/SQL/URL in
export; production formatter deliberately drops exc_info. Preserve a detailed
safe diagnostic (bounded type/category + static frame identifiers only if approved
by sanitizer), not an unredacted stack trace. Cleanup failure logs once per owner.

### Cleanup, locks and persistence safety

Separate mandatory synchronous finalizers (release lock after mutators resolved,
reset ContextVars, seal mailboxes, clear blocked children/end spans) from bounded
async teardown (generator/response close, rollback, reload/cleanup deltas, joins).
Cleanup cap shared across all items, shield only within that cap and keep strong
task handles; naive wait_for can hang waiting for cancellation suppression and is
not by itself a hard cleanup proof. Owner handles expired work cancellation once;
cleanup children are budgeted independently inside reserved cap and never repeated
by another timer. Record cleanup started/completed/failed/timeout and actual duration.
Success cleanup failure cannot be silently relabelled successful: preserve existing
valid persisted answer/wire result as separate facts, mark required finalization
failure at M2 unless already terminal. Never emit second run.completed/message.

Lock acquire is deadline-bound but release requires actual successful acquire flag;
cancelled/timed-out waiter never releases another turn's lock. _drive remains lock
holder, generator cleanup occurs in that task/context, and finish/backstop happens
after release/mutation ownership resolved. Test a second same-session turn/approval/
rewind can proceed after every failure/cancel case. Process-local serialization is
an existing limitation; M6 does not invent a distributed lock/M7 DB active state.
Do not change session persistence revision checks or append ordering. Cancellation
during commit may leave durable outcome unknown; no blind retry/compensating replay.
Cleanup data needed by later turn follows existing stale-envelope clearing behavior;
if recovery write cannot finish, record safe failure, do not announce a new answer.

### SSE, queues, slow clients and delivery separation

Retain independent background backend task. Client transport disconnect closes only
relay, not backend failure/cancellation. Explicit cancel endpoint still cancels
backend for exact trusted run; sync run_turn consumes identical business pipeline.
Arm execution timers from acceptance even while waiting for lock or absent consumer.

Provisional queues: ADK256 and relay256, plus byte accounting (relay pending budget
4MiB PROVISIONAL; configurable) so large final messages cannot defeat count limits.
Business events preserve ordering and required payloads; do not truncate/drop them
to meet queue budget. Per-message size handled by existing content limits; overflow
of byte budget is explicit delivery failure, not silently shortened response.

ADK producer uses bounded puts with remaining parent/queue cap; full queue fails the
turn safely after cancellation/cleanup, never silently loses function/tool results.
Completion/error/cancellation uses a separate Event/future/control slot, not sentinel
put to a full queue. Preserve one persistent task driving generator for entire
lifetime, including aclose; do not introduce per-item tasks driving the generator.

Relay full/slow delivery has its own delivery timeout: mark stream incomplete /
SSE_COMPLETION_MISMATCH or disconnected, close/detach delivery, continue backend to
persist/completion under original budget. Explicit error control signal is separate
from required event queue; attempt terminal failure when transport writable. If it
cannot be delivered, connection closes and existing frontend incomplete-stream
handling fires. Once explicitly detached, backend no longer allocates unread SSE
payloads; this is declared delivery failure, not silent dropping of connected
business events. Final answer remains recoverable through EXISTING session history
when persistence succeeded; do not fabricate replay/resume or new M7 endpoint.
Relay-close detection must cover producer overflow and ASGI send timeout even when
modern StreamingResponse does not call aclose promptly; minimal existing SSE route
wrapper bounds send, observes disconnect and deterministic delivery detach. Sync
consumer surfaces explicit delivery failure, never returns empty successful text.

Activity queue max16 remains best-effort OPTIONAL presentation; saturation increments
safe metric/dropped count and may coalesce identical state, never changes business
outcome. Heartbeat single-slot/coalesced control lane cannot starve required events.
All queue gets wake on producer terminal independent of queue capacity; idle queues
wait until nearest real execution/delivery deadline, not repeated fake progress.
M1 telemetry/export queues are unchanged except registration of M6 safe outputs.

### Reliability metrics, safe events and execution-local diagnostics

Use existing OTel scope and naming convention, units `1`/`s`; doc 06 underscore
names are presentation examples, not a parallel SDK instrument family. Extend
finite metric registry, Python safe_point exporter and BOTH Collector configs in
same change so new metrics/events are not dropped by M1/M5 exact-name sanitization.
New proposed instrument names (register in doc 02 at implementation):

- Counters: slopanoc.turn.timeouts, slopanoc.stage.timeouts,
  slopanoc.turn.stalls, slopanoc.turn.cancellations,
  slopanoc.cleanup.timeouts, slopanoc.cleanup.failures,
  slopanoc.work.late_completions, slopanoc.work.detached,
  slopanoc.executor.saturation, slopanoc.queue.saturation,
  slopanoc.reliability.retries, slopanoc.reliability.retry_exhausted,
  slopanoc.reliability.controller_failures.
- Histograms: slopanoc.turn.stall_duration, slopanoc.cleanup.duration,
  slopanoc.session.lock_wait, slopanoc.reliability.deadline_overshoot.
- Gauges: slopanoc.work.detached_active, slopanoc.executor.active,
  slopanoc.executor.pending, slopanoc.queue.depth (aggregate by queue category,
  not per-run; never expose event payloads). Orphan count should remain zero;
  intentionally detached transport is a separate bounded category.

All unsampled, no exemplars/IDs/content; finite stage (M0), component/operation
category (registered reliability enums), status (M0), environment (configured)
only. Field names and combinations are explicitly bounded in metric sanitizer.
Exactly one canonical turn counter at M2 finish, stage timeout at actual expired
owner, stall once per episode. M3/M4/M5 existing request/retry/timeout metrics remain
owned there; M6 retry-admission/controller metrics do not recount physical attempts
or claim hidden retries. No dashboards, SLI aggregation/alerts or M9 rollout here.

Register bounded OperationalEvent entries for progress resumed, deadline nearing/
exceeded, cleanup started/completed/failed/timeout, late completion and controller
failure. Stage.RUN_STALLED and Stage.HEARTBEAT already exist; event observation
must not fake progress. Include only category/code/duration/caller/U/outcome and
trusted trace-only correlation. No prompt, Teams content, command/tool payload,
SQL, URL, credentials, arbitrary exception or free-form labels. Unknown values
fail closed in Python and Collector; safe siblings still export.

Extend existing Progress/snapshot (no DB/API) with: status, current business stage,
current agent/tool, all active dependency descriptors + deterministic current child,
monotonic elapsed/stage elapsed/last-real-progress age/remaining budget, operator
wall timestamps, expired category/code, stall age, cleanup outcome, caller/U
termination indication and detached counts. Existing run/trace IDs trace-only.
Bound concurrent lists/timeline; no new terminal history registry. Safe M6 state is
suitable for FUTURE M7 projection; it is not cross-instance durable truth and no
protected query endpoint is created in M6. Cost remains existing M3 metadata only;
no M10 cost accounting/summary invented.

### Settings / UI impact evaluation (all five required questions)

1. Creates safe deadline/stall/progress/cleanup/effective-config information needed
   later by Settings → Observability & FinOps → Reliability/Diagnostics/Tracing.
2. Defines execution-local diagnostic and effective configuration shapes for M7;
   no query API, migration, persistence or access endpoint. Minimal heartbeat SSE
   transport contract compatibility is current reliability scope, not diagnostics.
3. No Settings panel/admin screen belongs to M6. Minimal existing TypeScript event
   parser/consumer support for safe heartbeat belongs here; preserve typography,
   navigation, layout and progress labels. No internal data exposed in normal chat.
4. Existing session ownership applies to SSE. Future diagnostic API requires
   server-enforced User/Operator/Developer-SRE/FinOps/Admin/Auditor permissions;
   FinOps gains no content access. M6 internal snapshots expose no new API surface.
5. Effective reliability configuration is environment/backend/IaC-owned, read-only
   including future Admin display; no UI mutation or bypass of Terraform policy.

### Exact files expected to change during authorized M6 implementation

Only `.agent/OBSERVABILITY_EXECUTION.md` changes in THIS planning pass. The following
are proposed paths, not implementation that already exists. Reinspect before edits.

New runtime modules:
- backend/observability/deadlines.py — immutable monotonic budgets, typed expiry,
  injectable clock/scheduler, retry admission and lease; no SDK startup.
- backend/observability/watchdog.py — one tracked runtime monitor and real progress.
- backend/observability/blocking_work.py — bounded executor/admission, transport
  handles, actual completion and context-safe late discard; no unsafe thread kill.
- backend/observability/reliability_contract.py — finite category/outcome/config
  projection contracts; reuse canonical errors/statuses, not alternate settings.
- backend/observability/reliability_metrics.py — bounded unsampled producers/units.

Existing integrations/contracts:
- backend/observability/config.py; backend/config/settings.py — central typed
  config, aliases/provisional defaults, safe effective view, bounded secret miss.
- backend/observability/turn_trace.py; backend/observability/active_runs.py — one
  terminal owner, monotonic progress fields, snapshot/stall/progress handling.
- backend/observability/model_adapter.py; backend/observability/model_provider.py;
  backend/observability/model_instrumentation.py — model/embedding stream lifetime
  budget, attempt submit checks, provider progress, bounded response close.
- backend/observability/adk_adapter.py; backend/observability/agent_instrumentation.py;
  backend/observability/tool_instrumentation.py — agent/tool enforcement, known sync
  transport containment, same M4 scopes, task ownership and finite classifications.
- backend/observability/dependency_instrumentation.py;
  backend/observability/dependency_database.py;
  backend/observability/dependency_storage.py;
  backend/observability/knowledge_instrumentation.py — M5 caller/U fields and
  deadline integration; separate enforcement from optional telemetry guards.
- backend/observability/metrics.py; backend/observability/redaction.py;
  backend/observability/schemas.py; backend/observability/logging.py;
  backend/observability/README.md — registration/privacy/safe event rules.
  backend/observability/errors.py only if typed classifier support belongs here;
  existing ErrorCode values remain unchanged. stages.py only for existing
  transition helpers, no unregistered stage vocabulary.
- backend/api/chat_service.py; backend/api/app.py; backend/api/session_service.py;
  backend/api/execution_coordinator.py; backend/api/source_reference.py;
  backend/api/activity_queue.py; backend/api/streaming_events.py — minimal driver,
  preflight, lock/admission, contributors, queues, send and safe heartbeat wiring.
- backend/gateway/power_automate_client.py; backend/tools/teams/execute_write.py;
  backend/api/execution_service.py — per-request transport budget, preserve typed
  failure/uncertainty, owner-only proposal mutation, no write replay.
- backend/api/attachment_service.py; backend/attachments/storage.py;
  backend/knowledge_ingestion/artifact_storage.py — bounded transport offload and
  safe SDK retry windows, existing compensation budgeted, no storage redesign.
- backend/cases/db.py; backend/attachments/repository.py;
  backend/knowledge/repository/sqlalchemy.py — public engine options and remaining
  budgets; read retry preserved, no private ADK engine patch or migration.
- backend/knowledge/retrieval/service.py; backend/tools/knowledge/runtime.py —
  injected vendor-free budget seam, preserve dense-only fallback and ranking.
- backend/agents/team_manager/read_continuation_execution.py — route existing
  blocking sync execution into bounded policy; no new event loop for AgentTool.
- backend/api/turn_context.py; backend/tools/knowledge/diagnostic_trace.py — sealed
  run mailbox guards only if retained worker writes require them; no evidence
  semantics changes. Additional concrete mailbox owners require an explicitly
  updated inventory before modification, not opportunistic refactoring.
- backend/operations/execution.py; backend/api/operational_execution_service.py —
  bounded existing authorized read invocation only, no execution adapter added.
- docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md;
  docs/Telemetry/04_RELIABILITY_TIMEOUTS_ERRORS.md;
  infra/observability/collector/config.yaml;
  infra/observability/collector/config.local.yaml — register safe compatible M6
  contract/metrics/events and local Collector defense-in-depth. No topology change.
- src/api/types.ts; src/api/sseParser.ts; src/api/runBackendChat.ts — minimal additive
  heartbeat acceptance/no-op or freshness handling; no Settings or UI design.

New tests (exact proposed paths):
backend/tests/test_observability_deadlines.py;
backend/tests/test_observability_watchdog.py;
backend/tests/test_observability_reliability_errors.py;
backend/tests/test_observability_blocking_work.py;
backend/tests/test_observability_reliability_cleanup.py;
backend/tests/test_observability_reliability_retries.py;
backend/tests/test_observability_reliability_sse.py;
backend/tests/test_observability_reliability_concurrency.py;
backend/tests/test_observability_reliability_metrics.py;
backend/tests/test_observability_reliability_performance.py;
backend/tests/test_observability_reliability_coverage.py.
Extend existing backend/tests/test_chat_service_root_trace.py,
test_observability_collector.py, test_observability_settings.py,
test_api_execution_coordinator.py, test_api_run_cancellation.py,
test_api_execution_endpoints.py, test_teams_execute_write.py, affected M3–M5 suites;
src/api/sseParser.test.ts, streamChat.test.ts, runBackendChat.test.ts.
No requirements/version upgrades, Alembic, Terraform, new HTTP diagnostics routes,
Cloud SQL active-run store or UI component files planned.

### Implementation order (future authorization only)

1. Recheck unchanged M5 gate, record exact config/SDK/worker integration inventory
   and preservation snapshot; keep all unrelated local work.
2. Register finite config/outcome/metadata and immutable budgets; deterministic
   deadline/error/lease tests first, then model/tool/dependency wrappers.
3. Resolve blocking transport/state separation and bounded worker containment;
   prove late-write/caller-U semantics before arming whole-turn cancellation.
4. Integrate same-task M2 lifetime, lock acquisition, owned children and aggregate
   cleanup reserve; prove timed-out driver cannot retain mutation/lock ownership.
5. Add watchdog/real progress and safe heartbeat; STALLED remains nonterminal.
6. Add bounded business queues and send timeout/detach; preserve completion order,
   backend independence and explicit incomplete-delivery errors.
7. Register/export/Collector-validate bounded unsampled metrics and events; privacy,
   disabled-telemetry enforcement and fault isolation tests.
8. Run full local acceptance/regression/performance, individually evaluate exits,
   inspect diff/untracked preservation; update evidence and stop before M7.

### Validation plan / commands (NOT RUN in this planning pass)

Test safety prerequisite: use a reviewed isolated runner clearing cloud/DB/proxy
configuration, replacing providers/gateway/storage/secrets, disposable local
SQLite only, and denying ALL non-loopback sockets. Do not use real credentials
or app startup default warmup. Existing M5 guard/211-file manifest are in temporary
storage and may disappear; inspect if present and regenerate local copies from
reviewed test files if absent. Do not blindly run backend/tests/manual or live
corpus/provider scripts. Local loopback Collector may need sandbox approval; this
is not cloud authorization. Compile/type checks have no client construction.

Deterministic tests use injectable monotonic clock AND controllable scheduler;
advancing a fake clock alone does not trigger real asyncio timeout callbacks.
Use event/barrier-controlled fake worker plus finite test harness timeout for
real thread checks, no multi-second sleep matrix. Always join released test
workers/close SQLite pools and promote relevant unhandled-task/thread/unraisable
and SQLAlchemy cleanup warnings to failures in controlled fixtures.

| Test group | Required evidence |
|---|---|
| Turn/hierarchy | Normal success, exact expiry, parent cap, serial children/retries cannot reset budget, lock included, cleanup reserve inside total, one TIMEOUT/terminal/span close, pre-start task cancellation |
| Stalls/progress | Real progress suppresses stall, no progress emits once, heartbeat/tick do not reset, resume RUNNING clears condition/duration, stall nonterminal then timeout; parallel child progress is truthful |
| Blocking worker — BLOCKING | Start fake sync dependency, time out C, finish turn, prove U still running with capacity occupied, release U later, no root/state/mailbox/response mutation or stale ambient context; exactly one late observation and original M5 span lifetime |
| Side effect — BLOCKING | Fake approved action executes remotely then delayed response; timeout OUTCOME_UNKNOWN/no replay; owner-only consume, no late proposal mutation; signed authority unchanged; commit/upload/delete ambiguity; 429 without trusted guarantee not falsely confirmed |
| Cancellation | During model/tool/gateway/DB pool/query/knowledge/storage/SSE/persistence/preflight/lock, including explicit Stop vs disconnected relay; exact error cause, rollback outcome honest, no replay |
| Cleanup — BLOCKING | Success/failure/timeout/cancel; cleanup throws/hangs; response/generator close in correct task; aggregate reserve, context reset, child restoration/terminalization, no stale cleanup session delta; old mutator cannot survive lock release |
| Locks — BLOCKING | Acquire, cancelled waiter, timeout waiter, timeout holder, failure holder; no release of another holder; next turn/approval/rewind proceeds safely; no leaked lock |
| Retry | Safe retry within remaining cap; insufficient budget refuses new attempt; Retry-After/backoff exceeds budget; SDK/application loops do not multiply; business recovery remains tools-disabled and no read/write replay |
| Queue/delivery — BLOCKING | Count/byte full queue, slow send, disconnected/abandoned relay, producer finish/error/cancel with full queue, independent completion signal, heartbeat coalescing, no silent business loss/empty sync success, persist while relay detached |
| DB/knowledge | Four public engines, real disposable SQLite pool exhaustion/acquisition and query limits, read retry parent budget, dense-only fallback vs root expiry; ranking/evidence unchanged; query SQL never exported |
| Concurrency | Parallel different deadlines/stalls/agents/tools/dependencies, same-session serialization; no budget/watchdog/context leak; saturated worker admission bounded after many caller timeouts |
| Fault isolation | OTel disabled, broken trace/log/metric/watchdog telemetry, full weak registry; enforcement survives; controller failure visible with backstop, not silent infinite execution |
| Privacy/export contract | IDs only trace fields; fake credentials/prompt/Teams/command/tool/SQL/URL sentinels absent; finite enum/unit/type/count labels; direct malformed OTLP blocked independently, safe siblings retained |
| Regression | M0–M6 observation suites + 211-file M5 affected union + new affected tests; governance/approval/authority/provenance/egress/rewind/session/SSE/knowledge/DB/GCS/gateway/models/agents |
| Heartbeat transport | Parser accepts/validates safe new envelope, consumer keeps current safe stage, no dependency details/new UI; existing completion/cancel/incomplete-stream behavior; frontend tests/type/build |

Proposed commands after reviewing/recreating isolated runner at
/private/tmp/slopanoc-m6-test-guard/run_tests.py and manifest at
/private/tmp/slopanoc-m6-regression-manifest.json (neither created in this pass):

```bash
.venv/bin/python /private/tmp/slopanoc-m6-test-guard/run_tests.py backend/tests/test_observability_deadlines.py backend/tests/test_observability_watchdog.py backend/tests/test_observability_reliability_errors.py backend/tests/test_observability_blocking_work.py
.venv/bin/python /private/tmp/slopanoc-m6-test-guard/run_tests.py backend/tests/test_observability_reliability_*.py
.venv/bin/python /private/tmp/slopanoc-m6-test-guard/run_tests.py backend/tests/test_observability_collector.py
.venv/bin/python /private/tmp/slopanoc-m6-test-guard/run_tests.py --manifest /private/tmp/slopanoc-m6-regression-manifest.json
.venv/bin/python -m compileall -q backend
.venv/bin/python -m pip check
npm test -- --run src/api/sseParser.test.ts src/api/streamChat.test.ts src/api/runBackendChat.test.ts
npm run build
git diff --check
git status --short
```

Inspect package.json before frontend commands; use actual supported scripts.
Performance: paired warmed baseline vs deadline-only vs watchdog+progress (disabled,
no-export and local export); >=100 samples report median/p95/p99/CPU, check cost per
budget check/progress operation, idle watchdog wakeups <=1/second/run plus bounded
material-transition notifications, task/worker/queue memory and retained handles
return to baseline after cleanup. Local incremental p95 <10ms per tested synthetic
turn excluding injected provider wait, in line with doc 01; <3% production CPU is
NOT proven locally. Measure timeout overshoot, heartbeat cadence under event-loop
load and executor saturation; no claim of production throughput/latency/profiling.
No live cloud testing, no deployed timeout/IAM/billing/backend policy changes.

### Objective exit criteria — individual future acceptance gates

Every item is **NOT EVALUATED — implementation/test pending**, not PASS from design.

1. One Settings-owned typed finite config covers every inventoried blocking wait;
   effective values/aliases recorded; provisional defaults clearly distinguished.
2. Absolute monotonic hierarchy; exact expiry and every child/admission/retry clamped
   to remaining parent, maximum accepted-to-terminal includes cleanup reserve.
3. Deterministic existing canonical codes/cause ownership; timeout vs explicit
   cancellation vs stall/cleanup/unknown outcome distinguishable without raw text.
4. One tracked watchdog; truthful real-progress stall/resume; STALLED nonterminal,
   synthetic heartbeat never clears it or rewrites blocked business stage.
5. Turn timeout/cancel root finishes once through M2 after lock/mutation ownership
   resolved; same-task ADK/model generator context closes/restores correctly.
6. ALL loop-blocking SDK paths contained; bounded worker admission/active/pending
   work, permits held to actual completion; no unsafe thread kill/unbounded joins.
7. Blocking fake late worker test passes: caller timeout distinct from U completion,
   no late business state/SSE/terminal mutation or stale ambient span/context reuse.
8. Ambiguous write/command/storage/commit classified OUTCOME_UNKNOWN, no automatic
   retry or invented authority/idempotency guarantee; existing approvals preserved.
9. Existing safe SDK/application/business retry ownership explicit; deadlines/backoff
   and admission respected, no retry storms/operational replay.
10. Cleanup bounded on every exit; hangs/failures visible, contexts/live children/
    spans cleared; cannot abandon session mutators and release their lock.
11. Session acquire/holder timeout/cancel/failure tests demonstrate no lock leak and
    safe subsequent turn/approval/rewind admission; root closes after release.
12. SSE/backend lifetime independent; explicit Stop preserved; connected completion
    order and truthful safe heartbeat; slow/disconnected relay cannot hang cleanup.
13. Queue count/bytes/backpressure bounded, full queue can't strand terminal/error
    signal; no silent required-business-event loss, no empty successful sync result.
14. DB acquisition/query/commit and dense fallback behavior budget-bound; supported
    driver cleanup tested locally; cancellations do not fabricate rollback success.
15. Watchdog/progress/blocked-child/task state execution-local and bounded; no M7
    DB schema/storage/history/query API, M8 UI or distributed locking implementation.
16. Unsampled finite reliability metrics/events validated at Python+Collector export;
    no IDs/exemplars/free strings; no duplicated request/attempt accounting.
17. Privacy/exception ownership and telemetry failure isolation pass; enforcement
    works with telemetry disabled and controller failure never silently unprotected.
18. All applicable M0–M6 and affected business regressions/import/build/frontend
    contract tests pass; each blocking defect repaired; local overhead evidence kept.
19. Scoped Git diff/status/untracked/preservation checks show no unrelated work loss,
    no secrets/generated payload/DB committed; actual implementation/results recorded.
20. No M7 or later scope and no remote mutation/deployment/migration/live test.

### Risks, blockers and cloud status

Planning blockers: **NONE found** after the recorded local M0–M5 gate audit.
Implementation risks requiring concrete proof, not waived criteria: sync tools
blocking event loop; worker state mutation; AnyIO cancellation shielding; generator
close cancellation suppression; DB transaction uncertainty; SDK-hidden retries/
credential waits; ADK pinned instance compatibility; full queues and ASGI send
cleanup; false progress from observation events; broad legacy fixture cleanup
warnings. Do not label these resolved until the named blocking tests pass.
Unknown deployed timeout/resource settings and downstream retry/idempotency remain
unknown; production rollout and live capacity/performance validation deferred.

Local planning status = COMPLETE.
Local M6 implementation status = NOT IMPLEMENTED.
Local M6 runtime validation status = NOT RUN.
Cloud deployment status = **NOT EXECUTED — USER APPROVAL REQUIRED**.
Exact pending M6 cloud operations = **NONE**. M6 requires local code/contracts/
Collector definitions only. Future deployment of config/code is a separate exact
operation approval; inherited M1 rollout remains pending independently.
No GCP/Cloud Run/Cloud SQL/BigQuery/IAM/billing/GCS/Secret Manager/Power Automate/
Microsoft Graph/shared DB/Terraform state/external-service mutation performed.

### Planning-pass validation and actual changes

Actual file changed: `.agent/OBSERVABILITY_EXECUTION.md` only (status/header,
M6 matrix row, appended M6 plan). M0–M5 history preserved. No implementation,
new test/runtime/frontend module or infrastructure definition written in this pass.

Planning validation: static source/installed-SDK audit and requirements coverage
review completed; `git diff --check` PASS; Git status/untracked roster and isolated
before/after execution-record diff reviewed. SHA256 comparison of 816 tracked and
untracked nonignored files: exactly this record changed; zero new/deleted files,
815 unchanged. Snapshot excludes ignored dependencies/generated data and makes
no claim to validation of those files. M6 runtime tests/performance NOT RUN;
M0–M5 historical test results were not independently rerun. No blocking planning
failure found, no implementation repair performed, all M6 runtime exits pending.

**Ready To Implement M6: YES** — planning is reviewable; requires separate user
implementation request. This is NOT M6 acceptance or cloud authorization.
**Ready for Next Milestone: NO. M7 BLOCKED. STOP after planning.**


## M6 implementation authorization — 2026-10-08

User approved the M6 plan and authorized M6 local implementation/validation only.
Approved objective/specifications/inventory/invariants/path manifest/validation and
20 objective acceptance gates above remain the implementation contract. Latest
request forbids Settings/UI implementation: retain existing public progress protocol
with SSE transport heartbeats; no new screen or UI functionality. All M7+ work and
remote mutations remain prohibited. Initial gate rechecked: recorded M0–M5 local
acceptance PASS, actual boundaries inspected, unrelated working tree snapshotted.
Current implementation plan: central monotonic budgets/config; bounded isolated
workers; M2 driver/cleanup/lock/queue integration; existing M3–M5 budget wrappers;
watchdog/progress; safe bounded metrics/export contracts; isolated tests and repair.
Expected paths are the approved manifest above; additional paths must be inventoried
before change. Configuration numbers remain provisional, not approved SLO values.
M6 exit gates PENDING. Ready for Next Milestone: NO. Cloud operations NONE.

M6 implementation inventory additions before editing: delivery queue mechanics will
live in backend/observability/delivery.py rather than expanding chat_service;
backend/observability/attributes.py requires finite M6 metadata registrations;
backend/api/hosted_content_vision_context.py requires deferred worker mailbox writes.
These are enabling M6 paths only. Root/model generator cleanup and SQLite async
worker completion are explicit bounded-wait inventory entries, not assumed aborts.

### M6 implementation progress and repaired validation defects — 2026-10-08

Status remains IMPLEMENTATION IN PROGRESS; acceptance gates are not waived.

Implementation now uses `deadlines.py` (absolute monotonic budgets),
`watchdog.py` (one auxiliary monitor + independent deadline/cleanup timers),
`blocking_work.py` (bounded daemon executor + isolated ToolContext state + deferred
mailbox commits), `delivery.py` (count/byte limits and independent completion), and
`reliability_metrics.py` (finite unsampled aggregates). M2 owns the root and terminal
result. Existing M3/M4/M5 boundaries retain their original span ownership.

Additional inspected boundaries added to the approved inventory:

- ADK's configured `_call_tool_in_thread_pool` path bypassed its normal async
  dispatcher. The instance-local pinned flow facade now routes it through the
  existing observed tool dispatcher; synchronous Teams functions receive the
  bounded private-state executor, while async tools stay on their owning loop.
  An unsupported pinned dispatch interface fails before tool egress; the old M4
  compatibility test now explicitly verifies this M6 safety requirement.
- Settings remains the configuration owner. A lazy provider registration from
  Settings supplies the pure reliability module; there is no reverse import from
  observability into backend configuration and no configuration/secret resolution
  during registration. A model coverage double now supplies this typed contract.
- Hosted-content/message-text/activity setters use deferred worker writes. Only a
  timely active owner applies the batch. Canonical tool state is never passed to
  the contained synchronous worker.
- Generator cancellation can enter its `finally` before reaching the driver's
  cleanup scope. An independent reserve-expiry timer supplies a second bounded
  cancellation. Normal asyncio session-lock release is synchronous and occurs
  even if generator cleanup exhausted the reserve.
- Whole knowledge retrieval uses the planned provisional 15s budget. The existing
  separate dense timeout remains 10s by default, preserving dense-only sparse
  fallback. The M0 scaffold's dense-to-whole fallback alias was separated now
  that the whole-retrieval setting is enforced. Explicit KNOWLEDGE_TIMEOUT_SECONDS
  overrides the whole budget; existing dense configuration still controls dense.
- ASGI send has a delivery cap; artificial liveness uses SSE **comments**, not a
  new business wire event. This respects the latest no-UI scope without requiring
  frontend/TypeScript changes. Heartbeats never mark material progress.
- No new retry engine exists. Existing provider/database retries are checked
  before egress; their waits consume the same parent budget. Gateway has no new
  retry; SDK storage writes explicitly disable SDK retry. Read SDK retry duration
  and per-request transport timeout are capped, without changing shared options.

Validation evidence so far:

- Initial existing integration set: 86 passed (3 warnings).
- Expanded dependency/model/tool/root/stream/write set: 388 passed (3 warnings).
- Initial fault tests: one nested-timeout test incorrectly expected the inner
  context to absorb a simultaneous ancestor expiry; repaired to assert the
  owning ancestor's typed expiry. No runtime gate was waived.
- Sandbox initially denied loopback binds. The isolated, external-service-blocking
  runner was approved for local loopback execution; 7 existing Collector/native
  validation tests passed. No remote infrastructure operation was proposed.
- New reliability Collector round trip: 1 passed (7 deselected), preserving finite
  event attributes, STALLED metric labels and seconds histogram units.
- Real driver/worker/model/gateway fault set: 43 passed (1 dependency warning).
- Broad affected M5 manifest: 3704 passed, 4 failed. Failures: two ADK merge tests
  required already-produced events to precede ordinary generator failure; the
  inert-import contract prohibited a reverse Settings import; a synthetic
  embedding Settings double lacked observability_config. Repairs preserved
  event order, introduced configuration dependency inversion and updated the
  narrow test double. Targeted repair set: 94 passed (1 dependency warning).
- Broad final validation, new boundary tests, performance and diff review remain
  pending; Ready for Next Milestone remains NO.

UI evaluation: M6 creates safe runtime deadline/progress/stall/worker/cleanup data
for future Settings → Observability & FinOps. No query API or persistence contract
is implemented; M7 owns those. No Settings UI belongs to M6. Existing role/privacy
policy applies to future diagnostic access; runtime configuration projection is
read-only and versioned, with no UI mutation or IaC bypass.

Cloud deployment status: NOT EXECUTED. M6 requires no cloud operations. All tests
use in-memory/disposable local databases, fake transports and/or loopback OTLP.
M1's separately pending deployment still requires exact user approval. M7 has not
been started.


## M6 completion evidence — 2026-10-08

**IMPLEMENTED AND LOCALLY VALIDATED. All blocking local M6 criteria PASS.**
M7 has NOT been started. No M6 cloud deployment or external operation is required.

### Final architecture and timeout inventory

M2 retains the existing background driver, one root, one canonical terminal
result and session-lock responsibility. M3 owns model operations/physical attempts;
M4 owns agent/tool operations; M5 owns actual dependencies. M6 adds enforcement
around those boundaries without duplicate executions/spans or a second runner.

`Deadline` is immutable and monotonic. Children use the minimum of the absolute
parent and their category cap. Iterator budgets are allocated once, never anew
per chunk. Both async await and synchronous return are checked. SDK options are
per-call/per-request; shared model clients/options are not rewritten. Physical
provider submissions check retry budget before creating/submitting another
attempt. Deadline-driven cancellation is classified TIMEOUT through existing M3
and M4 operations; ordinary cancellation remains CANCELLED. Root cause and cleanup
outcome are separate; M2 prevents later cancellation overwriting TIMEOUT.

| Configurable PROVISIONAL value | Default | Enforcement / ownership |
|---|---:|---|
| Total turn | 180s | Existing driver, independent deadline timer |
| Cleanup reserve | 5s | Inside total; one shared reserve; independent unwind timer |
| Session load / session-lock wait | 10s / 10s | Existing session facade / coordinator |
| Planning / orchestration | 30s / 150s | Existing planning decorator / ADK merge iterator |
| Model / agent / tool | 120s / 150s / 30s | Existing M3/M4 logical operations |
| Gateway | 10s | Actual Power Automate transport; never relabeled Graph |
| DB acquisition / query | 5s / 15s | Pool/connect including pre_ping; observed session execute; asyncpg options |
| Knowledge whole / dense | 15s / 10s | Injected vendor-free whole guard; existing dense-only fallback |
| Storage / secret | 30s / 10s | Existing M5 SDK wrappers + per-call transport/retry bounds |
| Persistence | 10s | Session facade and commit, including absent telemetry owner |
| ASGI send / business queue wait | 10s / 1s | Delivery response / bounded buffer |
| Retry minimum budget | 1s | Existing retry owner admission; no generic retry engine |
| Stall / heartbeat / coarse watchdog check | 30s / 5s / 1s | Material progress / SSE comments / auxiliary monitor |
| Blocking executor workers / pending | 8 / 16 | Occupancy held through actual worker termination |
| Business queue count / approximate bytes | 256 / 4,194,304 | Deterministic serialized size; explicit oversize/backpressure |

Legacy Graph timeout defaults to 10s and is only a gateway configuration alias;
legacy database aggregate defaults to 30s and explicit aggregate configuration
supplies separate acquire/query fallback caps. Explicit category configuration
wins. Dense timeout remains separate from whole knowledge retrieval. These are
hard safety caps, **not production-approved SLOs**. All timeout settings use the
existing Settings architecture. Effective configuration is read-only, omits
secret endpoints and versions timeout/stall/heartbeat/capacity changes.

### Watchdog, material progress and execution-local state

One auxiliary watchdog per existing driver observes root stage, actual progress,
agent/tool/dependency ownership, elapsed/remaining budget and terminal state.
Phase transitions, agent transitions, non-thought provider output, tool/dependency
completion and actual persistence progress count. Ticks, exports and SSE comments
never count. One bounded STALLED episode resumes to RUNNING on genuine progress;
another episode may form. Independent timer enforcement survives a monitor or
telemetry failure. Existing execution-local snapshots now include deadline,
remaining time, progress age, stall, agent/tool/dependencies, expired category,
cleanup outcome and finite worker/caller/outcome metadata. There is no DB history,
repository, migration or API. The active registry remains execution-local.

### Blocking work, ownership and side effects

The bounded daemon executor is process-owned, not created per request. Admission
rejects excess work deterministically. Caller timeout/cancellation does not free
running-worker occupancy. Pending cancellation prevents later remote egress;
occupancy releases only when the actual queue/worker lifecycle is resolved.
Worker metadata distinguishes waiting/completed/failed/timeout/cancelled caller,
not-started/running/terminated underlying work and outcome certainty.

Synchronous Teams tools receive a private deep-copied ToolContext state. A timely
owner merges the actual state delta; known-read registries accumulate rather than
losing parallel IDs. Message-text, message-order/metadata and optional activity
writes are deferred until a timely owner applies them. Final review discovered
that hosted-image admission returns a boolean: it now uses a guarded owner-loop
handoff for the actual local mailbox admission, preserving true limits/return
semantics. That handoff checks the captured absolute deadline and active lease
before mutation; a late worker cannot enqueue an image. No external invocation is
replayed by that handoff. Existing authority/approval checks remain in their
original tool functions against private state; confirmed timely consumption is
applied by the active owner. Unconfirmed timeout never consumes canonical proposal
state or automatically retries a write. Already-egressed gateway effects are
OUTCOME_UNKNOWN until confirmed; timeout does not imply remote failure.

Late results are discarded. Existing ended root/agent guards reject stale
correlation; no new business SSE/response or terminal mutation occurs. A detached
late-completion metric/log uses finite categories/count metadata and empty context.
Existing already-started dependency spans can close when underlying work returns;
that is honest late completion, not a second invocation or renewed root attachment.

### Cleanup, locks, delivery and retries

One shared cleanup deadline covers designated cleanup persistence, generator
closure, owned-task cancellation/join, context reset and root closure. Cleanup
errors/timeouts are safe metadata and cannot replace original TIMEOUT. The second
independent reserve timer also bounds generator unwinding that starts before a
cleanup scope can be entered. Standard asyncio session-lock release is synchronous
and occurs even when earlier closure consumed the reserve. Cancelled/timed-out
waiters cannot acquire later; subsequent valid turns proceed.

Business queues are count/byte bounded with separate completion/error control.
Oversize and capacity exhaustion explicitly fail delivery; no content is truncated.
Ordinary generator failure drains already-produced events before propagating the
error. Saturation uses urgent independent error signaling. Relay disconnect
clears delivery buffers while the existing backend driver can continue, persist
and finish. ASGI sends are bounded and comments provide transport liveness without
UI/protocol changes. Optional activity remains best effort with saturation metrics.

Retry owners remain SDK/application-owned. Admission checks parent budget before
another physical request; actual backoff is cancelled by the enclosing absolute
budget. Provider exhaustion and deadline denial have separate finite counters.
Gateway/action replay was not added; storage writes disable SDK retry; supported
read SDK retry/transport durations are capped. Dense-only timeout preserves sparse
fallback; parent deadline expiry propagates instead of becoming substitute evidence.

### Final tests and validation

All test commands use `/private/tmp/slopanoc-m5-test-guard/run_tests.py`, which
strips inherited cloud credentials/configuration/proxies, disables model warmup,
sets in-memory databases and blocks external socket connections. Loopback
Collector/OTLP tests alone require sandbox elevation. No cloud/shared mutation.

| Exact validation command / selection | Final result |
|---|---|
| `.venv/bin/python /private/tmp/slopanoc-m5-test-guard/run_tests.py --manifest /private/tmp/slopanoc-m5-regression-manifest.json backend/tests/test_observability_reliability.py` | 3,758 passed; 477 warnings; 101.00s |
| Same runner: `backend/tests/test_observability_reliability.py backend/tests/test_observability_model_*.py backend/tests/test_observability_agent_instrumentation.py backend/tests/test_observability_tool_instrumentation.py` | 252 passed; 8 warnings; 8.90s; includes final deadline-cancellation/retry metadata repairs |
| Same runner: reliability, Settings and contract tests with `-W error::pytest.PytestUnhandledThreadExceptionWarning -W error::RuntimeWarning -W error::ResourceWarning -W error::sqlalchemy.exc.SAWarning` | 90 passed; 1 dependency deprecation warning; 3.75s |
| Same runner: reliability, root trace, turn trace and active-run tests | 92 passed; 1 dependency warning; 4.56s; includes final snapshot projection |
| Same runner: reliability + hosted-content context/single/all-image tool tests | 128 passed; 1 dependency warning; 3.55s |
| Same runner: `backend/tests/test_observability_collector.py` | 8 passed; 1 dependency warning; 18.50s; native validate both configs + all M1–M6 round trips |
| `.venv/bin/python -m compileall -q backend/observability backend/api backend/gateway backend/knowledge/retrieval backend/tools/knowledge` | PASS |
| `git diff --check` | PASS |

The broad suite preceded the final metadata-only cancellation/retry/snapshot
repairs; the listed final focused/strict/Collector suites validate those exact
final paths. There are 50 M6 reliability test cases, including all 18 category
parent-deadline inequalities; disabled telemetry; monitor failure; real canonical
root timeout/late worker; private state and return-valued mailbox admission;
side-effect uncertainty with telemetry failure; real provider/Power Automate
boundaries; disposable SQLite pool exhaustion; bounded query/commit callers;
provider retry/backoff; count/byte/oversize/control signaling; ASGI send timeout;
relay disconnect; cleanup hang and lock recovery; parallel real turns with
separate agents/tools/dependencies and terminal outcomes; safe metrics/projection.

Failure/repair history is retained above. Additional final repairs: persistence
had an unbounded commit branch when no telemetry owner was registered; the branch
is now inside the same persistence cap. The hosted-content admission boolean was
preserved through a guarded owner handoff. Physical model deadline cancellation
is TIMEOUT rather than generic CANCELLED. Retry exhaustion and deadline denial
are distinct. No blocking defect remains.

### Reliability metrics and privacy

Unsampled counters cover turn/stage timeout, stall/resume, cancellation, cleanup
failure/timeout, late completion, worker still running, executor saturation,
admission rejection, queue saturation, retry count, retry denied, retry exhausted
and controller failure. Histograms cover stall/cleanup duration, lock wait and
deadline overshoot. Exact finite labels: environment, operation category, status.
No IDs, content or dynamic exception strings; units are 1 or seconds, and exemplar
content is stripped/rejected. Application export validation and Collector keep-key,
closed-value/type/range/event-name filters independently enforce the contract.
M1–M5 privacy tests remain green; M6 round trip preserves safe reliability fields,
STALLED status, category labels and seconds histograms.

### Performance measurement

Local synthetic benchmark (`/private/tmp/m6-benchmark.py`): median of seven batches
of 20,000 iterations; `perf_counter_ns`; telemetry disabled; same-process no-op
baseline. Actual worker handoff measured separately over 100 warmed submissions.

| Operation | Final local median |
|---|---:|
| No-op baseline | 38.2 ns |
| Deadline lookup/check | 118.1 ns |
| Context lookup + deadline/lease check | 518.5 ns |
| Material progress notification | 115.8 ns |
| Bounded worker admission + complete round trip | 65.542 µs |
| Watchdog idle wakeups | 1 in 1.15s |

Earlier repeated runs: context check 260–519ns; worker round trip 66–134µs. These
are synthetic, scheduler/load-dependent local measurements, not production
latency, Cloud Run/GCP throughput, or SLO approval. The monitor has no tight idle
polling loop; default coarse check is one second plus genuine event notifications.

### Working-tree audit and actual files changed

`git status --short`, `git diff --stat`, full `git diff`, untracked paths and
`git diff --check` were inspected. The pre-implementation SHA-256 snapshot covered
816 existing files; no baseline file was deleted. Only the following approved M6
files changed; unrelated pre-existing M0–M5 changes remain intact. No secrets,
production payloads, DB contents or test exports were added to the repository.

Changed existing files:

- `.agent/OBSERVABILITY_EXECUTION.md`
- `backend/observability/README.md`
- `backend/observability/active_runs.py`
- `backend/observability/adk_adapter.py`
- `backend/observability/agent_instrumentation.py`
- `backend/observability/attributes.py`
- `backend/observability/config.py`
- `backend/observability/dependency_database.py`
- `backend/observability/dependency_instrumentation.py`
- `backend/observability/dependency_storage.py`
- `backend/observability/metrics.py`
- `backend/observability/model_adapter.py`
- `backend/observability/model_instrumentation.py`
- `backend/observability/model_provider.py`
- `backend/observability/redaction.py`
- `backend/observability/runtime.py`
- `backend/observability/schemas.py`
- `backend/observability/tracing.py`
- `backend/observability/turn_trace.py`
- `backend/tests/test_observability_adk_adapter.py`
- `backend/tests/test_observability_collector.py`
- `backend/tests/test_observability_model_coverage.py`
- `backend/tests/test_observability_settings.py`
- `infra/observability/collector/config.local.yaml`
- `infra/observability/collector/config.yaml`
- `backend/api/activity_queue.py`
- `backend/api/app.py`
- `backend/api/attachment_service.py`
- `backend/api/chat_service.py`
- `backend/api/execution_service.py`
- `backend/api/hosted_content_vision_context.py`
- `backend/api/session_service.py`
- `backend/api/source_reference.py`
- `backend/api/turn_context.py`
- `backend/config/settings.py`
- `backend/gateway/power_automate_client.py`
- `backend/knowledge/retrieval/service.py`
- `backend/tools/knowledge/runtime.py`

New files:

- `backend/observability/blocking_work.py`
- `backend/observability/deadlines.py`
- `backend/observability/delivery.py`
- `backend/observability/reliability_contract.py`
- `backend/observability/reliability_metrics.py`
- `backend/observability/watchdog.py`
- `backend/tests/test_observability_reliability.py`

### Individually evaluated M6 exit criteria

| Criterion | Result | Evidence |
|---|---|---|
| Centralized deadline configuration | PASS | Typed Settings/config plus read-only effective projection |
| Absolute monotonic deadline propagation | PASS | Immutable Deadline, iterator-lifetime budgets |
| Child deadlines never exceed parent | PASS | All 18 category tests + real model/gateway/pool/commit paths |
| Provisional values clearly identified and configurable | PASS | Configuration table, README, Settings tests; no SLO claim |
| Watchdog independent of exporters | PASS | Disabled-runtime and failed monitor/telemetry tests |
| Material progress semantics correct | PASS | Actual transitions/output; heartbeat test does not reset progress |
| STALLED nonterminal | PASS | Deterministic running/stall state test |
| Progress resume clears stall | PASS | One episode, resume, later new episode tested |
| Timeout terminalizes once | PASS | Canonical root test checks one timeout event and unchanged terminal timestamp |
| Late synchronous workers cannot mutate terminal turns | PASS | Private state, deferred writes, guarded mailbox, actual root late completion tests |
| Underlying worker status truthful | PASS | Separate caller/worker/outcome metadata; no thread/server abort claim |
| Blocking capacity bounded | PASS | Saturation, pending rejection and retained occupancy tests |
| Ambiguous side effects not automatically retried | PASS | Actual gateway/fake side effect tests, invocation count one with telemetry failure |
| Retries respect remaining deadline | PASS | SDK retry success/exhaustion regressions, denial and long-backoff tests |
| Cleanup bounded | PASS | Shared reserve + independent unwind timer; actual hanging generator test |
| Cleanup failure preserves original cause | PASS | Root TIMEOUT persists through exhausted cleanup |
| Session locks cannot leak | PASS | Timed-out waiter, actual owned timeout/hang, next valid turn tests |
| SSE disconnect distinct from backend failure | PASS | Existing/new relay-close tests preserve backend COMPLETED |
| Business queues bounded | PASS | Count and serialized byte accounting; oversize tests |
| Required events not silently dropped | PASS | Explicit capacity failure; ordinary failure preserves queued event order |
| Terminal control survives saturation | PASS | Independent done/error channel; full payload queue test |
| Deadline/watchdog state execution-local | PASS | Existing weak registry + safe runtime projections; no storage/API |
| Reliability metrics bounded | PASS | Finite names/labels/status/units, exporter validation and Collector test |
| Privacy passes | PASS | All Collector cases + M6 malicious projection/metric tests; no content fields |
| M2–M5 instrumentation correct | PASS | Broad regression + final 252 instrumentation and 92 lifecycle tests |
| Applicable regressions pass | PASS | 3758 broad + final scoped/strict suites |
| M7 persistence/API unimplemented | PASS | Hash/path audit; no migration/repository/endpoint/UI added |
| No cloud mutation | PASS | Only local source/tests/config/native Collector; no external commands |
| Roadmap: no indefinite Processing in supported I/O paths | PASS | Caller budgets, independent timeout/cleanup control, bounded detached-worker capacity |
| Roadmap: stalled/timeout state visible | PASS | Execution-local snapshot and safe events/public timeout error; UI deferred by user |
| Roadmap: non-idempotent work not blindly retried | PASS | Gateway no retry; storage writes no retry; approval/egress regressions |

### Known limitations

- Hard caps/capacity defaults remain provisional; no production latency/load tuning
  or remote Cloud Run/Cloud SQL/gateway validation was performed.
- Caller cancellation cannot kill an existing Python thread or prove remote model,
  gateway, storage or database termination. Running sync work holds bounded
  capacity; unconfirmed writes/DB outcomes remain unknown. No blind replay occurs.
- Async enforcement relies on the actual stack's cooperative cancellation. Pure
  synchronous CPU code cannot be preempted safely on an event loop; existing
  corpus/input bounds remain in force. Arbitrary cancellation-hostile third-party
  code would require an isolated process contract, not unsafe thread/task killing.
- Broad suites show existing ADK/Starlette deprecations and nondeterministic legacy
  disposable-aiosqlite teardown warnings. Equivalent warnings exist in prior M5
  logs. Final M6 strict checks raise thread/resource/runtime/SQLAlchemy warnings
  as errors and pass; no new contained-worker warning was suppressed.
- Durable cross-instance active-run state, diagnostic history/API, Settings UI,
  dashboards, SLO engine and FinOps remain later milestones by design.

### UI / Settings impact

1. Safe runtime reliability information is ready for future Settings →
   Observability & FinOps, especially Reliability and Diagnostics.
2. Execution-local projections are extended; no query API/data persistence layer
   is introduced in M6. M7 owns that boundary.
3. No Settings UI or frontend design/protocol change belongs to this request.
   Existing SSE safe errors and comment heartbeats suffice for transport behavior.
4. Future access remains governed by server RBAC; no role is broadened here and
   FinOps gets no conversation/evidence content.
5. Effective timeout/capacity configuration is read-only and versioned; no UI
   settings mutation, secure-backend bypass or Terraform bypass exists.

### Cloud / infrastructure status

**NOT EXECUTED. No M6 cloud operations required or pending.**
No GCP, Cloud Run, Cloud SQL, BigQuery, IAM, billing, GCS, secrets, Microsoft,
Power Automate, Terraform state, shared database or external-service mutation
occurred. Existing M1 cloud deployment remains NOT EXECUTED — USER APPROVAL
REQUIRED, separately from these local M6 gates.

### Ready for Next Milestone

**YES** — M6 local gates are satisfied. **M7 has not been started; await a separate
user instruction.**

---

## M7 — Durable Observability Storage + APIs: planning only — 2026-10-08

**M7 — PLANNED — NOT IMPLEMENTED.** User authorization is planning only.
This section supersedes the historical instruction to await M7 planning; it does
not supersede M0–M6 completion evidence. No implementation, migrations, API
exposure, tests, deployment or external mutation is authorized by this plan.
Last completed milestone: M6. M8 remains blocked. Ready for Next Milestone: NO.

### Predecessor gate and authoritative specification audit

**Planning entry gate PASS.** Latest matrix and completion evidence record:
M0 VALIDATED COMPLETE; M1–M6 IMPLEMENTED AND LOCALLY VALIDATED; every predecessor
permits local progression. M6 completion evidence records all blocking local exit
criteria PASS, validation PASS and Ready for Next Milestone YES. Actual contract,
SDK/exporter, lifecycle, model, agent/tool, dependency and reliability modules and
tests exist. No source/record discrepancy requiring predecessor repair was found.
Historical tests were inspected, not rerun or independently recertified here.
M6 evidence includes 3,758 broad regression passes and final focused/strict suites;
those remain historical results. M1 cloud rollout remains explicitly pending under
its previously documented separation of local acceptance from deployment.

Read AGENTS.md, .agent/PLANS.md, this execution record and all user-requested
specifications: docs/Telemetry/README.md; 00_MASTER_BUILD_CONTRACT.md;
01_TARGET_ARCHITECTURE.md; 02_TELEMETRY_DATA_CONTRACT.md;
03_INSTRUMENTATION_AND_RUNTIME.md; 04_RELIABILITY_TIMEOUTS_ERRORS.md;
05_STORAGE_APIS_AND_UI.md; 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md;
10_DASHBOARDS_RUNBOOKS_OPERATING_MODEL.md; 11_SLOPANOC_INTEGRATION_MAP.md;
12_IMPLEMENTATION_ROADMAP.md; 13_TEST_VALIDATION_ACCEPTANCE.md;
14_CODEX_EXECUTION_GUIDE.md; 15_DEFINITION_OF_DONE.md;
16_PRODUCTION_TELEMETRY_BACKEND.md; 18_STRUCTURED_LOGGING_STANDARD.md;
22_RELEASE_CONFIG_CORRELATION_CI.md.

Roadmap M7 builds summaries, active/timeline query, retention/TTL, RBAC and
multi-instance strategy. Its two explicit exits are run_id retrieval and active-run
support independent of shell logs. Production cross-instance Cloud SQL validation
remains distinct from disposable local database validation. Doc 05 illustrative
usage/cost fields do not authorize an M7 financial ledger: M10 owns that work.

### Objective, current-state audit and reuse map

Implement later a durable, safe diagnostic projection of the existing canonical
runtime and protected read APIs. Cloud SQL/Postgres is durable operational truth;
TurnTrace/ChatService/M6 remain execution truth. Database state is never an input
to evidence, routing, approvals, authority, provenance or the business response.

| Inspected source | Actual mechanism and M7 reuse |
|---|---|
| observability/stages.py, errors.py, schemas.py, redaction.py | One canonical stage/status/error vocabulary, strict identifiers, finite metadata and inert Role/Permission ceiling. Reuse; do not add STALE as a RunStatus. |
| observability/config.py, runtime.py, exporters/, metrics.py | M1 process-owned SDK leases, bounded export/shutdown, resource release metadata and read-only effective config. Health currently has only trace/metric/log signals; projection health needs its own bounded owner. |
| api/chat_service.py execute_turn_events | EventSequencer creates server run UUID; one background driver owns lock, generator, cleanup and finish/backstop. Relay disconnect is independent. Root finishes after controller.stop and lock release. Keep this ordering. |
| observability/turn_trace.py | TurnTrace owns lifecycle, candidate failure and exactly-once finish. Late event/progress/bind calls ignore terminal turns. Snapshot is immutable Progress, but lacks error/config/version/UTC deadline fields needed for durable projection. |
| observability/active_runs.py | WeakValueDictionary cache, capacity 2048, finished entries discarded, no terminal history or list API. Retain cache; never use it as cross-instance query fallback. |
| observability/agent_instrumentation.py | Execution currently changes _executions directly then calls turn.progress on begin/finish. Add small centralized TurnTrace notification seams to capture agent/tool transitions atomically; no repository imports in this producer. |
| observability/dependency_instrumentation.py | TurnTrace.add_dependency/remove_dependency owns bounded live children; snapshots include dependency, operation, parent/span, agent/tool, start and elapsed. Preserve actual gateway classification, not presumed direct Graph. |
| observability/watchdog.py, deadlines.py, blocking_work.py | One M6 monitor changes STALLED/RUNNING directly; bounded cleanup and absolute monotonic budgets; late workers discard mutations. Feed safe notification into the same projection boundary. Do not add a watchdog or alter deadlines. |
| observability/model_instrumentation.py, model_usage.py | One unsampled physical-attempt observation sink, no durable ledger. Leave unchanged; M7 does not subscribe to usage for accounting. |
| api/run_trace.py | RunTraceRecorder owns sanitized trace.step SSE with numeric allowlist and consecutive deduplication. Presentation labels/recorded_steps are not the durable timeline or lifecycle truth. Preserve SSE and do not ingest those dictionaries wholesale. |
| api/session_service.py | ADK owns session schema keyed by app/user/session and existing locks. SQLAlchemy observation already wraps ADK without granting a reusable public session factory. Never access ADK private engines. |
| cases/db.py; attachments/repository.py; knowledge/repository/sqlalchemy.py | Separate application engines and async sessionmakers, explicit-URL test seams. Cases/attachments use session DB; knowledge has separate resolver. Some repositories lazily create_all; do not copy automatic production DDL into observability. |
| alembic/env.py, versions/ | Application metadata list plus derived table allowlist excludes unrelated/ADK tables. URL resolution may access Secret Manager even offline. Head from dependencies is 7c4e9a2d1f05, not filename order. |
| api/app.py, errors.py | Existing create_app, one lifespan (mandatory PostgreSQL gate before warmup), safe errors and liveness /health. New APIRouter belongs here; observability health must not change application liveness. |
| api/identity.py, tests/test_api_identity.py | UserContext contains only user_id. Unverified X-SLOPANOC-DEV-USER and stable default are explicitly development-only. No authenticated roles or reliable production ownership exist. |
| config/settings.py; api/runtime_database_policy.py | Settings owns environment and secret resolution. Normal runtime requires PostgreSQL/database sessions; SQLite/memory permitted only through explicit test substitutions. Preserve this gate. |
| Existing observability/runtime/lifecycle/reliability/API security tests | Reuse offline doubles, independent cancellation, late-worker, queue, session/approval/egress/rewind/SSE regressions; do not mistake conftest alone for network isolation. |

### Durable schema and identity ownership

Use an observability-owned DeclarativeBase, three small logical tables. No
high-volume span store, generic payload column, session-state copy or ledger.

1. **observability_run_status**: one current row per trusted run_id (primary key).
   Nullable trace_id/root_span_id until native telemetry exists; nullable turn_id
   until observed, then immutable turn_id_origin (adk/execution). Restricted
   session_id is validated server session identity, never an authorization proof.
   Fields: environment; canonical status/current_stage; current_agent/current_tool;
   bounded current_dependencies (maximum 128 validated structural descriptors,
   no arbitrary JSON); current_span_id; started_at/stage_started_at/last_progress_at;
   heartbeat_at/observed_at/updated_at; elapsed_ms_at_observation;
   work_deadline_at/total_deadline_at; stalled/stalled_at/ever_stalled;
   terminal_at/error_code/error_stage; cleanup_status; delivery_status;
   state_version; producer_instance_id (random process-incarnation UUID);
   service_version/git_sha/release_id/cloud_run_revision/region;
   config_version/telemetry_schema_version; timeline_truncated_count;
   projection schema version and retention class/expiry_at.
2. **observability_turn**: one terminal operational summary keyed by run_id,
   committed with terminal status and final bounded timeline. Copy only identity,
   canonical result/error, start/terminal/duration, safe release/config correlation,
   cleanup/delivery outcome and retention class/expiry_at. No costs/tokens/ledger,
   speculative TurnPlan objects or business content. Query uses status row while
   active, summary after terminal status TTL expires. Genuine safe governance
   metadata can be added only from an already-authoritative typed producer and
   justified within M7; absent data remains unavailable.
3. **observability_run_event**: bounded transition timeline, key (run_id,event_seq),
   no FK to session/ADK tables. Summary/status IDs join through run_id. Columns:
   event_seq/state_version/event_type, UTC timestamp, elapsed offset/duration when
   measured, canonical stage/status, safe agent/tool/dependency/operation/error
   and narrowly typed numeric metadata. Event types are diagnostic enums documented
   in observability README, not new telemetry stages. Row validity enforced before
   enqueue and DB insert. No arbitrary string labels or opaque payload dictionaries.

Run UUID belongs to EventSequencer; native trace/root span to M1/M2; turn ID and
fallback origin to M2; session ID to session facade. Versions originate from
trusted Settings/runtime resource metadata; do not fabricate missing contract,
model, prompt or TurnPlan versions. No user_id/chat/case/fault duplication needed
for the initial operator-only design. No ID becomes a metric label.

Store timezone-aware UTC instants and nonnegative measured elapsed offsets.
Never persist M6 absolute monotonic clock values as cross-process timestamps.
At controller binding, pair UTC and monotonic reference values once and translate
both work/total deadlines; retain measured elapsed duration. API projects ages
using UTC reference with clock-skew grace and clamps negative ages. Cleanup and
relay are bounded observations separate from the canonical terminal outcome.

Initial indexes: run_id PK; (environment,status,heartbeat_at,run_id) for active
classification; (session_id,started_at,run_id) for required session lookup;
(environment,terminal_at,run_id) on summary for recent terminals;
(expiry_at,run_id) for bounded cleanup; (run_id,event_seq) timeline PK.
Do not add speculative revision/stage indexes before local query evidence.

### Single projection boundary and bounded write strategy

A typed projection publisher associated with TurnTrace emits detached immutable
snapshots under its existing state lock. It owns a monotonically increasing
state_version and event_seq, not a second lifecycle. Minimal notification adapters
for execution registration/removal, dependency mutation, watchdog stall/resume,
controller binding/cleanup and relay outcome converge here. No repository writes
in agents, models, tools, ChatService stages or dependencies. Snapshot callbacks
never await DB/network, never export raw runtime objects and never grant authority.

One process-owned async persistence coordinator, initialized through existing
FastAPI lifespan, writes via repository sessions. It is independent of trace
sampling/exporter enablement. Persistence enablement is an explicit Settings
policy; production enabled operation requires PostgreSQL, never silent memory
fallback. Dedicated application-owned bounded pool targets the existing session/
case Cloud SQL resolver. Prefer pool_size=2/max_overflow=0, one writer and bounded
read concurrency initially; configurable and locally measured. No new database,
ADK private engine, remote schema auto-create or import-time connection.

Provisional configurable policy (implementation must validate types/relationships):

| Policy | Proposed local default | Meaning |
|---|---:|---|
| Minimum normal write spacing | 1 second per run | Coalesce frequent stage/agent/tool/dependency transitions; keep bounded significant timeline. |
| Durable checkpoint interval | 15 seconds | At most one heartbeat write per interval; unrelated to M6 1s monitor/5s SSE activity. |
| Stale heartbeat threshold / clock grace | 60 seconds / 5 seconds | Must exceed checkpoint/write/retry envelope; diagnostics only. |
| Admitted projection run slots | 2048 | Bound memory for all outstanding per-run normal/terminal envelopes. |
| Pending timeline per slot / retained per run | 128 / 128 events | Explicit truncation count and reserved significant events. |
| Transaction attempt budget | 1 second | Independent background persistence budget, no user-turn budget extension. |
| Terminal attempts / total retry window | 3 / 5 seconds | Bounded idempotent retry with jitter/backoff inside total. |
| Persistence shutdown flush | 5 seconds | Process deadline, cancellation/disposal included, never additive to turn cleanup. |
| Terminal current-state TTL | 24 hours | Separate from 30/90-day summary/timeline retention. |

These are proposed tuning values, not approved production SLOs. Publish effective
values/source/config_version read-only; no runtime settings mutation endpoints.

Triggers: start; identity/controller binding; material stage/agent/tool change;
blocked-child change; STALLED/resume; periodic durable checkpoint; canonical
terminal result; cleanup/relay observation. Progress/token events may advance
in-memory progress, but write at most at checkpoint/coalescing boundaries. Checkpoint
must not call M6.material or reset last_progress. No timeline event for heartbeat.

One slot per admitted run contains latest normal projection and bounded events;
terminal has a reserved envelope within that same slot and priority over normal
work. Once sealed, stale normal envelopes are discarded. Start + final terminal
can insert atomically if start persistence failed. No unbounded task-per-update or
retry queue. On capacity exhaustion, first discard/coalesce superseded normal
updates, retaining reserved terminal envelopes for admitted runs. If no slot can
be admitted, record explicit admission/persistence loss and continue the business
turn; do not claim terminal durability for that run. The saturation test must
prove admitted terminal updates cannot be displaced. Any unavoidable loss has
bounded counters and nonrecursive rate-limited health visibility.

### Repository concurrency, terminal and late-worker guards

API -> service -> repository -> injected async SQLAlchemy sessions. Repository
writes current state, accepted bounded events and terminal summary in one
transaction using parameterized expressions. Use Postgres INSERT ON CONFLICT plus
conditional UPDATE/transaction locking semantics; portable test implementation
must preserve the same invariants. DB constraints validate enum/value bounds.

- Only matching producer_instance_id may update an existing run. No new process
  attaches to an old run even if an identifier collision occurs.
- Nonterminal UPDATE requires durable row nonterminal AND incoming state_version
  strictly greater. Older/equal duplicates do nothing, including timeline insert.
- First terminal transition is atomic and authoritative; rejects nonterminal
  writes at any later version. Canonical terminal fields (status/time/error/duration)
  are immutable. Same terminal retry is idempotent; conflicting terminal outcome
  is rejected with finite integrity-health classification, never last-writer-wins.
- Summary and terminal event are created exactly once in the terminal transaction.
  Commit outcome unknown is safe to retry only with identical run/version/event
  keys; no side-effect/business invocation is retried.
- A separate constrained post-terminal observation operation permits only canonical
  cleanup/delivery fields from their captured owner, increasing observation version.
  It cannot change lifecycle/current agent/tool/dependency, terminal facts or append
  running events. Relay disconnect after business completion stays delivery-only.
- Terminal clears active execution/dependency fields. Publisher ignores late worker
  lifecycle/progress notifications once M2 is terminal; repository guards remain a
  second defense if an already queued out-of-order update arrives.
- Protect publisher sequencing and snapshot capture under the same TurnTrace lock;
  no await under that lock. Retry scheduling cannot reorder accepted state/events.

Terminal persistence is scheduled nonblocking by M2 finish/backstop before cache
removal, after the canonical result is fixed. Acknowledgement belongs to coordinator,
not an extra awaited cleanup in ChatService. Terminal retry and process shutdown
share bounded envelopes; cancellation never changes business outcome. Final flush
attempt runs within coordinator deadline. DB unavailability can still lose latest
state on process death: explicit limitation, health degradation, no accounting-grade
outbox promise. M6 cleanup reserve/lock release is never prolonged by M7.

Projection health owns bounded counters for attempts/failures/retries/coalescing,
admission/event loss, backlog, terminal pending/unpersisted and incomplete shutdown;
last success and finite degradation reason. Keep only registered finite labels
(environment/operation/status); no IDs or exception text. If exported via OTel,
extend application metric/log vocabulary and Collector allowlists together. Never
instrument projection DB calls as business dependencies feeding their own publisher;
use detached/no-turn context and dedicated health to prevent recursion. Exporter
health remains M1-owned; configured Collector is not evidence of live delivery.

### Stale-run, process death and restart semantics

Keep canonical stored status PENDING/RUNNING/STALLED until its real owner reports
terminal. Read-time diagnostic classification is separate: ACTIVE, STALLED, STALE,
TERMINAL, with outcome_unknown for stale nonterminals. STALE is not a business
FAILED/TIMEOUT status and is not an extra M6 terminal owner.

Classify stale when now exceeds heartbeat_at + stale threshold + grace OR total
UTC deadline + grace, absent an accepted terminal. Include classification_as_of,
last durable observation, source=durable and freshness. An overdue observed cleanup
is separately visible; do not fabricate completion. DB outage or paused process can
also cause stale classification; it is not proof of process death. Do not query
local cache to turn stale DB records into globally healthy records.

Active endpoint distinguishes ACTIVE/STALLED/STALE with explicit counts/freshness,
excluding terminal rows; it must never advertise STALE as currently healthy.
Checkpoint advances heartbeat only after transaction succeeds. Process incarnation
and revision explain ownership without using revision changes as death proof.
After restart durable records remain queryable, new runs have fresh server UUIDs
and incarnation, no automatic business resumption. Optional recovery sweep marks
only stale diagnostic metadata/expiry, never invented business terminal outcomes;
read-time classification is the primary mechanism and needs no new remote job.

### Timeline, retention and deletion design

Bounded timeline records start; stage/agent/tool transitions; blocked dependency
changes; stall/resume; canonical timeout/cancel/fail/complete; cleanup issues and
safe delivery outcome. Events have deterministic event_seq with UTC/offset data;
no per-token, watchdog wakeup, helper, SQL or SDK payload history.

Retain at most 128 events per run; reserve 8 slots for start, latest stall/resume,
terminal and cleanup/delivery observations. On overflow compact older ordinary
transition events with an explicit truncation count; do not claim full waterfall
reconstruction. Protect start/terminal and bound repeated exceptional episodes by
keeping their counts/latest episode. Deep trace history remains Cloud Trace.

Retention: terminal status cache 24h (configurable short TTL proposed because docs
specify no exact active TTL); terminal summary and associated timeline 30 days for
ordinary successful runs; 90 days for errors/timeouts/cancelled/ever-stalled/governed
operational runs. 30/90 follow fixed diagnostic trace targets conservatively; DB
mapping is an explicit M7 policy, not a newly claimed authoritative day count for
active state. Capture governed classification only if an existing safe typed
producer proves it; otherwise classify retention conservatively as exceptional.
No financial history or financial retention policy in these tables.

Unresolved stale runs retain unknown outcome, become exceptional diagnostics and
expire 90 days after the later of deadline or last accepted checkpoint; they never
remain active forever and never get a fabricated terminal timestamp. Retention
sweep is bounded keyset/batch (default 500, maximum 1000), transaction timed, dry-run
counts possible, test-clock injectable. Do not delete healthy active rows. Timeline
expiry follows summary, not the 24h status row; prevent orphan events with summary/
run ownership checks and transactional cleanup. Restart does not reset TTL.

Session deletion must not leave a hidden conversation copy (none stored). For M7,
retain policy-permitted content-free run diagnostics with session correlation
removed when a trusted deletion operation occurs; define a repository detach
operation and document deletion integration before changing an existing deletion
flow. Current app has no session-delete route; no invented route or business
retention workflow. Rewind does not delete/rewrite observability history. Legal
hold/enterprise retention exceptions require secure policy and remain explicit.
No scheduled production deletion/infrastructure provisioned in this pass.

### API, DTO, filtering and error design

Add an APIRouter under existing create_app; route dependencies resolve verified
principal, enforce capability/environment scope BEFORE querying, then call service.
Strict Pydantic response DTOs explicitly project fields per permission; never
serialize ORM objects, Progress.__dict__, runtime config or recorded_steps.

| GET endpoint | Planned data / permission |
|---|---|
| /api/observability/runs/{run_id} | Active/terminal safe summary by UUID; OPERATIONAL_METADATA; deeper fields require TECHNICAL_DIAGNOSTICS. |
| /api/observability/runs/{run_id}/timeline | Bounded content-free transition page; OPERATIONAL_METADATA. |
| /api/observability/sessions/{session_id}/runs | Required doc 05 endpoint; restricted session ID, operational capability and environment scope; no conversation fetch. |
| /api/observability/active | Durable ACTIVE/STALLED/STALE listing with freshness; OPERATIONAL_METADATA. |
| /api/observability/runs | Recent bounded run/terminal listing for support and performance validation; OPERATIONAL_METADATA. |
| /api/observability/health | Safe subsystem state, process-local writer health explicitly labeled and durable stale count as-of; OPERATIONAL_METADATA. |
| /api/observability/config | Existing safe effective config plus persistence/retention values; EFFECTIVE_CONFIGURATION. |

No POST/PATCH/DELETE, cancellation, configuration changes, unrestricted trace URLs,
financial endpoints or normal-user diagnostic lookup in M7. Trace_id/root span are
safe restricted correlations gated by technical capability; absent native trace
is explicitly unavailable. Operator DTO includes run/status/classification/stage,
agent/tool/safe dependency, elapsed/progress/deadline, cleanup/delivery, safe error
code and freshness; session identifiers only in explicitly scoped authorized
responses. Developer/SRE adds technical trace/release/config/producer metadata.
Admin is not automatically Developer/SRE; no permission inheritance invented.
Cost and later governance sections report capability/data unavailable, not zeros
or made-up summaries.

Default page size 50, maximum 100. Keyset pagination with bounded validated opaque
cursor carrying endpoint/filter scope and last (started_at,run_id) for listings,
(event_seq) for timelines; upper as-of boundary keeps traversal deterministic.
No arbitrary sort. Mutable active lists explicitly describe point-in-time freshness,
not snapshot isolation across pages. Cursor max 512 bytes; reject malformed,
wrong-scope/out-of-range inputs. Filters only allowlisted status/classification,
stage, environment, canonical error code and UTC time range (maximum 90 days).
UUID run input; session Identifier bounds; environment authorization before filter;
no arbitrary SQL expressions. Query deadlines/read concurrency bounded.

Reuse SafeError envelope: absent/unverified identity 401, insufficient verified
capability 403 before lookup, unavailable or out-of-scope run 404, malformed input
400, bounded repository unavailability 503, unexpected internal failure 500. Static
safe messages; never DB SQL/DSN/error repr/tracebacks or raw validator input. Check
existing global validation handler behavior for new DTO/cursor validation and keep
M7 messages static without unrelated handler refactors. Rate-limit diagnostic
reads with bounded server-side principal buckets plus global concurrency admission;
keys come only from verified principal, never free-form headers. Default 60/minute
per principal is provisional policy; return safe 429 and do not create unbounded
bucket memory. Gateway/distributed limits remain later deployment configuration.

### RBAC and identity limitation

Reuse schemas.Role, Permission and ROLE_PERMISSIONS as ceilings, not authentication.
Do not edit existing UserContext or interpret its dev header/default as verified.
Add a narrow observability principal dependency with typed verified principal,
server-owned capabilities and environment scopes. Default provider denies access
(401). A future real authenticated provider must validate identity/role mapping;
M7 tests inject trusted principal through FastAPI dependency overrides only. No
env flag, client username/header/token-as-string or frontend claim can enable it.

| Role | M7 policy |
|---|---|
| User | Existing safe chat progress only; new diagnostic endpoints denied. Own-run lookup deferred until verified ownership exists. |
| Operator | Scoped operational summary/active/session-list/timeline/health; no deeper technical fields/config. |
| Developer / SRE | Scoped operational + technical diagnostics, no automatic EFFECTIVE_CONFIGURATION permission. |
| FinOps | All new M7 technical endpoints denied; financial capability is not operational access. |
| Admin | Scoped operational metadata and read-only effective configuration; technical capability only if separately granted within ceiling/policy. |
| Auditor | Existing ceiling is GOVERNED_SUMMARIES only. M7 has no trustworthy durable governed-summary producer; deny listed operational endpoints and explicitly report audit view unavailable. Do not broaden into Operator. |

Identity is a **production exposure blocker**, not a reason to invent auth or
halt safe local schema/repository/API implementation. M7 local readiness means
fail-closed endpoints with role-scoped behavior validated using trusted doubles;
it does not mean production usable APIs. Production API acceptance and M8 readiness
remain NO until a verified principal integration and required live validation are
actually available. Do not weaken these exits or silently count role fixtures as
production authentication. Selecting/configuring an identity provider is separate
work; no OAuth/IAM/Entra mutation is implied.

### Files expected to change during separately authorized implementation

New:
- backend/observability/models.py — owned Base and three bounded diagnostic tables.
- backend/observability/database.py — injected engine/factory, bounded pool, no DDL.
- backend/observability/projection.py — strict immutable DTOs/sequence publisher.
- backend/observability/repository.py — guarded atomic writes/queries/retention.
- backend/observability/persistence.py — bounded coordinator/coalescing/terminal flush.
- backend/observability/service.py — safe authorized query/freshness policy.
- backend/observability/api_models.py — role-specific allowlisted response DTOs.
- backend/observability/authorization.py — verified-principal seam, default deny.
- backend/observability/retention.py — pure retention/sweep policy.
- backend/observability/persistence_metrics.py — bounded projection health.
- backend/api/observability_routes.py — protected read APIRouter.
- alembic/versions/9f71c2a64e08_observability_storage.py — proposed additive revision
  9f71c2a64e08, down_revision=7c4e9a2d1f05; recheck head/collisions before authoring.
- backend/tests/test_observability_repository.py.
- backend/tests/test_observability_persistence.py.
- backend/tests/test_observability_api.py.
- backend/tests/test_observability_migrations.py.
- backend/tests/test_observability_retention.py.
- backend/tests/test_observability_projection_performance.py.

Modify narrowly:
- backend/observability/turn_trace.py; active_runs.py — publisher attachment and
  detached complete safe projection, preserving cache/lifecycle behavior.
- backend/observability/agent_instrumentation.py; dependency_instrumentation.py;
  watchdog.py — centralized safe change notifications, no direct DB coupling.
- backend/observability/config.py; backend/config/settings.py; schemas.py — typed
  policy/accessors/effective config; additive operational/persistence schema only.
- backend/observability/runtime.py; metrics.py; attributes.py; redaction.py;
  logging.py — own bounded health/registered metadata where needed; no SDK duplicate.
- backend/api/app.py — router/lifespan coordinator injection and bounded close.
- backend/api/chat_service.py — only publisher/controller attachment seam if needed;
  retain finish/backstop/lock/relay ownership and never await DB per progress.
- alembic/env.py — add observability metadata with derived allowlist preserved.
- infra/observability/collector/config.yaml and config.local.yaml — exact safe
  projection health registration if emitting new instruments/log fields.
- backend/observability/README.md; .agent/OBSERVABILITY_EXECUTION.md — contracts,
  validation/status and safe activation/deployment documentation.
- Existing affected active_runs/turn_trace/reliability/settings/Collector/root and
  API security tests — regression additions around actual integration seams.

No frontend/shared client change required by current conventions. No agents/tools/
knowledge/governance/command code rewrite; no M8 screens, M9 SLO engine, M10 ledger,
new package installs, production IAM/auth config or Terraform state changes. Update
this manifest before any additional path is necessary.

### Migration and local validation strategy

Author one additive Alembic revision with bounded constraints/indexes and
observability-only downgrade. Extend metadata/table filtering; never import/manage
ADK schema. No startup create_all in production. Missing/unmigrated tables degrade
projection health and give safe API 503, never cause runtime auto-migration.

Existing env.py resolves Settings URL even in offline SQL mode: do not run bare
alembic upgrade/autogenerate/--sql in inherited environment. Tests must explicitly
inject disposable DB/Settings, strip credential/secret/proxy config, block external
network and disable warmup BEFORE imports. Inspect generated Postgres SQL offline
with explicit inert URL; test clean upgrade/head/downgrade/re-upgrade on isolated
SQLite and on disposable local Postgres. Preserve seeded unrelated/ADK tables and
prior revision data. SQLite is useful for portability, insufficient to certify
Postgres ON CONFLICT/locking/JSON/timezone/query-plan behavior.

Only use a locally disposable PostgreSQL instance whose binary/container, loopback
or Unix socket, credentials, port, volume and DB creation are demonstrably isolated;
no inherited DSN, Cloud SQL proxy or shared localhost tunnel. If none is available,
record migration/concurrency Postgres gates NOT RUN and do not mark acceptance PASS.
Tests may initialize observability schema explicitly through fixtures only.
No database connection or migration was attempted during this planning pass.

Before implementation validation, re-audit existing temporary guard
/private/tmp/slopanoc-m5-test-guard/run_tests.py and sitecustomize.py. They exist,
strip SLOPANOC/cloud/proxy config, force memory DB and deny non-loopback sockets;
loopback allowance alone is not proof of isolation. Adapt a temporary M7 runner
for the explicit disposable Postgres fixture only, with no cloud credentials or
reachable inherited services. Keep guard/test DB contents outside repository.

Planned commands after implementation (not run in this planning pass):

```text
.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_observability_repository.py backend/tests/test_observability_persistence.py backend/tests/test_observability_api.py backend/tests/test_observability_migrations.py backend/tests/test_observability_retention.py
.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_observability_projection_performance.py
.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_observability_reliability.py backend/tests/test_observability_active_runs.py backend/tests/test_observability_turn_trace.py backend/tests/test_chat_service_root_trace.py backend/tests/test_api_identity.py backend/tests/test_api_security_contract.py backend/tests/test_api_execution_endpoints.py backend/tests/test_api_approval_endpoints.py backend/tests/test_approval_security_contract.py backend/tests/test_single_invocation_command_authority.py backend/tests/test_command_egress_boundary.py backend/tests/test_chat_service_rewind.py
.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py --manifest /private/tmp/slopanoc-m7-regression-manifest.json
.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_observability_repository.py backend/tests/test_observability_persistence.py backend/tests/test_observability_api.py -W error::pytest.PytestUnhandledThreadExceptionWarning -W error::RuntimeWarning -W error::ResourceWarning -W error::sqlalchemy.exc.SAWarning
.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_observability_collector.py
.venv/bin/python -m compileall -q backend/observability backend/api
git diff --check
```

Generate/review M7 regression manifest from existing M5 211-file affected manifest
plus all M0–M7 tests and new API/identity/migration/security regressions; record exact
selection/results. Recheck every path exists before executing. Collector tests are
local-only, may need loopback sandbox approval; never a cloud approval shortcut.
Frontend tests/build only if scope legitimately introduces shared frontend changes.
Evaluate the doc 01 engineering targets: typical instrumentation CPU overhead
below 3% and synchronous instrumentation overhead below 10ms, excluding provider/
export network. Report enqueue/coalescing distributions and baseline separately
from background DB time; local evidence cannot certify production overhead.

| Blocking validation scenario | Required proof |
|---|---|
| Durable lifecycle | Start, stage, identity, agent/tool/parallel dependencies, STALLED/resume and all four terminals persist and remain queryable from a different coordinator/service instance. |
| Ordering/idempotency | Version 10 RUNNING -> 11 COMPLETED -> older/duplicate/higher-version nonterminal and conflicting terminal; DB/summary/events cannot regress or duplicate. Atomic rollback and unknown commit retry included. |
| Real M6 late worker | Block synchronous tool, caller times out, canonical root terminal persists; release worker later; status/time/error/current fields unchanged, no new running event, executor capacity and authority semantics retained. |
| Restart/staleness | Drop process-local registry, query same DB from fresh incarnation; expired heartbeat/deadline becomes STALE + unknown outcome, clock grace and fresh checkpoints tested. No resume or synthetic FAILED. |
| Projection DB outage | Actual fake business turn completes safely, M6 independent timer/monitor still enforce; bounded retry/queue/memory/rate-limited safe health, no recursion or exception leak. |
| Saturation | Superseded normal updates coalesce, terminal reserved envelope wins, event truncation visible; admission exhaustion explicitly reports unpersisted, no unbounded tasks/queue. |
| Shutdown/cleanup | Empty/normal/terminal backlog and hung/unavailable DB; measured shutdown bound; original outcome and session lock release preserved; incomplete flush visible. |
| Authentication/RBAC | Each endpoint: anonymous/dev-header/spoofed claims denied; every canonical role allowed/denied as above; trusted override only; per-field and environment restrictions, FinOps/Auditor isolation, 404 enumeration boundary. |
| DTO/privacy | Sentinels for prompts/responses/Teams/commands/tool payloads/SQL/URLs/tokens/attachments/knowledge/reasoning in surrounding context and rejected inputs; none in DB/API/logs/Collector; strict nested dependency bounds. |
| Pagination/security | Page caps, deterministic keysets/event order, filter-scoped cursor/as-of, oversized/malformed IDs/cursors/time/enums, parameterized injection probes, bounded throttle/read slots, safe errors. |
| Retention/deletion | Test-clock 24h/30d/90d tiers, ever-stalled/governed unknown conservative policy, stale expiry, bounded sweep, timeline survives short status TTL, no healthy active deletion, detach session identity, rewind unchanged. |
| Schema/Postgres | Clean upgrade/downgrade/re-upgrade; preserve unrelated tables/data; no remote resolver/DDL; real disposable Postgres concurrency/locking and two writer/read instances. |
| Performance | Hot-path enqueue and coalescing separately from DB writes; measured write-count ceiling under token/tick storms; representative run/active/recent-terminal/timeline queries, indexed query plans; distribution/method/data size/baseline reported locally. |
| Regression | M0–M6 lifecycle/models/agents/tools/dependencies/reliability plus governance/approval/command/egress/provenance/session/rewind/SSE/storage/DB and API security; privacy vocab and Collector parity. |

### Implementation sequence for future authorization

1. Recheck unchanged M6 gate, working-tree hashes, migration head and current
   identity limitations. Record implementation authorization and exact manifest.
2. Build strict projection/schema/UTC references and observability-only migration;
   validate inert imports and isolated schema before any app wiring.
3. Implement transactional version/terminal/event/summary repository guards and
   retention/read-time classification; prove Postgres semantics locally.
4. Add bounded coordinator and one publisher seam; minimal M2/M4/M5/M6 notifications,
   reserved terminal envelopes and health; preserve disabled-export runtime.
5. Add DTO/service/deny-default principal/RBAC/router; test every role and field.
6. Integrate sole app lifespan/start/shutdown with no DDL or implicit API access;
   run actual lifecycle/late-worker/outage/backpressure/cleanup regressions.
7. Run isolated complete acceptance/regression/privacy/performance matrix, inspect
   hashes/diffs/untracked files, record failures/repairs and evaluate every exit.
8. Stop at M7. Required production identity/live validation remains explicit;
   no M8 or remote operation without separate authorization.

### Individually planned exit criteria and acceptance status

All implementation/runtime criteria below are **PENDING / NOT RUN**, not PASS.
The planning criteria are defined and covered above; planning is not completion.

1. Observability-owned durable schema/current state/terminal summary exists, imports
   and migrates cleanly in isolated DB; no unrelated or ADK table touched.
2. Run_id retrieval, active and session-run queries and bounded timeline work from
   independent local process/service instances, without shell logs/affinity.
3. Canonical runtime/terminal owner unchanged; one projection boundary, no DB
   dependency for every progress event; bounded frequency/heartbeat/memory proven.
4. Older/out-of-order/duplicate updates rejected; first terminal immutable;
   post-terminal cleanup/relay updates narrowly limited and idempotent.
5. Real M6 late worker cannot regress durable result or append active events.
6. Restart/stale records classified explicitly without invented business result.
7. Projection outage does not fail safe business turns or disable M6; bounded
   health/degradation/terminal-loss visibility, no recursion/log flood.
8. Terminal priority, bounded retries/final attempt and shutdown flush proven;
   existing total deadline/cleanup/lock/cancellation behavior preserved.
9. Every API enforces deny-default verified-principal/capability/environment checks;
   safe field projection and canonical role ceilings; no dev/header auth bypass.
10. Allowlisted DTO/DB/log/Collector privacy, parameterized queries, capped pages,
    deterministic pagination/filter validation, safe errors and admission limits pass.
11. Configurable TTL/retention/cleanup/deletion interaction documented and locally
    tested, deep trace/accounting stores remain separate.
12. Real isolated Postgres transaction/concurrency/migration semantics validated;
    SQLite/test doubles alone do not satisfy that gate.
13. Applicable M0–M7 regressions/import checks and local overhead/query benchmarks
    pass; no blocking defect or unimplemented M7 architecture requirement.
14. No business authority/evidence/routing/response consumes observability DB; no
    M8 UI, M9 SLO engine, M10 ledger or unauthorized external mutation.
15. Production exposure: verified identity integration and Cloud SQL cross-instance
    runtime retrieval are actually validated after exact-operation approval.
    NOT VALIDATED by local fixtures. Production-wide pending DoD remains separate.

M7 local implementation may eventually be reported IMPLEMENTED AND LOCALLY
VALIDATED once local gates pass, but **Ready for Next Milestone remains NO** while
required production identity/live Cloud SQL acceptance is unmet. This plan does not
waive a criterion or assume production operations are authorized.

### Settings / UI impact: five-part evaluation

1. Creates safe backend data for Overview, Tracing, Reliability, Data & Retention,
   Integrations, Access and Diagnostics within existing Settings shell.
2. Required contracts are allowlisted summaries, timeline, freshness/stale status,
   safe health/read-only config and server permission/capability availability.
   Cost/SLO/governance views are explicitly unavailable pending their producers.
3. Backend-only M7; no visual Settings/chat/progress/SSE redesign or frontend client
   generation required. Existing RunTraceRecorder/public protocol stays intact.
4. Server RBAC applies to every endpoint/field/environment. Current dev identity
   cannot authorize own-run or admin reads. FinOps gets no technical/content access.
5. IaC/environment/secure backend values are effective read-only even for Admin;
   no Settings writes or Terraform bypass.

### Cloud / infrastructure status and pending operations

Planning = COMPLETE. M7 implementation = NOT IMPLEMENTED. Runtime validation =
NOT RUN. Cloud deployment = **NOT EXECUTED — USER APPROVAL REQUIRED**.
No cloud/shared DB/Graph/Power Automate/IAM/billing/storage/secrets/Terraform-state
operation is proposed for execution now. Planning performed only local reads and
this documentation edit. No gcloud/terraform/alembic/database/network operation.

Future required operations, NOT AUTHORIZED: apply the new observability Alembic
revision to the verified existing Cloud SQL database; deploy the backend containing
M7 APIs/projection policy to the exact Cloud Run service/revision; integrate verified
principal validation/mapping (any external identity/IAM/OAuth configuration requires
its own approval); update Collector registration if new health signals are emitted;
approve any production retention scheduler separately if needed. No new billing,
BigQuery/GCS/secret/DB-user/IAM resource assumed. Existing M1 rollout remains separate.
Target project/account/service/database/tenant and DEV/STAGING/PRODUCTION are not
assumed or inspected. Revision ID and deploy command cannot be exact until code
exists. Before any operation, prepare the concrete command/target/resources, reason,
operational/security/cost impacts, lock/data-loss/downtime risk and rollback under
AGENTS.md; wait for operation-specific approval. No generic continuation grants it.

Migration impact will be additive tables/indexes only, brief schema lock/catalog
load, bounded storage/write/pool overhead, no business data migration; verify exact
DDL before proposing. Recovery: disable only projection via approved deployment,
retain safe diagnostic data and roll back application; destructive downgrade must
have its own approval and deletes observability history. Runtime retention needs
an explicit reviewed schedule; no infrastructure is provisioned as part of planning.

### Risks, limitations and planning-pass validation

- Verified production authentication/roles are absent. Fail-closed local APIs are
  feasible; production usability/acceptance and M8 progression are blocked.
- Disposable local Postgres availability is not yet established. Validate isolation
  first; if unavailable record Postgres gates NOT RUN rather than use shared DB.
- DB outage/process death can lose unacknowledged operational projection; final
  durability is best effort with bounded priority/retries and explicit health.
  This is not the accounting-grade M10 durability contract.
- Cross-instance state is eventually consistent, with explicit heartbeat/freshness/
  staleness semantics. UTC skew and shared pool headroom need local and approved
  production validation. Proposed intervals/capacities are not production tuning.
- Retention TTL is proposed where docs leave exact current-state count open;
  legal/audit exceptions and automated production scheduling remain deployment policy.
- Timeline is intentionally bounded and reports truncation. No claim to full trace
  reconstruction without the separate trace backend.

Actual file changed in this pass: **.agent/OBSERVABILITY_EXECUTION.md only** (program
status, M7 matrix row and this plan). Existing local changes remain authoritative.
Pre-edit SHA-256 baseline: 823 repository files, recorded outside repository at
/private/tmp/slopanoc-m7-planning-baseline.json. Final audit PASS: only this record
changed, no baseline file removed and no new repository path created. Git status
(78 existing entries), tracked diff/stat, untracked paths and document changes were
inspected; git diff --check PASS. Because .agent is already untracked, content/hash
comparison validates this edit independently of tracked git diff output.
Planning validation: source/specification/gate inspection, API/schema/RBAC/retention/
privacy/queue/terminal requirements coverage; no runtime test or benchmark run.
No implementation failure/repair occurred. Historical suites are not newly validated.

**Ready To Implement M7: YES** — reviewable conservative local plan; separate user
implementation instruction required. This does not resolve production identity,
Postgres availability or deployment gates and grants no cloud permission.
**Ready for Next Milestone: NO. M8 BLOCKED. STOP after planning.**

## M7 implementation authorization — 2026-10-08

User approved local implementation of M7 only. Predecessor local gate rechecked
PASS; approved source/architecture/path manifest remains the contract. Baseline
823 file hashes captured at /private/tmp/slopanoc-m7-implementation-baseline.json.
Current milestone M7 IMPLEMENTING; all acceptance gates PENDING, M8 blocked.
The new request explicitly supersedes planning criteria requiring production
identity/live Cloud SQL and real Postgres as prerequisites for local completion:
default-denied production APIs and strongest isolated DB validation are allowed;
unavailable Postgres-specific validation must be reported unproven. No remote
operation is authorized. In-process operational health (protected API + safe
rate-limited fallback) will be used initially, avoiding new Collector/metric
vocabulary unless needed. No accounting-grade durability claim.

M7 implementation inventory addition before creating tests:
backend/tests/_m7_storage.py provides shared explicit isolated fixture/builders
only, no production/test-mode auto-detection. First regression run found effective
configuration did not recognize the new boolean projection_enabled; this prevented
root trace configuration and was repaired centrally. M6 SimpleNamespace test doubles
also lacked project(); watchdog projection now uses existing guarded notify seam.
No runtime deadline/terminal semantics were altered. Postgres binaries absent;
Docker context is a local Unix socket, but socket/daemon does not exist. No container
or shared DB connection attempted; Postgres-specific validation remains unproven.

M7 privacy review refinement: request session_id is not an authenticated session
identity until the existing session facade successfully loads it. M7 projection
will omit it before that point, then bind it through a guarded notification after
existing session lookup. A private session-detached DB flag prevents future pending
projection updates from restoring removed correlation. This is diagnostic metadata
only; existing M2 correlation and business identity/ownership remain unchanged.

First broad isolated regression: 3,889 passed, 11 failed. Two failures were expected
closed inventories requiring explicit new M7 API paths and detached database-owner
registration; both updated without weakening the guards. Nine failures were sandbox
loopback bind restrictions in existing Collector/OTLP tests (not product failures).
Those tests are rerun separately with local-listener sandbox elevation and the
same credential-stripping/non-loopback-blocking guard. No cloud/service mutation.

## M7 final implementation and local validation — 2026-10-08

Status: **IMPLEMENTED AND LOCALLY VALIDATED**. This report supersedes the earlier
M7 planning-only status and conservative production progression gates in accordance
with the latest explicit user instruction. Runtime M2/M6 state remains execution
authority; persisted rows are diagnostic projections only. No M8 work performed.

### Actual schema and ownership

Additive Alembic head 9f71c2a64e08, parent 7c4e9a2d1f05 verified from the actual
revision chain. Observability owns a separate SQLAlchemy Base and three tables:
observability_run_status, observability_turn, observability_run_event. Alembic
metadata allowlist includes this Base; ADK session metadata remains excluded.
Application startup constructs an optional isolated engine/coordinator without DDL.
Production constructor rejects SQLite; explicit isolated_test fixtures use SQLite.

Current rows contain bounded run/native trace/session correlation (session only
after existing facade confirmation), canonical status/stage/work names, strict
dependency classifications, UTC start/progress/checkpoint/deadline/terminal times,
measured elapsed values, stall/cleanup/delivery/error states, trusted release/revision,
schema versions and UUID process incarnation. Terminal summary is a smaller explicit
operational schema. Private session_detached prevents delayed updates restoring
deleted session correlation. No content, commands, arbitrary JSON metadata, URLs,
SQL, credentials, prompts/responses or accounting values are stored. Dependency
JSON is revalidated typed DependencyState only. Explicit DB constraints and indexes
cover primary run lookup, active/checkpoint, recent/session, timeline sequence and
expiry queries. Session detach and rewind never turn diagnostics into business state.

### Projection, ordering, terminal and reliability evidence

Central Publisher captures canonical state under its existing lock; normal runtime
code has no repository calls/DB awaits. One detached Coordinator worker owns writes.
Default capacity 2048 admitted runs, including persisted active runs reserving their
terminal slot; one latest pending envelope per run, maximum 128 cumulative events,
weak bounded publisher references and one coalesced detached wakeup. Normal writes
are spaced 1s per run, periodic checkpoint 15s independent of tokens/watchdog ticks.
Material start/stage/agent/tool/dependency/stall/resume/terminal/cleanup/delivery
observations publish; same facts coalesce. Terminal writes outrank routine updates,
including in-flight terminal + pending cleanup/delivery. Saturation rejects new
admission observably; accepted active runs retain terminal capacity. Shutdown/retry
do not replay business execution. Tests exercise 2000-update wakeup coalescing.

Repository uses dialect upsert and conditional owner/version/nonterminal writes.
Terminal row, summary and timeline share a transaction. Version 11 COMPLETED remains
terminal after version 10 RUNNING, higher nonterminal versions and conflicting
terminal outcomes. The first durable terminal's facts/timeline cannot be replaced;
post-terminal changes permit only cleanup/delivery/version/truncation. Protection
still holds after 24h current-state expiry because retained summary is checked.
SQLite concurrent writes and rollback tests pass. Real Postgres concurrency UNPROVEN.

Real ChatService/FakeRunner execution continues during injected repository outage.
M6 BlockingExecutor timeout test persists TIMEOUT before worker release: late
completion cannot change final stage, agent, tool, dependencies or running timeline.
Persistence health counts failed/retried/rejected/conflicting/unpersisted work;
fallback JSON contains fixed classifications, no IDs/errors, rate-limited to 30s.
No recursive observability DB spans, Collector attributes or new metric instruments.

### Staleness, restart, timeline and retention

Central read policy: STALE when last durable checkpoint exceeds 60s + 5s clock grace,
or total UTC deadline + grace passes. Canonical status stays RUNNING/STALLED, never
synthetic FAILED/TIMEOUT/CANCELLED. Stale elapsed freezes at last measured observation.
UTC deadline uses one paired clock reference. Process incarnation cannot take over
old producer records; restarting repository/service preserves diagnostic queries
without recreating live execution, cache affinity or business resumption.

Timeline is ordered by event sequence, maximum 128 significant entries; retained
start/latest critical stall/resume/terminal/cleanup/delivery episodes and explicit
truncation. It is not a complete trace. Bounded repository cleanup max batch 1000:
terminal current rows 24h; confirmed ordinary COMPLETED summary/timeline 30d;
exceptional/stalled/operational/unknown 90d. Classification uses registered safe
operational facts, not content. Stale unknown rows expire after 90d relative to last
checkpoint/deadline. Healthy active rows remain. No production scheduler created.

### APIs, RBAC and production identity gate

Seven read-only GET routes under /api/observability:
/runs; /active; /runs/{run_id}; /runs/{run_id}/timeline;
/sessions/{session_id}/runs; /health; /config.
Routes -> Service -> Repository -> SQLAlchemy; explicit allowlisted DTOs, fixed
keyset ordering, default page 50/max100, filter-scoped bounded opaque cursors,
validated UUID/session/enums/aware UTC windows max90d. Four concurrent read slots,
2s query deadline, bounded 60/min principal limiter. Static safe errors contain no
SQL/DSN/repr/stack. Environment/field scopes enforced before disclosure; lookup
exposure is uniform, no unrestricted trace URLs. Core /health remains independent.

verified_principal_provider returns None; no production verified identity exists.
**Production diagnostics remain inaccessible without a verified server principal.**
No development header, role header, environment flag or frontend claim enables
access. Principal must be verified, valid and within canonical role capability
ceiling and environment scope. Operator gets permitted operational fields;
Developer/SRE explicit technical capability; Admin administrative read/config only
according to existing ceiling. User/FinOps/Auditor have no implicit diagnostic access.
Tests exercise all six roles across all seven paths using test-only dependency
overrides, and all paths deny unverified/malformed/spoofed production claims before
repository queries. These fixtures do not establish production identity readiness.
Config is effective/read-only; process health is withheld outside permitted process
environment. Exporter delivery remains unknown until observed, not inferred from
configuration. No provider integration or IAM mutation was attempted.

### Validation commands, results and repairs

Guard runner /private/tmp/slopanoc-m7-test-guard/run_tests.py strips cloud/business
environment credentials, sets isolated in-memory URLs and disabled warmup/Vertex,
and rejects all non-loopback sockets. No shared SQL connection occurred.

- Broad local regression: `.venv/bin/python <guard>/run_tests.py --manifest
  /private/tmp/slopanoc-m7-regression-local-manifest.json`: **3891 passed**, 481
  existing dependency/legacy teardown warnings, 90.91s, 219 test files.
- Existing loopback Collector/OTLP tests under the same guard with local-listener
  sandbox elevation: **14 passed**, 1 existing Starlette warning, 20.54s. All traffic
  local; no cloud/service resources created.
- Final M0–M6 foundations and API closed inventory: guard runner with
  test_observability_reliability.py, test_observability_active_runs.py,
  test_observability_turn_trace.py, test_chat_service_root_trace.py,
  test_observability_agent_instrumentation.py,
  test_observability_dependency_coverage.py, test_api_security_contract.py:
  **133 passed**, 6 existing ADK deprecations, 6.08s.
- Final M7 strict suite: guard runner with test_observability_repository.py,
  test_observability_retention.py, test_observability_api.py,
  test_observability_migrations.py, test_observability_persistence.py,
  test_observability_projection_performance.py, `-W error::ResourceWarning
  -W error::pytest.PytestUnraisableExceptionWarning`: **100 passed**, no warnings,
  9.47s. Includes the final architectural isolation guard and all final repairs.
- Isolated migration tests pass upgrade/schema/constraints/indexes/downgrade/
  re-upgrade and preserve unrelated fixture tables. Real Alembic environment stamps
  the verified parent in an isolated SQLite fixture then applies M7, because older
  parent timestamp ALTER requires Postgres. Entire real Postgres chain upgrade and
  M7 downgrade rendered offline without connecting; actual head verified. This is
  NOT live Postgres migration/concurrency validation. Local daemon/binaries absent;
  no shared DB fallback.
- Compileall backend/observability, backend/api and M7 migration; git diff --check:
  PASS. Architectural AST guard rejects diagnostic storage imports from business
  routing/tools/approval/cases/context/knowledge/operations and ChatService.
- Privacy sentinels and strict schema rejection cover every prohibited payload
  category; safely persisted rows, API bodies and fallback health contain none.

Repairs: new config boolean typing and missing-runtime projection test doubles;
explicit seven-route/database-owner inventories; invalid test event version;
filter cursor default-time stability; session confirmation/detach monotonicity;
in-flight normal spacing and terminal reservation; post-terminal timeline immutability;
one bounded detached wakeup; scoped process health/stale elapsed. Strict warnings
identified unclosed disposable case/attachment test stores; test-owned resources
now close explicitly. Final strict tests validate those repairs. Initial sandbox
local-bind failures reran successfully in guarded local-listener tests.

### Local performance (provisional, not production capacity)

Five repetitions x1000 hot-path operations, median: no-op 0.036us;
publication/coalescing 119.046us; material progress 0.372us. Isolated SQLite 200-run
dataset, 20 queries/type: write + event 2.153ms, terminal + summary 7.008ms;
repository lookup 0.442ms, active page50 25.054ms, timeline 1.407ms,
service lookup 0.478ms. Query plans use indexes. Background writes do not block
business hot path. No Postgres/Cloud SQL/cloud latency or load validation claim.

### Blocking local exit criteria

| Criterion from user's M7 local exit gate | Result |
|---|---|
| Durable current state exists | PASS |
| Terminal summary exists | PASS |
| Bounded timeline exists | PASS |
| Safe fields only | PASS |
| Centralized publisher used | PASS |
| Hot-path DB dependency avoided | PASS |
| Projection queue bounded | PASS |
| Meaningful updates coalesced | PASS |
| Terminal priority works | PASS |
| State versions prevent stale overwrite | PASS |
| Terminal status cannot regress | PASS |
| Late worker cannot alter durable final state | PASS |
| Stale runs identifiable | PASS |
| Restart does not resurrect execution | PASS |
| Persistence failure does not break business execution | PASS |
| Shutdown flush bounded | PASS |
| Retention works locally | PASS |
| Explicit API DTOs | PASS |
| Bounded API pagination/filtering | PASS |
| Server-side RBAC | PASS |
| Deny by default | PASS |
| Production diagnostics inaccessible without verified principal | PASS |
| Privacy tests | PASS |
| Local migration validation | PASS |
| No business authority consumes observability records | PASS |
| M8 UI absent | PASS |
| M9 SLO engine absent | PASS |
| M10 accounting ledger absent | PASS |
| No cloud/shared DB mutation | PASS |

Production verified identity: **NOT LIVE-VALIDATED**; provider integration absent.
Shared Cloud SQL migration: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Production API deployment: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Production retention scheduling: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Pending external operations, each requiring separate exact-target approval: apply
revision 9f71c2a64e08 to selected shared DB; deploy approved app/config to selected
Cloud Run service; integrate/validate verified principal/role scopes; provision
approved retention execution. Targets not selected and commands not executed.

### Known limitations and UI impact

**Unacknowledged asynchronous projection updates may be lost if the process dies
before persistence.** No transactional outbox, exactly-once/crash guarantee or
accounting-grade durability. DB outages/process death can leave stale or missing
diagnostic records; bounded loss/health reporting is operational, not reconciliation.
Postgres isolation/concurrency/driver cancellation and production pool/latency tuning
remain unproven. Intervals/capacities are provisional. Shutdown relies on cooperative
SQLAlchemy/driver cancellation; worker/process-kill guarantees are not claimed.
Timeline compaction intentionally loses old noncritical trace detail. Production
identity, migration, API access and cleanup scheduling require separate work/approval.

UI rule evaluated: (1) safe run/active/history/timeline/health/effective-config data
supports future Settings -> Observability & FinOps Diagnostics/Overview/Integrations;
(2) explicit protected DTO/API contracts now exist; (3) UI belongs to M8, absent;
(4) server RBAC and environment/field scopes mandatory; (5) configuration view-only,
no mutation endpoint or IaC bypass. No normal-chat UI or unrestricted reasoning added.
No FinOps data/cost values or M9 SLO computation introduced.

**Ready for Next Milestone: YES (local M8 data readiness only). STOP.** M8 NOT STARTED;
separate instruction required. No cloud authorization granted by this result.

### Actual M7 files changed and final hygiene

Existing paths changed during implementation (12):
.agent/OBSERVABILITY_EXECUTION.md; alembic/env.py; backend/api/app.py;
backend/api/chat_service.py; backend/observability/README.md;
backend/observability/agent_instrumentation.py; backend/observability/config.py;
backend/observability/dependency_instrumentation.py; backend/observability/schemas.py;
backend/observability/turn_trace.py; backend/observability/watchdog.py;
backend/tests/test_api_security_contract.py.

New paths (19):
alembic/versions/9f71c2a64e08_observability_storage.py;
backend/api/observability_routes.py;
backend/observability/api_models.py; backend/observability/authorization.py;
backend/observability/database.py; backend/observability/models.py;
backend/observability/persistence.py; backend/observability/persistence_metrics.py;
backend/observability/projection.py; backend/observability/repository.py;
backend/observability/retention.py; backend/observability/service.py;
backend/tests/_m7_storage.py; backend/tests/test_observability_api.py;
backend/tests/test_observability_migrations.py;
backend/tests/test_observability_persistence.py;
backend/tests/test_observability_projection_performance.py;
backend/tests/test_observability_repository.py;
backend/tests/test_observability_retention.py.

Baseline hash audit against 823 preimplementation files: exactly these 12 existing
paths changed, exactly these 19 paths added, zero baseline files deleted. Existing
unrelated local edits preserved. git status, tracked diff and untracked paths
reviewed; compileall and git diff --check PASS. Only source/tests/documentation and
additive migration added; no secrets, runtime payloads, DB contents, production
exports or generated test artifacts added. Test logs, credential-stripping runner,
performance output and hash audit remain outside repo under /private/tmp.


## M8 — Observability & FinOps Settings UI: planning only — 2026-10-08

**M8 — PLANNED — NOT IMPLEMENTED.** User authorization is planning only.
Only this execution record changes. No frontend/backend implementation, test code,
prototype, migration, dependency installation, deployment or external mutation.
M9 remains blocked. Ready for Next Milestone: **NO**.

### Milestone gate and authoritative inputs

M0 final acceptance and M1–M7 final local acceptance sections plus milestone matrix
record all predecessor local completion/validation gates PASS and progression YES.
Actual backend/observability modules, seven protected routes, M7 migration and test
files exist. M7 final evidence includes 3891 regression tests, 14 local Collector/OTLP
tests, 133 foundations tests and 100 strict M7 tests passing. These are historical
results, not rerun in this planning pass. No local gate discrepancy found.
M0 = VALIDATED COMPLETE; M1–M7 = IMPLEMENTED AND LOCALLY VALIDATED, which is the
user-approved local COMPLETE meaning; production completion is not claimed.
Verified production identity, shared Cloud SQL revision 9f71c2a64e08, production API
deployment and retention scheduling remain pending. User explicitly permits local
M8 development while these are pending if production diagnostics remain default-denied.

Read AGENTS.md, .agent/PLANS.md and execution-record gate/completion evidence.
Authoritative specification files read under docs/Telemetry/: README.md;
00_MASTER_BUILD_CONTRACT.md; 01_TARGET_ARCHITECTURE.md;
02_TELEMETRY_DATA_CONTRACT.md; 05_STORAGE_APIS_AND_UI.md;
06_SRE_SLOS_METRICS_ALERTS.md; 07_FINOPS_ARCHITECTURE.md;
09_SECURITY_PRIVACY_RETENTION_SAMPLING.md;
10_DASHBOARDS_RUNBOOKS_OPERATING_MODEL.md; 11_SLOPANOC_INTEGRATION_MAP.md;
12_IMPLEMENTATION_ROADMAP.md; 13_TEST_VALIDATION_ACCEPTANCE.md;
14_CODEX_EXECUTION_GUIDE.md; 15_DEFINITION_OF_DONE.md.
Current user scope resolves roadmap's broad M8 cost/governance/waterfall ambitions:
M8 renders only available M1–M7 safe API data, with truthful future sections.
Full SLO/alerting M9, accounting/billing/allocation/forecast M10–M14, final controls
M15, release/IaC hardening M18, quality/profiling M19 remain later work.

### Objective and existing Settings audit

Extend existing Settings with Observability & FinOps and all nine permanent sections.
Operator must diagnose an isolated deliberately stalled dependency using UI/API alone.
Preserve normal chat, existing safe backend activity labels, SSE, ownership, evidence,
authority, approvals, command egress, session persistence and rewind.

Inspected frontend inventory, src/types.ts, src/state/AppState.tsx Settings actions,
Sidebar.tsx Settings mount, settings/SettingsModal.tsx and all three existing panels;
ui/Dialog.tsx, ProjectSettingsModal.tsx, TaskRunHistory.tsx, CurrentActivity.tsx,
RunTrace.tsx, elapsedTime.ts, API client/config/sessions, CSS, package/Vite/Vitest
configuration and representative tests. Backend inspection: observability routes,
api_models.py, authorization.py, service.py, schemas.py, config.py, projection.py,
API identity and safe-error HTTP mapping; existing source remains authoritative.

| Existing owner | Findings and reuse |
|---|---|
| Sidebar / AppState | Sidebar mounts SettingsModal; OPEN/CLOSE/SET_SETTINGS_SECTION already exist. SettingsSection is usage/connectors/skills; add one observability value. Generic reducer/actions already accept this type; no new route or global query state needed. |
| SettingsModal | Radix dialog, 880px max width, 580px height, 208px sidebar, padded scrolling keyed content. Add category and lazy feature mount; preserve Usage/Connectors/Skills. Long category label needs wrapping, not existing whitespace-nowrap overflow. |
| Existing panels | Rounded bordered cards, text hierarchy, secondary/accent buttons, focus rings and local state/effects. Usage reads MOCK_USAGE; connectors simulate access. Neither is real FinOps or integration-health evidence. |
| Theme / primitives | Existing light/dark CSS variables, Ericsson Hilda/Inter font stack, Tailwind surface/text/subtle/accent/success/warning/danger tokens, lucide icon facade, cn, Radix focus/escape/close behavior and fade transitions. No new design system. |
| API | apiFetch uses getApiBaseUrl; getJson shares ApiError/SafeError handling but currently has no AbortSignal argument and casts generic JSON without runtime validation. Add optional GET signal and unknown-to-safe DTO decoding for this feature. No browser role/auth provider or query framework exists. |
| State / queries | React component hooks and service helpers; AppState stores application state. Diagnostics stays feature-local and ephemeral, outside persistence. |
| Responsive / accessibility | Settings has fixed height/sidebar and no dedicated narrow layout; existing connector columns use sm hiding. Limit changes to Settings/feature: viewport height/width bounds, category label wrap, contained overflow and responsive detail stacking. Radix traps/restores focus; semantic nav buttons/focus rings and live activity announcements already exist. |
| Tests | Vitest/jsdom + Testing Library/user-event, existing API/service and chat/state regressions. npm test and npm run build exist; no lint script/config or browser automation setup found. |

### Navigation, components and responsive behavior

Keep left Settings category navigation: Usage, Connectors, Skills, Observability &
FinOps. Inside its content use a wrapping nav of native rounded buttons with
aria-current, named navigation landmark and section heading. Order: Overview,
Tracing, Reliability, SLOs, FinOps, Data & Retention, Integrations, Access, Diagnostics.
Native Tab/Shift-Tab/Enter/Space interaction avoids introducing an incomplete ARIA
tab widget. Sections stay inside Settings; no top-level route. Initial section
Overview; keep selection only while feature mounted. Unmount on leaving category/close.

ObservabilitySettings owns nested selection and bounded in-memory request state.
ObservabilityNav selects one lazily mounted panel. Nine small panels reuse shared
ReadState, ConfigTable, StatusBadge, RunTable, RunDetail and Timeline. Tracing and
Diagnostics share run selection/detail presentation; no second run registry.
Status/time/schema helpers and section-local query hooks stay in feature directory.
No component parses chat trace label strings into diagnostic authority.

Keep current modal dimensions on desktop with viewport padding and max-height;
wrap category label within sidebar. Feature uses one/two-column cards, min-w-0 and
breakable IDs, controlled horizontal scroll for wide table. At narrow widths make
Settings category navigation compact/wrapping within this modal; nested nav wraps,
optional table columns hide with details retaining all fields. Detail is inline with
Back to runs, avoiding a second modal/focus trap. Preserve row focus after Back;
announce detail heading after selection without disrupting periodic refresh.
Validate 1280x720, 1440x900 and 390x844, light/dark, keyboard and 200% zoom locally.
No global responsive redesign or CSS/token rewrite.

### Seven M7 endpoints and exact data-to-view mapping

All endpoints are GET /api/observability plus the suffix below. Operational endpoints
require operational_metadata. Config requires effective_configuration. Technical
fields additionally require technical_diagnostics. Backend applies environment scope.

| Endpoint | Contract | M8 use |
|---|---|---|
| /active | RunPage(items <=100, next_cursor?, as_of) | Active Diagnostics; Overview bounded activity preview. |
| /runs | RunPage; limit/cursor/status/stage/environment/since/until | Recent Diagnostics; bounded recent failures preview. No aggregate totals. |
| /runs/{run_id} | RunView, omitted absent fields | ID lookup; Diagnostics/Tracing detail. |
| /runs/{run_id}/timeline | TimelinePage(items <=100, next_cursor?, truncated_count) | Significant event timeline, paged, sequence ordered. |
| /sessions/{session_id}/runs | RunPage | Typed client coverage and optional known-session filter; no session content or identity discovery. |
| /health | HealthView | Overview/Integrations safe persistence/export health; process counters explicitly scoped. |
| /config | EffectiveConfiguration[] | Admin-permitted read-only Reliability/Data/Integration/Overview config. |

RunView exposes run_id, environment, canonical status, classification, outcome_unknown,
source=durable, classification_as_of, started_at/terminal_at, current_stage/agent/tool,
dependencies(dependency/operation/agent/tool/started_at), elapsed_ms, last_progress_at,
heartbeat_at, progress_age_ms, work_deadline_at/total_deadline_at, error_code,
cleanup_status, delivery_status, timeline_truncated_count, optional technical.
TechnicalView exposes trace/root/current span IDs, turn correlation, producer instance,
state version, service version/git SHA/release/revision/region/config version and
telemetry_schema_version=1. Operator and Admin do not implicitly receive this block.
No model name, tokens, cost, session ID, full span parentage, governance summary,
full-stage duration or checkpoint timestamp exists in RunView. Heartbeat is not
relabelled as a durable checkpoint. DiagnosticEvent fields are event_seq/state_version,
event_type/timestamp/elapsed_ms/stage/status/agent/tool/dependency/error_code; no bodies.
Events do not contain span parentage or model details. Timeline is capped at 128 in
M7, while individual API pages cap at 100. API does not expose total run counts.

Health: persistence disabled/healthy/degraded/unavailable, exporter_state
 disabled/healthy/degraded/unknown, collector_configured, as_of, last_success,
stale_records and bounded writer counters. writer_scope=process_local;
durable_scope=authorized_environments. Never promote process admitted/pending counters
to global active count or collector_configured to exporter delivery/Collector health.

Config: setting/value/owner/read_only=true/config_version. Actual effective projection
includes telemetry/schema/environment/service/exporter booleans and mode, all registered
non-OTel *_timeout_seconds, heartbeat/stall/retry/watchdog, worker/queue limits and
projection_* values. Although DTO permits trace_sample_rate/finops_enabled, actual
/config omits them. No telemetry-backend retention, timeline-cap setting, dense
retrieval-specific timeout, identity/role/capability feed or per-provider health exists.
No backend change is required: unavailable values remain unavailable. If implementation
uncovers a genuine blocking mismatch, document an exact minimal safe DTO adjustment
before editing; no new endpoint, persistence, role admin or business functionality.

### Nine section designs

| Section | Supported design and truthful unavailable behavior |
|---|---|
| Overview | Small persistence/exporter cards from health; configured environment/observability state when config authorized. Active/stalled examples and recent failures from bounded pages labelled “Shown on this page” with more-results indicator. No fleet totals, rates, model activity, dependency SLA or fake zeros. SLO and cost summaries explicitly unavailable until M9/M10. Partial authorization/failure affects only its card. |
| Tracing | Safe run-ID selector and shared detail/timeline. Show observed stage/agent/tool/dependency and event offsets, backend elapsed/status/errors; optional technical trace ID as plain correlation, no arbitrary Cloud Trace URL/fetch. Ordered semantic timeline is available; full Turn→Agent→Model→Tool→Dependency span hierarchy is a future layout contract, not reconstructed from missing parentage. No model metadata invented. |
| Reliability | Config table grouping turn/cleanup, model/agent/tool/planning/orchestration, sessions/gateway/DB/knowledge/storage/secrets/persistence/SSE/queue, watchdog and capacities. Banner: “Provisional defaults — configurable — not production-approved SLOs.” View-only environment policy. Timeout/stall/cancel aggregate counters unavailable; no counting a partial run page into metrics. |
| SLOs | Permanent layout for availability, terminal completion, latency, TTFT, trace completeness, cost-ledger completeness and SSE delivery. “SLO evaluation becomes available in M9.” No gauges/targets/error-budget computations or charts implying zero. |
| FinOps | Named areas runtime usage M10; billing reconciliation/allocation M11–M12; unit economics M13; budgets/forecasts/anomalies M14. “FinOps accounting is not enabled yet. Accounting starts in M10.” EUR reporting design, estimated/billed remain separate future fields. No MOCK_USAGE import, prices, token totals or browser cost calculation. |
| Data & Retention | Effective projection terminal retention 24h, ordinary successful history 30d, exceptional/governed 90d only when returned by config. Checkpoint 15s, schema 1 and config version likewise server-provided. Describe fixed v1 timeline cap 128 as contract, not a fetched editable config. Backend trace/log/metric/accounting retention targets are future policy, not effective deployment; sampling absent = unavailable. M15 controls remain future/read-only. |
| Integrations | Collector endpoint configured vs unknown delivery; Cloud Trace/Monitoring/Logging backend health unavailable; persistence repository health does not prove Cloud SQL infrastructure health. Cards for Cloud SQL, Teams gateway: Power Automate, GCS, Secret Manager, knowledge, model provider. Selected-run dependency observations may show observed operation/time/status with run scope, never fleet health. No direct Microsoft Graph latency claim. No secret URLs, bucket names, DSNs or secrets. |
| Access | Read-only successful/denied/unavailable API access results per operational/config/technical response. Actual principal verification, role and full environment capability scope “Not exposed by current API.” Successful scoped response is observed access, not a fabricated identity profile. No role selector, auth administration or client principal manufacturing. |
| Diagnostics | Active/Recent subviews, exact UUID run search, backend status/stage/environment/aware UTC window filters, paged table and inline detail/timeline. STALLED is actual backend status; STALE is separate diagnostic overlay. Backend search 404 remains uniform “Run is unavailable.” No raw sessions/users/business content. |

Reliability effective defaults must come from /config, never hard-coded as live:
turn180s; cleanup5s; model120s; agent150s; tool30s; planning30s;
orchestration150s; session load/lock10s; gateway10s; DB acquisition5s/query15s;
knowledge whole15s; storage30s; secrets10s; persistence10s; SSE10s; queue wait1s;
stall30s; heartbeat5s; watchdog1s; retry minimum1s. Dense retrieval10s exists as
underlying setting but lacks separate M7 effective entry: explicitly unavailable
in this view. Legacy database_timeout_seconds=30 and graph_timeout_seconds=10
are displayed by exact safe setting with meaning, not silently substituted for
query/acquisition/Power Automate metrics. Worker8/pending16/business queue256/4MiB,
projection capacity2048/stale60s/grace5s are provisional if returned.

### Diagnostics details, status semantics and time

Primary columns: Run (short display, full accessible text in detail), Status with
classification overlay, Stage, Agent, Tool, Dependency, Started, Last progress,
Elapsed, Environment. Bound page to 25 (backend max100); row button opens full
safe ID detail. Optional columns hide at narrow widths; full values remain in detail.
No classification filter supported: don't invent a stale filter parameter; display
backend classifications. Status filter RUNNING/STALLED etc uses canonical enums.
Recent failure preview is explicitly a bounded FAILED query, not all error totals.

Detail header: ID/status/classification/outcome unknown/start/terminal times and
technical trace correlation when present. Current state: stage/agent/tool/dependencies,
elapsed/progress/heartbeat/deadlines and cleanup/delivery. Timeline uses significant
M7 events, sequence order, page controls and truncation notice. Runtime technical
metadata only if returned. No replay/cancel/retry command controls in M8 diagnostics.

Central text+icon status mapping: PENDING pending; RUNNING active; STALLED stalled;
COMPLETED completed; FAILED failed; TIMEOUT timed out; CANCELLED cancelled. STALE
classification overlays canonical badge: “RUNNING (stale diagnostic record)” and
“Outcome unknown”; never converts to FAILED/TIMEOUT. DEGRADED applies only to
reported health. Unknown enums use fixed “Unknown status/stage/event” fallback,
never render arbitrary unrecognized server strings. Stable known error codes map
to fixed friendly explanations; no stack/raw exception/repr display.

Reuse elapsed formatter for finite durations; absolute times include timezone and
machine-readable time elements. Backend elapsed_ms/progress_age_ms remain authority;
deadline countdown is display-only (“Deadline reached; awaiting backend state”
instead of creating timeout). Freeze stale/terminal elapsed. Distinguish client
“Last API refresh”, RunPage.as_of, classification_as_of and last_progress_at age;
no unsupported “last checkpoint” timestamp. Rendering time cannot classify stale,
authorize access/commands or infer canonical errors.

### API, state, compatibility and refresh design

Add seven typed service functions using existing getJson/apiFetch abstraction, URL
encoding IDs and URLSearchParams for allowlisted query parameters. Extend getJson
with optional AbortSignal without changing existing call semantics. No role/dev-user
headers, browser secrets, token storage or speculative auth mechanism. Retain current
same-origin/base URL behavior; future server identity must use approved server flow.

Decode unknown JSON into bounded explicit DTOs at feature boundary; safe numeric,
UTC timestamp, ID and known-code validation. Drop unknown fields; reject malformed
required fields; no broad any, raw JSON dumps or automatic object stringify.
Optional technical absent is permitted; hidden fields never synthesized. Mirror
RunView/RunPage/TimelinePage/HealthView/EffectiveConfiguration and DiagnosticEvent.
Explicit v1 decoder: check technical telemetry_schema_version and config schema entry
when present; unsupported supplied version shows incompatibility and hides payload.
Operator runs/timeline/health have no envelope API/schema version: validate their
v1 structural shape without asserting negotiated version; don't fetch Admin-only
config to gate Operator diagnostics. No new version endpoint merely for UI.

Section-local React hooks with AbortController, generation guard and cleanup;
manual refresh everywhere applicable. Optional 15s completion-scheduled polling only
while Diagnostics is active, page is visible and Active page1 or selected backend
nonterminal non-stale run is shown. At most list+detail+timeline requests per cycle,
sequential/no overlap; no global health/config polling. Pause for inactive/hidden,
stale/terminal detail, subsequent history pages or denial; cancel on close/unmount/
filter/run change. Visibility resume/manual refresh fetches once. Backend allowance
60 requests/min/principal: 3 per15s=12/min nominal; 429 stops auto-refresh with safe
retry action. Deadline10s aborts prevent infinite spinner; no retry storms. Keep
cursor filters stable; reset cursors on filter/refresh; Next/Back page replaces rows,
no accumulation of whole history. Preserve current filters/selected run on refresh.
No websocket, SSE protocol change or normal chat polling.

Lazy import feature on selecting Observability and mount only active panel. No calls
when ordinary Settings opens; static future SLO/FinOps sections make no queries.
Overview fetches only displayed supported cards; config and health independent.
Bound ephemeral caches to current page/detail plus small cursor history; clear data
on denial/scope change/close. No new state/query dependency or virtualization needed
for <=25 rows; optimize only from local measurement. No persistent diagnostic cache.

### Authorization and all read states

No frontend role context exists and M7 verified_principal_provider returns None.
Production defaults to 401 authentication_error; 403 authorization_error covers
missing capability/environment. M7 /config only Admin ceiling; Operator/SRE cannot
read config; Admin cannot automatically read technical metadata. FinOps/User/Auditor
have no implicit operational access. Keep all nine sections discoverable with safe
unavailable content; frontend visibility never authorizes a request.

401: “Diagnostics unavailable — verified server-side identity is unavailable.”
With current integration absent, help text explains production verification is not
configured. M7 error does not distinguish absent configuration from expired/missing
identity: do not assert a verified causal diagnosis from HTTP status alone.
403: “Diagnostic access is not permitted for this view or environment.” No guessed
role, automatic role escalation or repeated polling. Successful endpoint proves
only that request's access; absent technical data says “Not available with this
response,” without guessing denied vs missing. Access view labels role/principal
information unavailable rather than fabricating capabilities.

ReadState covers finite loading with polite announcement; successful empty messages
“No active runs”, “No recent runs”, “No diagnostic events available”; unavailable API
404/503 or persistence disabled; permission denied; degraded health; network/server
failure; 429 throttling; safe schema incompatibility. Recheck prevents rendering old
authorized rows after denial. Failed refresh may retain earlier safe data only for
non-auth transport failures with explicit stale-fetch banner/time, never relabel its
backend classification. Abort from navigation is silent. Safe retry is user-triggered.
Keep sections and individual cards isolated; local render error boundary fallback
prevents malformed data/component failure taking down Settings. Static future panels
state unavailable directly. Existing Settings panels remain navigable during failures.

### Security/privacy and invariants

Render only decoded allowlisted safe fields as React text. No content viewer, raw
object dump, arbitrary URLs, stack traces, SQL/provider messages, business/session
metadata, model prompts/responses, hidden reasoning, Teams/knowledge/attachment bodies,
commands, passwords, OAuth/bearer/auth headers. No production console logging of
payloads/errors, localStorage/sessionStorage, analytics capture, browser telemetry
payloads, export/download or copying whole responses. Existing client DEV debug logs
request path/status only; new observability errors use static path labels without IDs
if logging changes become necessary. IDs only in authorized ephemeral data and safe
request paths, never metric labels. No use of diagnostics as evidence/intent/authority.

Source contracts and M7 privacy already constrain data; frontend sentinel tests also
inject unexpected fields/unknown-code sensitive strings and verify DOM/storage/console
never contain them. Known codes map to local static text. Do not weaken RBAC/default
deny or add permissive backend overrides; test dependency overrides only in test code.

### Exact proposed implementation file manifest

Only implementation after separate user instruction. Modified existing paths (4):
- .agent/OBSERVABILITY_EXECUTION.md — persistent plan and validation evidence.
- src/types.ts — add observability SettingsSection.
- src/components/shell/settings/SettingsModal.tsx — category/lazy feature integration,
  label wrapping and scoped viewport containment/accessibility.
- src/api/client.ts — optional GET AbortSignal with unchanged shared safe errors.

New production paths (22):
- src/api/observability.ts
- src/api/observabilityTypes.ts
- src/components/shell/settings/observability/ObservabilitySettings.tsx
- src/components/shell/settings/observability/ObservabilityNav.tsx
- src/components/shell/settings/observability/OverviewPanel.tsx
- src/components/shell/settings/observability/TracingPanel.tsx
- src/components/shell/settings/observability/ReliabilityPanel.tsx
- src/components/shell/settings/observability/SlosPanel.tsx
- src/components/shell/settings/observability/FinOpsPanel.tsx
- src/components/shell/settings/observability/DataRetentionPanel.tsx
- src/components/shell/settings/observability/IntegrationsPanel.tsx
- src/components/shell/settings/observability/AccessPanel.tsx
- src/components/shell/settings/observability/DiagnosticsPanel.tsx
- src/components/shell/settings/observability/RunTable.tsx
- src/components/shell/settings/observability/RunDetail.tsx
- src/components/shell/settings/observability/Timeline.tsx
- src/components/shell/settings/observability/ReadState.tsx
- src/components/shell/settings/observability/ConfigTable.tsx
- src/components/shell/settings/observability/StatusBadge.tsx
- src/components/shell/settings/observability/useObservabilityQuery.ts
- src/components/shell/settings/observability/format.ts
- src/components/shell/settings/observability/contract.ts

New test paths (8):
- src/api/observability.test.ts
- src/components/shell/settings/SettingsModal.test.tsx
- src/components/shell/settings/observability/ObservabilitySettings.test.tsx
- src/components/shell/settings/observability/DiagnosticsPanel.test.tsx
- src/components/shell/settings/observability/Panels.test.tsx
- src/components/shell/settings/observability/useObservabilityQuery.test.tsx
- src/components/shell/settings/observability/contract.test.ts
- src/components/shell/settings/observability/performance.test.tsx

Existing src/api/client.test.ts may also change (explicit conditional path) if adding
signal forwarding coverage cannot be covered by observability.test.ts. No backend,
AppState, CSS, package/dependency/config, chat or IaC changes planned. Any actual
manifest adjustment must be recorded before adding scope. This pass actual changed
file: .agent/OBSERVABILITY_EXECUTION.md only.

### Implementation sequence and validation plan

1. Typed M7 DTOs/finite decoder, cancellable client and seven GET helpers; contract tests.
2. Existing Settings integration/nested nav/shared read states and static future panels.
3. Config/health-backed panels, no fabricated totals or unavailable capabilities.
4. Diagnostics Active/Recent/run search/detail/paged timeline, shared Tracing view.
5. Controlled polling/freshness, privacy/compatibility/error/access tests.
6. Full frontend regressions/typecheck/build; local operator stalled scenario,
   accessibility/responsive check and performance measurements; evaluate every exit.
Stop and repair current M8 on any blocking gate failure. Never start M9.

Planned local commands (not run during planning):
- npm test -- src/api/observability.test.ts src/components/shell/settings/
- npm test — every existing frontend test plus M8 tests; mocked fetch/test doubles only.
- ./node_modules/.bin/tsc -b
- npm run build — typecheck and production Vite build; no deployment.
- git diff --check; git status --short; git diff --stat; inspect actual diffs/untracked.
No repository lint command exists; report not configured, do not invent a pass.
If any backend DTO mismatch adjustment becomes necessary: inspect existing guard
/private/tmp/slopanoc-m7-test-guard/run_tests.py and its sitecustomize before running
.venv/bin/python <guard>/run_tests.py backend/tests/test_observability_api.py
backend/tests/test_observability_contract.py backend/tests/test_observability_settings.py.
Only isolated DB/test-double dependency overrides, blocked external sockets, stripped
credentials; never start normal API lifespan to test UI against cloud configuration.

Tests must cover all nine section navigation/active state and old panels; 401/403/
missing identity/allowed Operator/SRE/Admin field scopes; no role header; active/recent
lists, exact run search, detail/events/status STALLED/FAILED/TIMEOUT/CANCELLED/COMPLETED,
RUNNING+STALE preserving outcome unknown, empty/cursors/filter reset/manual refresh;
health disabled/degraded/unknown; config read-only/provisional/effective retention;
SLO/FinOps truthfulness; Power Automate vs Graph; sentinel privacy including console/
storage; schema1/unknown enum/malformed response/incompatible version; abort/slow
response/old-response race/error boundary and section isolation. Fake timers prove
15s polling, no overlaps, hidden/inactive/unmount cancellation, terminal/stale pause,
429/401/403 stop and bounded requests/caches. No live identity bypass in test app.

Acceptance stall scenario: isolated M7 test service/provider fixture with verified
Operator dependency override and a blocked Power Automate dependency under M6 test
clock; expose only local safe REST responses to UI test. Operator finds STALLED run,
agent/tool/dependency/progress/deadline, opens significant timeline, observes eventual
backend TIMEOUT or completion. If browser uses DTO fixtures alone, report UI projection
validation separately from actual producer→M7 API validation; fixtures are not live
cross-instance evidence. Do not require deployed cloud for local acceptance.

Local performance plan: fixed 25-row page and <=128-event total fixture, React Profiler/
performance clock, five warm repetitions for Settings open/first feature panel/table/
detail. Record median/max, environment and fixture size; report jsdom render overhead
as such. In local browser separately measure first paint/interaction if available,
no production claim. Request-count budgets: 0 observability calls before category
selection; only active section's endpoints; <=3 per polling15s cycle; no overlap;
no virtualized/unbounded history. Any absent browser/visual validation is an explicit
unproven acceptance item, not silently PASS.

### M8 objective exit criteria (all implementation gates PENDING)

| Criterion | Required evidence |
|---|---|
| Existing Settings integration, nine sections, native visual design | Navigation/render tests + local light/dark viewport inspection; existing panels preserved. |
| Seven typed M7 clients, bounded safe v1 DTOs, cancellation | Client/decoder tests, URL/filter/abort/schema/unknown enum cases, typecheck. |
| Server authorization/default deny authoritative; production limitation honest | 401/403 and scope tests, no fake headers/roles; no unsupported verification claims. |
| Active/recent/detail/timeline operator diagnosis | UI/API isolated blocked dependency scenario with stage/agent/tool/dependency/timing/error. |
| Canonical status vs STALE preserved | RUNNING+STALE and STALLED→terminal tests, no local failure synthesis. |
| Provisional config/read-only retention accurate | Returned effective values, denied config and absent sampling/dense/timeline-setting tests. |
| Trace/technical view respects actual scope and data | Optional technical absent, no fabricated span parentage/model/cost/governance details. |
| Integration scope honest | Collector configured vs delivery, process writer scope, Power Automate boundary; unknown backend health. |
| SLO/FinOps layout truthful, no later implementation | No fake metrics/costs, no price/ledger/SLO engine; static future panels visible. |
| All loading/empty/unavailable/denied/degraded/failure states isolated | Finite timeout, retries, error boundary, malformed/unsupported contracts and old Settings navigation. |
| Privacy enforced in DOM/log/storage/analytics | Restricted sentinel tests and source review; no persisted payload or raw error rendering. |
| Bounded pagination/polling/lazy rendering/freshness | Request/cursor/timer/cancel tests; API refresh vs progress age distinct; performance report. |
| Accessibility/responsiveness preserved | Keyboard/focus/live-region/table tests plus viewport/zoom validation. |
| All applicable frontend regressions/imports/typecheck/build pass | npm test, tsc -b, npm run build; backend safe suites if contracts touched. |
| Diff hygiene and safety | Actual manifest/hash/status/diff audit; no unrelated edits, M9/M10 work or remote mutation. |

No M8 implementation or runtime gate is PASS from planning alone. Planning audit is
PASS; M8 implementation/validation are NOT STARTED. Ready for Next Milestone **NO**.

### UI impact evaluation and production prerequisites

1. M8 explicitly supplies Settings → Observability & FinOps information architecture
and all M1–M7-backed safe read views. 2. Seven M7 contracts suffice with documented
absent fields; typed browser adapters required, no backend extension planned.
3. All nine views belong to M8, later computations/admin controls do not.
4. RBAC applies at server; UI cannot establish identity/capabilities/technical scope.
5. Effective configuration is view-only; no mutation endpoint or IaC bypass.

Cloud deployment status = **NOT EXECUTED — USER APPROVAL REQUIRED**.
No cloud/infrastructure change is necessary for M8 planning or local implementation.
Production UI diagnostics remains inaccessible until verified server identity,
shared M7 migration and API deployment are approved/completed; production retention
execution is separately pending. Future frontend deployment also requires exact
operation/environment/resource/impact/rollback approval under AGENTS.md. Targets are
unselected; no commands proposed or run. Prior approval does not authorize later
operations. Postgres/M7 crash-loss/timeline limitations remain; UI cannot repair them.

### Planning validation, risks and disposition

Planning performed source/spec/gate/API/permission/field-gap/privacy/performance/test
coverage review. No new runtime tests/builds/benchmark or cloud validation claimed.
Key risks: default-denied production identity; config access limited to Admin;
no identity profile/model feed/full tree/counts/checkpoint timestamp/sampling projection;
bounded async projection loss/stale records; existing fixed Settings width and long
label. Each has safe unavailable UX/scoped layout plan, no local planning blocker.
No implementation failures/repairs in this pass; historical regressions are untouched.

**Ready To Implement M8: YES** — local plan complete; await separate user instruction.
**Ready for Next Milestone: NO. M9 BLOCKED. STOP after planning.**

Planning hygiene evidence: 842-file SHA-256 baseline at
/private/tmp/slopanoc-m8-planning-baseline.json; audit PASS, only this execution
record changed, zero files added/deleted. Existing Git status, tracked diff/stat
and untracked inventories reviewed. git diff --check PASS. No source/test/UI
files modified, no generated artifacts or credentials added.

## M8 implementation authorization — 2026-10-08

User approved local M8 implementation only. M0–M7 local gates and approved plan
rechecked; no discrepancy. Source manifest and invariants above apply. No remote
operation authorized. Implementation and all M8 exit gates PENDING; M9 blocked.
Baseline recorded at /private/tmp/slopanoc-m8-implementation-baseline.json.
Small preimplementation refinement: diagnostic GET errors will suppress existing
DEV path debug logging to keep run/session IDs out of browser output; other API
clients retain behavior. Decoder vocabularies are generated once from inspected
local backend enums into source literals, not imported from backend at runtime.
No API/persistence changes required.

M8 implementation inventory refinement before validation: add
src/components/shell/settings/observability/fixtures.test-support.ts for shared safe
synthetic DTO/test builders (test-only imports). Detail polls summary only; timeline
uses manual bounded page refresh, so no independent polling race for timeline.
Overview uses health + active/recent previews, environment comes from each run.
No backend adjustment. Local browser automation availability is being inspected;
no installs or external calls attempted.

M8 validation refinements: list mode/filters/cursors now survive detail drill-down;
inactive list cancels requests and clears fetched rows while preserving filter state.
Back restores row focus once, without stealing focus on subsequent polls. Added
environment/local-time filters with explicit apply and bounded 90-day UTC conversion.
Settings description and viewport containment improve this modal only. Tests initially
needed explicit Vitest imports, row-scoped assertions (filter options include statuses)
and independent mock Response objects; performance fixture needed the actual empty
sessions response. Those test harness defects were repaired; focused validation passes.
Browser harness first matched /src/api modules as API requests, then failed to reset
per-viewport config-denial fixtures; both fixed without product/auth changes. CSS zoom
was an invalid browser-zoom simulation; replaced with half-size CSS viewport and 2x
raster validation. Initial sandbox Chromium launch failed; local-only cached Chromium
ran with sandbox escalation and external DNS/request blocking. No cloud mutation.


## M8 final implementation and local validation — 2026-10-08

**IMPLEMENTED AND LOCALLY VALIDATED.** This section supersedes M8 planning and
pending-gate text. No M9 implementation, M10+ accounting, production identity
integration, persistence/schema changes, deployment or external-service mutation.
All existing backend/governance/SSE/approval/session behavior remains unchanged.

### Final Settings and component implementation

Existing SettingsModal owns the added category and lazy-loads ObservabilitySettings.
SettingsSection gains observability; existing generic AppState/reducer is reused
without editing AppState. Usage/Connectors/Skills still render. Native CSS theme,
light/dark typography, lucide icon facade, Radix dialog, cards and focus styles reused.
Modal retains 880x580 desktop defaults with viewport bounds; category label wraps,
narrow screens stack category navigation and content. No new route/design system.
An accessible dialog description and named navigation landmarks are provided.

Nine sections: Overview, Tracing, Reliability, SLOs, FinOps, Data & Retention,
Integrations, Access, Diagnostics. One active panel mounts under an isolated render
boundary. Shared ReadState, ConfigTable, StatusBadge, RunTable, RunDetail, Timeline,
format/contract helpers and request hook implement modular read-only views.
No observability request occurs merely by opening ordinary Settings. Static SLOs
and FinOps have no queries or mock usage/pricing imports.

Overview uses /health and two five-run active/recent previews. Page counts explicitly
say matching runs on this page, with as_of and more-results availability. Health
writer counts are labelled process-local; no fake fleet totals, model/agent aggregates,
SLO compliance or monetary values. Run environments are shown directly from responses.
Tracing reuses run lookup/detail and significant operational timeline; no fabricated
full span tree, model feed, raw attributes or arbitrary Cloud Trace URLs.

Reliability renders returned config only, read-only, with exact banner
“Provisional defaults — configurable — not production-approved SLOs.” Timeouts,
stall/heartbeat/watchdog/retry, queue/worker/projection limits are distinct from SLOs.
Unavailable dense-specific timeout/counters remain explicit. Data & Retention renders
returned 24h/30d/90d defaults (or effective overrides), checkpoint/stale/grace/schema
entries. Timeline128 is explicitly a v1 contract limit; not an effective editable
setting. Telemetry retention/sampling and M15 controls remain unreported/future.

SLO layout lists availability/completion, four latency classes, TTFT, trace/cost-ledger
completeness and SSE delivery, with M9 availability message and no fake percentages.
FinOps layout lists runtime usage, reconciliation/allocation/unit economics/budgets/
forecasts/anomalies by owning milestone, with M10 accounting message. EUR is future
reporting policy only; no monetary arithmetic, ledger, prices or accounting fetch.

Integrations distinguishes application exporter delivery from configured Collector
endpoint and unreported Collector/cloud infrastructure health. Persistence repository
health never proves Cloud SQL health. Teams gateway is labelled Power Automate;
direct Microsoft Graph telemetry unavailable. Cloud Trace/Monitoring/Logging, GCS,
Secret Manager, knowledge and model provider health remain not reported.
Access checks operational health/config independently, displays actual request access,
and explicitly says profile/verification/provider/role/full scope are not exposed.
No principal manufacture, role editing or capability inference from successful request.

### Typed client, compatibility and privacy

Seven GET helpers in src/api/observability.ts reuse getJson/getApiBaseUrl, preserve
same-origin behavior, encode IDs/query strings and forward AbortSignal. getJson's new
optional signal keeps old call semantics. Existing DEV diagnostic path logging is
suppressed for /api/observability routes; other API error behavior is unchanged.
Client normalizes API errors to static messages; UI maps status without rendering
raw SafeError/provider/SQL/stack bodies. No authorization/dev identity headers added.

Explicit TypeScript run/page/timeline/dependency/technical/health/config/query types.
Decoder projects allowlisted fields, finite nonnegative numeric bounds, strict ID/
timestamp/code formats, 100-item pages, 128 dependencies, bounded opaque cursor,
ordered event sequences, read-only unique known config entries. Local backend enum
vocabulary is mirrored in literals; future unrecognized enums become fixed Unknown,
not raw strings. Extra fields never reach the projected DTO. Supplied technical/config
or optional envelope schema versions must be1; unsupported versions produce safe
incompatibility. Versionless Operator/health/timeline envelopes are structurally
validated; no schema negotiation or Admin config dependency is invented.

No production console payload logging, storage/IndexedDB cache, telemetry/analytics
capture, raw JSON rendering, sensitive bodies, URLs, SQL, commands or credentials.
Sentinel tests cover prohibited extras and unknown enum fields through client/DOM/
console/storage boundaries. Source scan finds no any, raw fetch, console or browser
persistence usage in new production feature/client modules.

### Scoped authorization, diagnostic state and refresh

Each request owns its own finite ReadState: loading, empty, unavailable, denied,
401, 404, 429, network/server failure, malformed or incompatible response. Config403
never disables permitted Diagnostics, and health failure never disables config-backed
views. Timeline failure leaves the permitted summary available. Routine API failures
are handled locally; unexpected component failure remains isolated to active panel.

401 copy: “Diagnostics are unavailable for the current authenticated context.”
No unsupported claim about missing identity infrastructure vs expired authentication.
403 is view/environment-specific denial. Query state clears all data on failure,
including previous protected technical metadata after 401/403, and stops auto-refresh.
Technical block renders only when backend supplies it; Operator/Admin cannot acquire
it through client role toggles. Backend remains sole identity/capability/scope authority.

Diagnostics: Active/Recent, exact canonical UUID search, status/stage/environment and
local-time→aware-UTC window filters, explicit environment/time apply with <=90d
validation, backend page25/cursor navigation, bounded20-page cursor history. Page
changes replace rows; manual list refresh returns to first page with filters retained.
List mode/filters/cursors survive drill-down; hidden list stops requests and discards
fetched rows. Back restores row focus once after reload, not on every future poll.

Detail shows canonical status, diagnostic classification and stale outcome unknown;
stage/agent/tool/dependencies, start/progress/heartbeat/deadlines/terminal/elapsed,
cleanup/delivery/canonical errors, available technical trace/release/revision/schema.
Timeline pages25 significant events in server sequence with truncation warning.
No replay/retry/cancel/command operation added. RUNNING + STALE remains RUNNING;
STALLED is backend status, not terminal failure. Unknown categories remain Unknown.
Dates include timezone; progress age, fetch time, page snapshot and classification
as_of are distinct. Deadline arithmetic is display-only and cannot create TIMEOUT.
No unsupported durable checkpoint timestamp or stage-duration truth is inferred.

Request hook:10s finite loading deadline, AbortController + generation guard,
completion-scheduled15s polling only in visible active Diagnostics page1 or known
nonterminal nonstale selected detail. No overlap; inactive/hidden/unmount/key changes
cancel; terminal/stale/unknown detail and 401/403/429 stop polling. Timeline is manually
refreshed, no independent polling. Visibility resume reloads once when permitted.
No retry storm, unbounded cache, websocket/SSE redesign or global query framework.
Tests prove request cadence, revocation, no overlap, hidden/inactive cleanup, old-response
suppression and correct terminal/stale policy. Maximum normal auto cadence is one
request/15s for current active list or selected run summary; detail initial load adds
one bounded timeline request. UI interval is not backend heartbeat.

### Local operator acceptance and backend evidence

/private/tmp/slopanoc_m8_acceptance.py uses disposable SQLite explicitly isolated_test,
actual TurnTrace, agent/tool scopes, blocked Power Automate dependency, fake M6 clock,
Watchdog and Coordinator. Advancing clock40s creates STALLED with incident_manager /
teams_get_messages / power_automate_gateway. Durable M7 ASGI routes return those facts
and timeline; Operator config403 is enforced. Closing work and TimeoutError produces
durable TIMEOUT; Admin reads effective config. No normal app lifespan/warmup/cloud
client or shared DB is run. Socket guard blocks all non-loopback connections.
Result PASS. Safe synthetic responses saved only under /private/tmp.

Cached Chromium loads actual existing /app + Settings over local Vite. CDP interceptor
serves those real isolated M7 JSON responses, blocks external traffic and substitutes
empty saved-session fixture. Operator can locate STALLED run, inspect blocked agent/
tool/dependency and timeline, refresh to TIMEOUT, then observe summary/timeline clearing
on401. Config403 remains scoped; Admin fixture renders actual24h/30d/90d config.
Keyboard Tab remains inside modal (20 steps at each of3 viewports), Escape closes.
Result PASS, zero browser exceptions. This is captured producer→API→browser projection
validation, not a live deployed authenticated/Cloud SQL test. Test identity overrides
exist only in isolated harness, not browser headers or production backend code.

### Tests, build, accessibility and performance

- Focused M8 tests:55 passed,8 files,2.77s before final focus-once refinement;
  final full suite includes these same55 tests passing on final source.
- Final `npm test`: **912 passed,51 files,8.37s**, including all857 existing frontend
  tests. Existing unrelated React act/Radix warnings remain; no unhandled error.
- `./node_modules/.bin/tsc -b`: PASS; final `npm run build`: PASS,
  2049 modules,1.72s Vite build. Lazy ObservabilitySettings chunk37.69kB /11.28kB gzip.
- Guarded isolated backend API/persistence/contract tests:
  `.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py
  backend/tests/test_observability_api.py backend/tests/test_observability_persistence.py
  backend/tests/test_observability_contract.py`: **95 passed,5.99s**.
  Runner strips inherited cloud/business credentials, fixes isolated DB URLs and
  blocks non-loopback connections. No backend source or migration change.
- No lint script/config exists: NOT CONFIGURED, no lint-pass claim.
- `git diff --check`: PASS. Actual baseline hash/status/tracked diff/untracked review
  confirms scoped edits and preservation of all preexisting local work.

Accessibility: native labelled section buttons + aria-current; Radix description/
focus trap/Escape; screenreader loading/status/error semantics; semantic captioned
column/row-header table and labelled scroll region; full run ID accessible button
label; detail heading focus and return focus; color accompanied by status text.
Keyboard unit tests and local Chromium navigation/focus assertions pass.

Browser geometry and inspected screenshots:1280x720 light and1440x900 dark modal
880x580 fully contained;390x844 narrow modal358x580 fully contained, nav wraps and
content scrolls. No document horizontal overflow. Zoom-equivalent720x450 CSS viewport
at2x raster keeps modal x16..704,y16..434. This validates200% equivalent layout,
not an OS/assistive-device certification or every legacy Settings panel redesign.
Screenshots inspected at /private/tmp/slopanoc-m8-{1280,1440,390}.png.

Performance test: five repetitions in jsdom,25-row table fixture, real Settings modal,
first Overview and RunDetail/timeline with mocked safe transport; final broad-suite
median/max ms: Settings22.45/147.60; Observability37.27/66.76; table14.80/32.90;
detail6.29/41.79. Broad-suite contention/cold startup influence these measurements;
not production browser capacity or a hard latency guarantee. Browser automation
also records three viewport interaction timings, including CDP calls and50ms selector
wait granularity, separately in /private/tmp/slopanoc-m8-browser-results.json.
0 diagnostic requests opening ordinary Settings proven. Timer tests validate bounded
cadence and cancellation; no production load/profiling claim. Local Vite and temporary
Chromium were stopped after validation; no dependencies installed.

### Blocking local M8 exit criteria

| User M8 local criterion | Result |
|---|---|
| Observability & FinOps integrated into existing Settings | PASS |
| All nine sections exist | PASS |
| Existing Settings sections remain intact | PASS |
| Current visual system preserved | PASS |
| Typed M7 API client used | PASS |
| Runtime DTOs fail safely | PASS |
| Backend authorization remains authoritative | PASS |
| Partial permissions work correctly | PASS |
| Previously authorized data clears after401/403 | PASS |
| Diagnostics supports active/recent/detail/timeline | PASS |
| Backend pagination/filtering used | PASS |
| STALE stays separate from canonical failure | PASS |
| Reliability displays effective values read-only | PASS |
| Provisional reliability labelled correctly | PASS |
| SLOs do not fabricate M9 results | PASS |
| FinOps does not fabricate M10+ results | PASS |
| Retention values display truthfully | PASS |
| Integrations represent Power Automate/Graph boundary correctly | PASS |
| Access does not invent role/principal data | PASS |
| Loading/error/empty/denied states work | PASS |
| Polling bounded | PASS |
| Sensitive content not rendered/logged/persisted | PASS |
| Accessibility remains functional | PASS |
| Frontend tests/typecheck/build pass | PASS |
| M9 remains unimplemented | PASS |
| M10+ remains unimplemented | PASS |
| No cloud mutation occurred | PASS |

### Production and known limitations

Production verified identity: **NOT LIVE-VALIDATED**; integration remains absent.
Shared Cloud SQL migration: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Production M7 APIs: **NOT DEPLOYED** in this work.
Production frontend deployment: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Production retention scheduling: **NOT EXECUTED — USER APPROVAL REQUIRED**.
Cloud deployment status = **NOT EXECUTED — USER APPROVAL REQUIRED**.
No remote project/account/tenant selected and no remote mutation command proposed/run.
Each future exact deployment/identity/migration/scheduler action needs separate approval.

M7 default-denied production identity still means production Diagnostics is unavailable.
M8 does not overcome M7 asynchronous projection loss, stale/missing records or unproven
Postgres concurrency/cancellation. Timeline is bounded significant events, not full
OTel trace; model/cost/governance aggregates, fleet counts, identity profile and deeper
cloud health absent. Config visibility follows Admin capability; technical correlation
follows backend technical capability. No full API-envelope schema negotiation available.
Trace sampling/backend retention omitted from effective config stays unavailable.
Local browser fixtures and isolated SQLite do not establish production readiness.

UI-impact rule:1) all required nine Settings views now exist;2) existing seven safe M7
contracts consumed via typed client;3) later engines/admin controls not implemented;
4) backend RBAC mandatory and observed scoped UX;5) configuration view-only with no
IaC bypass. Normal chat safe progress/SSE/authority/approvals/persistence untouched.

**Ready for Next Milestone: YES (local M8 gates only). STOP. M9 NOT STARTED.**
Separate M9 instruction required; this report grants no cloud/deployment permission.

### Actual files and final hygiene

Existing files changed (4):

- src/api/client.ts
- src/components/shell/settings/SettingsModal.tsx
- src/types.ts
- .agent/OBSERVABILITY_EXECUTION.md

New files (31):

- src/api/observability.test.ts
- src/api/observability.ts
- src/api/observabilityTypes.ts
- src/components/shell/settings/SettingsModal.test.tsx
- src/components/shell/settings/observability/AccessPanel.tsx
- src/components/shell/settings/observability/ConfigTable.tsx
- src/components/shell/settings/observability/DataRetentionPanel.tsx
- src/components/shell/settings/observability/DiagnosticsPanel.test.tsx
- src/components/shell/settings/observability/DiagnosticsPanel.tsx
- src/components/shell/settings/observability/FinOpsPanel.tsx
- src/components/shell/settings/observability/IntegrationsPanel.tsx
- src/components/shell/settings/observability/ObservabilityNav.tsx
- src/components/shell/settings/observability/ObservabilitySettings.test.tsx
- src/components/shell/settings/observability/ObservabilitySettings.tsx
- src/components/shell/settings/observability/OverviewPanel.tsx
- src/components/shell/settings/observability/Panels.test.tsx
- src/components/shell/settings/observability/ReadState.tsx
- src/components/shell/settings/observability/ReliabilityPanel.tsx
- src/components/shell/settings/observability/RunDetail.tsx
- src/components/shell/settings/observability/RunTable.tsx
- src/components/shell/settings/observability/SlosPanel.tsx
- src/components/shell/settings/observability/StatusBadge.tsx
- src/components/shell/settings/observability/Timeline.tsx
- src/components/shell/settings/observability/TracingPanel.tsx
- src/components/shell/settings/observability/contract.test.ts
- src/components/shell/settings/observability/contract.ts
- src/components/shell/settings/observability/fixtures.test-support.ts
- src/components/shell/settings/observability/format.ts
- src/components/shell/settings/observability/performance.test.tsx
- src/components/shell/settings/observability/useObservabilityQuery.test.tsx
- src/components/shell/settings/observability/useObservabilityQuery.ts

842-file preimplementation SHA-256 audit PASS: exactly4 existing paths changed,
31 paths added,0 removed. No unrelated local changes destroyed/overwritten.
No backend, IaC, migration, package/dependency or chat files changed. Test harnesses,
logs, synthetic diagnostic fixture, browser profile/screenshots and audit evidence
remain under /private/tmp; none added to source control.

---

## M9 — SRE, SLOs, Metrics and Alerting: planning only — 2026-10-08

**M9 — PLANNED — NOT IMPLEMENTED.** This section supersedes historical M9-not-started
instructions only. Last completed milestone M8. Authorization: audit and planning,
with this execution record as the only repository change. M10 remains BLOCKED.
No runtime code, tests, migration, Terraform, dashboard or alert implementation in
this pass. Ready for Next Milestone: **NO**. Ready To Implement M9: **YES**, upon a
separate implementation instruction; this does not authorize production activation.

### Entry gate, objective and authoritative files

Entry gate **PASS**: M0 VALIDATED COMPLETE; M1–M8 IMPLEMENTED AND LOCALLY VALIDATED;
latest individual completion sections and matrix permit local progression. M8 local
validation PASS, all blocking local exit criteria PASS, Ready for Next Milestone YES.
Source modules and tests supporting those records exist. Historical validation was
reviewed, not rerun or independently recertified during planning. No known false
local predecessor gate discovered. Cloud-dependent completion is not claimed.

Production activation remains pending: verified production identity integration;
shared Cloud SQL migration 9f71c2a64e08; M7 API deployment; frontend deployment;
retention scheduling; Collector/GCP deployment and delivery verification. These
are separately recorded approval/activation gates, not local M9 planning blockers.

Objective: objectively measure health, SLO compliance, remaining error budget and
actionable burn using backend-owned, unsampled, environment-separated measurements.
Preserve existing conversation, governance, approval, command, persistence, SSE,
rewind and task ownership. Telemetry is never evidence, authority or intent.

Read AGENTS.md, .agent/PLANS.md and this record, plus all requested files under
docs/Telemetry/: README.md; 00_MASTER_BUILD_CONTRACT.md; 01_TARGET_ARCHITECTURE.md;
02_TELEMETRY_DATA_CONTRACT.md; 03_INSTRUMENTATION_AND_RUNTIME.md;
04_RELIABILITY_TIMEOUTS_ERRORS.md; 05_STORAGE_APIS_AND_UI.md;
06_SRE_SLOS_METRICS_ALERTS.md; 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md;
10_DASHBOARDS_RUNBOOKS_OPERATING_MODEL.md; 11_SLOPANOC_INTEGRATION_MAP.md;
12_IMPLEMENTATION_ROADMAP.md; 13_TEST_VALIDATION_ACCEPTANCE.md;
14_CODEX_EXECUTION_GUIDE.md; 15_DEFINITION_OF_DONE.md;
16_PRODUCTION_TELEMETRY_BACKEND.md; 17_INFRASTRUCTURE_OBSERVABILITY.md;
21_SLO_FORMULAS_AND_INITIAL_TARGETS.md; 22_RELEASE_CONFIG_CORRELATION_CI.md;
23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md.

Doc21 fixes population/formula semantics and calls numeric targets provisional.
All targets below retain that provisional designation pending production baseline.
M9 roadmap includes golden signals, AI metrics, SLOs, budgets, burn alerts and safe
synthetic checks; exit is objective health measurement plus validated alert dry-run.
Do not omit synthetics simply because no live synthetic tenant is approved.

### Actual source audit and components to reuse

| Actual source | Observed implementation / M9 decision |
|---|---|
| backend/api/chat_service.py: execute_turn_events / _drive | Trusted run allocation then begin_turn at accepted iteration, before session-lock wait. Independent task closes root after generator cleanup and lock release. Reuse this accepted→terminal monotonic duration, including lock wait and bounded cleanup; no new executor or inner-Runner timing. |
| backend/observability/turn_trace.py | Root, native IDs, canonical terminal guard, bounded timeline, phase handles and independent relay_closed observation exist. No accepted/success/terminal counters or root latency histogram exists. Add aggregate producers here; duplicate finish/backstop must not double count. |
| model_instrumentation.py / model_usage.py / model_metrics.py | One physical-attempt observation; first_provider_output TTFT or null; retries and unsampled usage observation seam. Existing model metrics below are reusable. No durable accounting source, as explicitly assigned to M10. |
| agent_instrumentation.py / tool_instrumentation.py / execution_metrics.py | Actual agent/tool executions, roles, terminal status and finite registries exist. Reuse; tool invocations include callback/governance outcomes, so do not mistake policy rejection for provider failure. |
| dependency_contract.py / dependency_metrics.py | Gateway, four DB owners, two storage roles, Secret Manager and knowledge stage metrics. requests excludes local knowledge stages, acquisition and transaction groups. Preserve denominators; pure DB pool queue wait is unavailable. |
| reliability_metrics.py / watchdog.py / deadlines.py / delivery.py | Timeout/stall/resume/cancellation/retry/queue/executor and cleanup aggregates; existing deadlines and independent transport ownership. No persistent-active gauge; work.detached is a counter, not current workers. Do not sum stalls into turn failures. |
| models.py / repository.py / persistence.py / projection.py | Three diagnostic tables, async coalescing bounded projection and terminal summary. Projection may be lost/rejected; bounded 128-event timeline is not complete trace evidence. Reuse diagnostic lookup/deadline facts, never scan all diagnostic events as a metrics warehouse. |
| persistence_metrics.py / service.py | ProjectionHealth counters and last_success are process-local health DTO data, not registered OTel instruments. Plan their aggregate metric bridge; cumulative failed count does not mean an ongoing outage. |
| authorization.py / schemas.py / api/observability_routes.py | Default-denied verified identity seam, capability ceilings, environment scope, bounded read routes; /health and /config already safe. Reuse those policies without dev-header bypass. |
| Team Manager operational_routing.py / agent/tool scopes | Server-owned GOVERNED_OPERATIONAL route and actual specialist metadata exist; Team Manager schemas.py only reexports IM schemas. No unified four-class TurnPlan classifier exists. Add observational class adapter; do not introduce new planning authority. |
| src/.../observability/SlosPanel.tsx | Static ten-card M9 placeholder, no query or arithmetic. Reuse existing query hook, strict decoder, ReadState, visual system and permission clearing. |
| infra/observability/collector/config{,.local}.yaml | Fail-closed metric name/scope/label/value filters; any new instrument will otherwise be dropped. Update application views, exporter projection and both Collector configurations together during implementation. |
| infra/observability/terraform/ | Existing Terraform is provider-free Collector service-template outputs, not deployed dashboards/alerts/service SLOs. Preserve its safe validation; add a separate provider-backed monitoring module, never convert existing outputs into an implicit deployment. |

Existing instruments (actual dot names take precedence over illustrative underscore
catalog names; no duplicate alias instruments):

- M1 slopanoc.telemetry.{accepted,attempts,exported,failed,dropped,filtered,queue_size,
  export_duration_ms,shutdown_failed}, observable gauges by environment/operation.
  These are subsystem totals/queue facts, not per-turn cloud-delivery evidence.
- M3 slopanoc.model.{requests,retries,failures,timeouts,usage_unavailable};
  gen_ai.client.operation.duration; slopanoc.model.ttft;
  slopanoc.model.{input_tokens,output_tokens,cached_tokens,candidate_tokens}.
- M4 slopanoc.agent.{executions,failures,duration};
  slopanoc.tool.{invocations,failures,timeouts,duration}.
- M5 slopanoc.dependency.{requests,failures,timeouts,rate_limits,retries,pages,duration};
  slopanoc.db.connection_acquire_duration; slopanoc.db.pool.{exhaustion,size,checked_out,
  overflow,checked_in,connections}; slopanoc.knowledge.{no_result,fallback,stage.duration};
  slopanoc.storage.{bytes_uploaded,bytes_downloaded}.
- M6 slopanoc.turn.{timeouts,stalls,stall_duration,progress_resumed,cancellations};
  slopanoc.stage.timeouts; slopanoc.cleanup.{timeouts,failures,duration};
  slopanoc.work.{late_completions,detached}; slopanoc.executor.{saturation,admission_rejected};
  slopanoc.queue.saturation; slopanoc.session.lock_wait;
  slopanoc.reliability.{retries,retry_denied,retry_exhausted,controller_failures,deadline_overshoot}.

### Canonical inventory, exact formulas and event boundaries

Central immutable definitions will live in backend/observability/slo_contract.py.
Each has stable ID, definition version, provisional target, unit, threshold,
population predicate, timestamp owner, expected source, windows and freshness policy.
No independent React/Terraform copies of objectives or formula constants.

Let E = eligible population, G = good events, B = E-G for fully classified data.
SLI = G/E. Unknown outcomes are separately counted; never quietly become good or
disappear into exclusions. Partial capture cannot certify a healthy SLO.

| Stable SLO ID | Provisional objective | Exact eligible/good rule and measurement |
|---|---|---|
| availability | .995 | E=accepted valid turns. G=terminal valid answer, clarification, source gap, approval request or safe policy rejection. Completed transport/HTTP200 alone is insufficient; infrastructure/provider/internal failures are bad even with friendly text. |
| terminal_completion | .999 | E=same accepted valid turns. G=exactly one explicit COMPLETED/FAILED/TIMEOUT/CANCELLED terminal closure at or before the recorded maximum total turn deadline. Any terminal, not successful completion only. Missing/late/duplicate closure is bad; late closure never retroactively becomes on-time. |
| latency_general | .95 within <=8s | E=class general terminal turns with valid canonical root duration. G=duration<=8s. p95 reported separately. |
| latency_teams | .95 within <=20s | Same, class teams_lookup; measures whole user turn, not teams.getMessages tool or gateway latency. |
| latency_troubleshooting | .95 within <=30s | Same, class governed_troubleshooting. |
| latency_complex | .95 within <=45s | Same, class complex_multi_agent. |
| model_ttft | .95 within <=5s | E=streaming user-turn provider generate-content attempts with valid measured TTFT boundary first_provider_output; G=TTFT<=5s. Includes retried attempts individually. Warmup/ingestion/background, embeddings, nonstreaming, server-only responses, unknown TTFT and failure before first output do not enter the distribution. Unknown/pre-output failures remain coverage/provider-reliability signals. |
| trace_completeness | .999 | E=terminal turns, independently of trace retention. G=one correlated root, exactly one correct terminal close and all applicable required phases present. Structural creation/closure evidence is mandatory; delivery is separately evidenced, never inferred from IDs. |
| sse_delivery | .999 | E=connected SSE turns eligible for message.completed. G=server emitted message.completed plus bounded authenticated browser receipt, while browser remains connected. Sync consumers excluded. Backend terminal status and relay success separately reported. |
| model_reliability | .995 | E=physical provider operations except explicit client cancellation; G=provider COMPLETED transport status. Reuse M3 request/status totals; validation failure is a distinct semantic signal, not a second provider call. Warmup/background panels remain distinguishable if user scope is requested. |
| dependency_reliability_<class> | .995 | E=real operations in each registered class except explicit client cancellation; G=COMPLETED. DB/storage/gateway/knowledge are separate, not one pooled success rate. Graph unavailable without direct source; Power Automate stays gateway. Knowledge overall retrieval needs its own operation observer, not a sum of local stages. |
| cost_ledger_completeness | 1.0 architectural | E=billable provider operations observed by provider-call instrumentation. G=operations with exactly one persisted accounting usage event. Operational threshold <.9999/24h; DEFINED — DATA SOURCE AVAILABLE IN M10. No evaluated value in M9. |

Latency percentile estimator: nearest-rank empirical p95 for exact offline fixtures;
production histograms report bounded/estimated percentile with method metadata.
The authoritative compliance decision is the exact fraction <= threshold, not an
interpolated percentile or average. Request-based 95% latency compliance implements
the p95 target; do not substitute a percentage of one-minute p95 windows.
Cloud request-based distribution-cut/ratio semantics reference:
https://docs.cloud.google.com/stackdriver/docs/solutions/slo-monitoring .
At-threshold is good (inclusive <=); generated cloud query tests must preserve this
even if a native range cut uses an exclusive upper bound. Prefer canonical good/total
event counts when bucket boundary inclusivity cannot be represented identically.

### Eligibility/exclusions and server request classes

Accepted means authentication/authorization + schema validation passed and canonical
execute_turn_events iteration entered. Count once at begin_turn before lock wait,
not when returning an unentered generator. Rejections before this boundary (malformed,
authorization denial, cancellation before entry) are excluded; expose request rejection
operational signals separately. Failures entering/persisting/loading an accepted
session remain eligible. Valid safety rejection, source gap, clarification and approval
request are good availability outcomes only when existing application owners confirm
that outcome. An actual exception disguised as source gap is never good.

User cancellation after acceptance stays in availability/completion denominator per
doc21. CANCELLED counts for timely terminal completion, does not count as a valid
availability application outcome. Voluntary SSE disconnect changes neither backend
SLI. A disconnect with later COMPLETED still counts as availability good.
Synthetic turns are not silently excluded from service reliability by the business
unit-economics exclusion in doc21: retain a bounded workload dimension and show
business and synthetic populations separately, with explicit combined-service policy.
Synthetics must not inflate a reported business-only SLO.

Proposed mutually exclusive classifier uses existing bounded server routing/operation
facts, never reevaluates prompt text or model prose:
1. complex_multi_agent when actual planned/executed primary plus supporting specialist
   relationship is confirmed (two distinct specialist owners; repeated one-agent calls
   alone do not establish complexity);
2. governed_troubleshooting when existing governed operational/progression route or
   explicit governed troubleshooting operation is confirmed;
3. teams_lookup when registered read lookup operation (teams_list_chats,
   teams_get_messages, get_resolved_chat_messages or supporting read) establishes lookup,
   without higher-priority route; Teams writes do not qualify merely by gateway usage;
4. general when existing route and final operation disposition positively confirm
   ordinary general execution;
5. unknown otherwise, including early routing failure. Never default unknown to general.
Freeze observational class at terminal; accepted eligibility is unchanged by routing.
Class ordering is an M9 adapter proposal to machine-test and document, not a new
router/TurnPlan decision. Display unknown/unclassified count and classification
coverage; a missing class source yields INSUFFICIENT_DATA, not healthy omission.

Canonical timing: TurnTrace monotonic acceptance→_finish after _drive cleanup/lock
release. UTC timestamps determine window membership only. This includes backend
session lock, final persistence and queueing, excludes browser render/network and
postterminal HTTP relay. Gateway and tool histograms remain separately labelled
operation timing; no relabelling as Teams turn latency. Terminal maximum deadline
uses total_deadline_at (work plus bounded cleanup reserve), not just provider timeout.

### Rolling windows, aggregation source and crash/coverage handling

Default rolling window = 28*86400 seconds, never month/30d/process lifetime.
Definitions expose window_seconds and explicit UTC window_start/window_end;
operational views 1h/6h/24h/7d; burn also 3d. Membership [start,end); future timestamps
rejected; exact cutoff included, end excluded. Evaluate on UTC minute boundaries
so minute aggregates match cuts exactly, without timezone/DST dependence.

Production source is Cloud Monitoring unsampled OTel aggregates. Backend evaluator
consumes bounded aggregate snapshots through a provider interface; local/test provider
feeds the same snapshot shape and canonical evaluator. No process-lifetime sample
array, local-memory 28d truth, browser arithmetic or full timeline SQL scans.

Accepted cohort alignment is mandatory: dividing completions in one interval by
acceptances in another gives wrong ratios at window edges and hides orphans. Plan
a narrow operational SLI receipt/rollup extension using existing Cloud SQL ownership:
one compact receipt keyed by trusted run_id with accepted_at, recorded deadline,
environment/workload, final safe class/outcome, phase masks and settlement flags;
then minute aggregates keyed by cohort minute/environment/class/SLI. No content,
model-token/cost records or complete trace trees. Idempotent settlement/restart
reconciliation and rollup high-watermarks; not a FinOps outbox or financial ledger.
Receipt writes are bounded/asynchronous and never authorize or replay execution.

Settlement waits through maximum total deadline plus configurable ingest grace.
Proposed grace 120s (provisional), recorded separately from completion deadline.
An accepted receipt without terminal evidence after its deadline is completion bad;
do not rewrite RUNNING/STALE diagnostic status into fabricated TIMEOUT. Unknown
availability disposition remains explicit incomplete evidence. A delayed terminal
with trusted <=deadline time can repair late-ingested evidence; an actual late close
remains bad. Settlement corrections update operational aggregates with versioned
reconciliation; avoid decrementing monotonic event counters or exporting duplicates.

Evaluate complete accepted cohorts through a watermark W that guarantees deadline
and grace maturity; window is [W-28d,W), and evaluated_at can be later than W.
Expose lag/pending cohorts and W, rather than pretending newest unresolved turns
are failures/successes. Terminal-only trace/latency use terminal_at; model/dependency
use physical operation completion; SSE uses completion emission time after receipt
grace. Each definition records its own timestamp/population basis explicitly.
Align cloud ratio inputs and local cohort aggregates with the same basis. Export
settled cohort metrics only through a supported timestamp/aggregation adapter;
validate Google ingestion age limits and no historical rewrite assumptions before
enabling that adapter. Until parity is proven, cohort SLOs use controlled backend
rollup results and Cloud dashboards consume those evaluated values with freshness;
do not publish a knowingly incompatible native cloud SLO ratio.

Rollups are compact operational summaries, not a diagnostic analytics warehouse.
Keep pending receipt lifetime bounded by deadline/grace/reconciliation retention;
aggregates cover >=28d plus 3d burn and late-data margin, policy-controlled retention.
Shared schema migration must be authored/reviewed locally and never applied here.
If acceptance receipt/metric capture is lost, coverage is unknown: DATA_SOURCE_UNAVAILABLE
or INSUFFICIENT_DATA, not zero eligible events or 100%. The metrics transport is
unsampled but not lossless; exporter outage can invalidate freshness/coverage.
A permanently incomplete source cannot produce validated production health.

### Trace completeness and SSE evidence

Trace structural rule is applicability-aware and independent of retaining every
child trace. Track nontruncated finite phase bitsets at producers, separate from
the 128-event diagnostic deque. Every terminal requires trusted run/root correlation,
acceptance/validation, a single matching terminal event and attempted/confirmed root
closure. Require started/completed lifecycle phases only when entered and expected
to finish: session load; attachments when present; planning/thread/pending/context
when executed; applicable authority/provenance/egress/final-persistence for a final
answer; SSE emission for SSE responses. A failed interrupted phase requires its
start and valid failure/terminal closure, not a fabricated success event. Simple
turns with no Teams/tool/specialist cannot fail for absent optional children.
If a component did execute, its applicability must be recorded by the owner.

Track root exporter queue acceptance/known delivery failure separately through the
existing processor/queue seam; OTel creation does not prove export. Per-batch exporter
success proves acceptance at that sink only, not indexed Cloud Trace storage. DTO
reports structural completeness, evidence level and delivery status (known/unknown/
not-required-by-retention). Initially capture100%; later intentional healthy sampling
is not incomplete structure. Involuntary drop is telemetry degradation. No global
exported/accepted span ratio is substituted for required-phase per-turn completeness.

Current relay states pending/relayed/disconnected cannot establish browser receipt
and conflate remote disconnect with backpressure in some paths. M9 must add a
separate transport result taxonomy at actual ASGI/queue boundaries, preserving v1
diagnostic delivery compatibility: emitted, relay_succeeded, client_disconnected,
relay_failed, backpressure_failed, unknown. Browser acknowledges receipt of parsed
message.completed, not literal token/UI paint/run.completed.

Minimal M9 receipt producer: bounded same-origin POST /api/observability/sse-receipts
with trusted run correlation and receipt kind only, existing authenticated ownership
validation, expiry, replay/idempotency protection, per-principal rate cap and bounded
body. It is a telemetry write only, never an action/cancel/approval API. A run UUID
alone grants no write permission; production identity integration remains a gate.
Optional receipt nonce is transport security data, excluded from logs/metrics.
Use server timestamp, not arbitrary browser clock, and tolerate duplicate receipts.
No full browser tracing/traceparent rollout (M18) or general analytics SDK in M9.

Confirmed voluntary/remote disconnect before completion eligibility excludes SSE
delivery only; ambiguous connection loss is unknown, not automatically availability
bad. A connected eligible turn with confirmed server relay/backpressure failure is
delivery bad. Missing ack after proposed30s receipt grace is bad only when connected
eligibility and producer coverage are established; otherwise source unavailable/
insufficient, with receipt coverage shown. Backend completion after disconnect is
tracked separately. Server-only relay success never makes SSE SLI healthy.

### Error budgets, burn design, freshness and state precedence

For 0<T<1: allowed_fraction=1-T; allowed_bad=E*(1-T);
consumed_fraction=B/allowed_bad; remaining_fraction=1-consumed_fraction;
remaining_events=allowed_bad-B. Preserve fractional allowance and negative remaining
budget, never round allowance to zero or clamp exhaustion to hide overconsumption.
Availability allowed fraction=.005; completion/trace/SSE=.001; latency/TTFT=.05;
model/dependency=.005. Percent display derives from backend DTO only.
T=1 cost ledger has no finite budget/burn division; null mathematical budget/burn,
explicit zero-tolerance completeness target and separate .9999/24h alert in M10.
Safety invariants have no percentage objective or budget at all.

Burn(w)=(B_w/E_w)/(1-T), requiring positive population and fresh, complete source.
Authoritative windows: fast1h/6h; slow6h/3d or equivalent. Proposed additional medium
1h/24h. Both paired windows must exceed threshold for the same SLO/environment/class.
Provisional factors: fast>=14.4 (critical/page); medium>=6 (high/ticket);
slow>=1 (high/ticket). Proposed minimum100 events per paired window (configurable),
five-minute sustain for fast, fifteen for medium, sixty for slow; low-volume/no-data
cannot claim no burn. Catastrophic/outage/safety/integrity checks are separate and
must not be suppressed by minimum traffic. Factors, volume limits, sustain and
clear hysteresis need baseline review; none is production-approved paging policy.
Resolve when paired condition is false on fresh eligible data for five minutes;
stale/no-data never silently resolves an active incident.

Expose evaluated_at, source_last_updated, population_watermark, window_start/end,
freshness_threshold_seconds, source/evaluator/definition version and coverage.
Proposed metric source freshness300s; short-window completeness requires all expected
buckets or an explicit zero-traffic heartbeat. Never confuse exporter success time,
query time, durable cohort lag or newest event time. A quiet system can have fresh
zero-event snapshots, still no successful SLI. A partially initialized28d history
shows actual observed interval and INSUFFICIENT_DATA until coverage policy passes.
Proposed minimum20 observations for latency percentile display; exact event ratios
remain available labelled low volume, not validated healthy. Policy is central/read-only.

Finite states and precedence:
1. NOT_IMPLEMENTED for deferred cost source (display defined/awaitingM10).
2. DATA_SOURCE_UNAVAILABLE for missing/disabled provider, invalid source or stale data;
   reason distinguishes stale from unavailable and old values are nonhealthy history.
3. INSUFFICIENT_DATA for E=0, too few events, unknown class/outcome, incomplete history/
   capture or unavailable eligible receipt evidence. Counts/unknowns remain visible.
4. BREACHED if full-window SLI<objective (can also have active burn).
5. BURNING if paired actionable burn is active but full-window objective still met.
6. HEALTHY only for fresh, sufficient, covered data with objective met and no burn.
Burn condition details are separate so BREACHED does not hide fast-burn urgency.
Safety incidents are separate critical conditions, not another SLO percentage state.

### Metric changes, histograms, sampling and release correlation

New aggregates only for gaps: slopanoc.turn.accepted / outcomes / duration / active;
slopanoc.sli.events (finite operation=SLI ID, status=good/bad/unknown);
slopanoc.sli.coverage; slopanoc.slo.{value,budget_remaining,burn_rate,source_age};
slopanoc.safety.violations; slopanoc.persistence.* bridge from ProjectionHealth.
Signed budget_remaining is a validated gauge; existing nonnegative exporters must
not be reused unchanged for it. Active is an observable gauge, not cumulative counter.
Reserve only required instruments; do not add duplicate model/tool/dependency counts.
Existing M3 TTFT histogram lacks workload filtering; add bounded workload on that
instrument (and compatible producer/filter contracts) to isolate user streaming
TTFT, rather than issuing a second metric with identical timing samples.
No cost/token accounting fields, persistent ModelUsageObservation or pricing engine.

Metric dimension changes are explicit contract updates: environment canonical values
local/development/staging/production (existing Terraform dev maps to development at
adapter); request_class general/teams_lookup/governed_troubleshooting/complex_multi_agent/
unknown; workload user_turn/synthetic/warmup/ingestion/background; finite SLI operation,
window and outcome registries. Existing agent/tool/dependency/provider/model/status
registries retained. Only dimensions necessary to a specific instrument are emitted;
never Cartesian-product all labels. Add requested keys to attributes.py only with
machine-tested finite registries; both app and Collector reject arbitrary keys/values.
IDs, URLs, SQL, commands, error prose, Git SHA/release/config never become new metric
labels. Environment filter is mandatory and server-authorized on every SLO read.
Release attributes/diagnostic metadata and dashboard deployment annotations correlate
existing Git SHA/release/revision/config where supplied; missing remains unknown.
Do not implement M18 deployment marker/CI automation early.

Histogram audit: SDK1.41.1 default explicit bounds are
0,5,10,25,50,75,100,250,500,750,1000,2500,5000,7500,10000 in instrument units.
Current latency instruments are seconds, so5s exists but8/20/30/45 do not and tail
resolution is poor. Plan explicit duration views with seconds bounds
0,.05,.1,.25,.5,1,2,3,5,8,10,15,20,25,30,40,45,60,90,120,180,300,600
plus overflow, selected per latency instrument; token histogram bounds unchanged.
Threshold good/bad counts are authoritative independent of quantile approximation.
Record histogram contract version/rollout watermark in definition metadata; do not
merge incompatible old/new bucket layouts or claim pre-rollout28d precision. If cloud
descriptor compatibility requires new instrument version, generate explicit v2 mapping
and dual-read transition once, never silently rename or sum duplicate streams.

SLIs/metrics/receipts/safety counters execute independently of trace retention sampling.
Initial100% capture is unchanged. Test suppressed trace export/sampled retention with
unchanged eligible/good/bad counts. Trace structural completeness evaluated before
retention decisions; delivery policy awareness prevents intentional sample drops being
reported as errors. Sampling can never manufacture availability or ledger coverage.

### Alert inventory, grouping and evidence

All alert definitions are source-controlled, environment-bound and initially disabled
for remote notification. Local evaluator can dry-run transitions. Numeric diagnostic/
capacity thresholds below remain provisional; use observed bad/total or saturation
with minimum volume/sustain, never process-lifetime cumulative failures alone.

| Alert ID | Action / condition | Runbook path under docs/Telemetry/runbooks/ |
|---|---|---|
| availability_fast_burn | Critical/page, paired14.4x1h/6h | availability_burn.md |
| slo_medium_burn / slo_slow_burn | High/ticket, paired6x1h/24h or1x6h/3d; availability/latency/completion/TTFT/trace/SSE | availability_burn.md or applicable source runbook below |
| terminal_integrity | Critical/page on confirmed duplicate closure or accepted overdue orphan; distinguish capture uncertainty | terminal_integrity.md |
| safety_violation | Critical/page on any confirmed unauthorized exposure/egress bypass/credential leak/hidden-CoT persistence | safety_violation.md |
| timeout_stall_spike | High/ticket, proposed>5% eligible turns over15m and>=20events; active/stalled age diagnostic | timeout_stall.md |
| gateway_degradation | High/ticket, gateway reliability below99.5%/30m with>=100calls or sustained throttling | power_automate_gateway.md |
| db_pool_exhaustion | High/ticket, proposed>=3exhaustions/5m plus acquisition/saturation evidence | database_pool.md |
| persistence_outage | Critical/page if sustained no successful writes with pending terminal backlog; proposed5m, not idle lack of writes | diagnostic_persistence.md |
| persistence_degradation | High/ticket for elevated failed/rejected/terminal_unpersisted rates | diagnostic_persistence.md |
| model_provider_degradation | High/ticket, provider reliability below99.5%/30m with>=100calls; timeout/rate-limit evidence | model_provider.md |
| telemetry_export_failure | High/ticket, proposed5m persistent drops/failures or unavailable fresh stream with corroborating local/Collector health | telemetry_exporter.md |
| trace_completeness_degradation | High/ticket, covered completeness below99.9%, burn as applicable | trace_completeness.md |
| sse_delivery_degradation | High/ticket, confirmed eligible connected delivery burn/mismatch | sse_delivery.md |
| queue_saturation | High/ticket for sustained backpressure, proposed>=3failures/5m; derivative of root incident | timeout_stall.md |
| planning_failure / source_gap_spike | Medium/informational or ticket under configured sustained baseline; source gap is not automatically outage | planning_source_gaps.md |
| infrastructure_capacity | High/ticket for Cloud Run CPU/memory/concurrency, Cloud SQL connections/storage, Collector queue/memory; baseline thresholds unset until platform source available | infrastructure_capacity.md |
| synthetic_path_failure | High/ticket for confirmed repeated failure of safe approved check, provisional3consecutive failures | synthetic_checks.md |
| cost_ledger_coverage | DEFINED/disabled awaitingM10; <99.99%/24h, severe coverage loss critical perdoc06 | cost_ledger_completeness.md (deferred implementationM10) |

No paging contact or escalation team invented. Alert metadata: stable alert/SLO ID,
environment, bounded class/dependency, counts/value/objective, window/burn, freshness,
definition version and runbook/dashboard reference; permitted diagnostic lookup link.
No business content or arbitrary run metric dimensions.

Group by environment+service+incident family(+dependency for dependency-only ticket).
Highest actionable root SLO burn pages; gateway/provider/timeout/queue symptoms attach
triage context or tickets and do not independently page the same root incident.
Safety and terminal integrity remain independently critical. Suppress lower-severity
notifications during an active higher-severity incident; clear state deterministically.
Cloud Monitoring multiple-condition combiners alone do not guarantee a single page:
https://docs.cloud.google.com/monitoring/alerts/concepts-indepth . Use one combined
expression per burn family plus notification grouping where the approved incident
receiver supports it. If no receiver dedup exists, only root-family policies receive
paging channels; derivative policies stay ticket/informational. No webhook setup now.

Runbooks cover safe diagnosis, likely causes, evidence/freshness checks, read-only
steps, release comparisons and recovery proposals. Any remote recovery action must
stop at the exact-operation approval gate. Gateway runbook explicitly separates
Power Automate from unobserved downstream Graph. Include stuck-request run lookup,
watchdog/timeout/dependency stages and policy-safe retry guidance. No command replay,
unapproved fallback, on-call contact fabrication or secret payload collection.

### Dashboards, synthetic checks and infrastructure scope

Compact operational dashboard files (not FinOps dashboards):

- service_health.json: availability/completion, traffic, four latency classes,
  error budgets/burn, active/stalled/timeouts, trace and SSE coverage/freshness.
- latency_reliability.json: p50/p95/p99 with histogram method, class thresholds,
  lock/planning/cleanup latency, retries/watchdog/queue/executor saturation.
- models_agents_tools.json: M3–M4 rates, failures, first_provider_output TTFT,
  retries/tokens and primary/supporting diagnostics where supplied; no money.
- dependencies_infrastructure.json: gateway latency/errors/throttling, DB acquisition
  and process pool gauges, GCS/Secret Manager/knowledge stages; platform Cloud Run/
  Cloud SQL CPU/memory/connections/storage and provider health where sourced. Direct
  Graph unavailable. BigQuery workload panels unavailable until actual later workload.
- telemetry_safety.json: exporter/Collector queue/refused/dropped/error health,
  projection backlog/failures/freshness, trace coverage and hard safety conditions.

Finite environment/class/agent/provider/dependency variables only; no raw series
explorer or unbounded IDs. Separate process-pool and Cloud SQL instance health.
Unknown/unavailable chart sources display no-data with explanation, not zero.
Release correlation uses existing safe resource metadata without arbitrary metric
label proliferation. Cost charts, profiling, FinOps anomalies and broad M18 CI/drift
automation remain in their owning milestones.

M9 synthetics: local fixture checks for general prompt, governed knowledge, isolated
DB/session and SSE receipt; Teams check only against fixture unless approved tenant
exists. No application lifespan/model warmup/live gateway calls in test harness.
Production check manifest disabled, scoped workload synthetic, safe canned inputs,
nonmutating business operations, schedule/cost caps, no chat/message writes. Invoking
real providers/creating uptime resources still requires exact operation approval.

### SLO API, RBAC and M8 UI plan

Read-only GET /api/observability/slos?environment=<bounded>&window=28d and
GET /api/observability/slos/{slo_id} for bounded details/burn windows. Arbitrary query
expressions/time-series labels and arbitrary windows are rejected; central approved
window allowlist only. Scope one environment per response, never blend authorized
environments. Reuse operational capability and verified_principal, environment_scope,
request admission and safe errors. Operator/Developer-SRE/Admin operational access;
User/FinOps/Auditor gain no automatic technical/SLO access from other capability ceilings.
M10 financial completeness access remains separately financial-scoped if it exposes
accounting data; M9 placeholder includes definition only. No raw conversation data.

DTO: schema_version, definition_version, slo_id/name, target/provisional flag, unit,
window_seconds/start/end, population_basis, eligible/good/bad/unknown/excluded/pending
counts, current_value, threshold, optional p95+method, allowed/consumed/remaining
budget, burn_windows[{seconds,eligible,bad,rate,condition}], state/reason, source_status,
coverage, evaluated_at/source_last_updated/freshness_threshold/population_watermark,
runbook reference and optional safe resource release correlation. All absent numbers
are null, counts finite and nonnegative; remaining budget may be negative. Response
bounded to central inventory, no identifiers/raw series/provider exception bodies.
Configured missing source returns a safe state, not fabricated data or leaked errors.
Authentication/authorization failures preserve existing401/403 semantics.

Query provider has a bounded timeout, read concurrency cap, short TTL snapshot cache
per environment/definition/window and no cache surviving revocation; stale data stays
stale when served from cache. Proposed2s timeout/30s cache TTL, policy-configurable.
Production Google API adapter read-only; fixture provider never impersonates production.
Local backend may use the controlled compact rollup adapter, returning explicit local
source/environment, not production health. Formulas identical across providers.

SlosPanel replaces placeholder with backend cards/detail: current SLI, provisional
objective, threshold/p95 method, rolling28d, budget remaining, burn, counts and freshness.
Use current query hook, strict decoder, view-only components; one bounded request
on entry/manual refresh, optional30s visible polling with existing cancellation
and authorization clearing. No formulas, percentiles, population selection or burn
classification in React. Show NOT_IMPLEMENTED/insufficient/unavailable/stale/low-volume
clearly; cost card says Data source available after M10 with no numeric result.
Do not rebuild Settings or create a standalone SRE app. Normal chat only gains the
minimal safe receipt producer; progress, SSE wire values, execution and approvals stay.

UI-impact rule evaluated:1) M9 creates SLO facts needed in Settings;2) new backend
summary/detail DTOs and minimal receipt contract required;3) SLO panel replacement
belongs in M9, broad operational console/admin editors do not;4) server RBAC/environment
scope mandatory;5) objectives/windows/burn/freshness/config remain effective read-only,
Terraform/backend-policy owned, no runtime editor or IaC bypass.

### Exact planned paths (future implementation, not edits performed now)

New backend modules:
- backend/observability/slo_contract.py
- backend/observability/slo_evaluator.py
- backend/observability/slo_sources.py
- backend/observability/slo_metrics.py
- backend/observability/slo_rollups.py
- backend/observability/slo_alerts.py
- backend/observability/slo_api_models.py
- backend/observability/sse_receipts.py
- backend/observability/synthetics.py

Existing backend paths to extend only at inspected producer/consumer seams:
- backend/observability/{attributes,config,schemas,metrics,runtime,turn_trace}.py
- backend/observability/{model_metrics,agent_instrumentation,tool_instrumentation}.py
- backend/observability/{knowledge_instrumentation,delivery,persistence_metrics}.py
- backend/observability/{models,repository,persistence,retention,service}.py
- backend/api/{app,chat_service,observability_routes}.py
- backend/config/settings.py
- alembic/versions/m9_slo_operational_rollups.py (revision ID allocated during implementation;
  parent must be actual head, presently9f71c2a64e08; no speculative remote migration)

Frontend modifications:
- src/api/observability.ts
- src/api/observabilityTypes.ts
- src/components/shell/settings/observability/SlosPanel.tsx
- src/components/shell/settings/observability/contract.ts
- src/components/shell/settings/observability/Panels.test.tsx
- src/api/client.ts and src/api/sseParser.ts (receipt transport/parser seam only)
New src/api/sseReceipts.ts; src/components/shell/settings/observability/SlosPanel.test.tsx.
Existing src/api/observability.test.ts and observability/contract.test.ts extended.

Local infrastructure/docs to author during implementation:
- infra/observability/collector/config.yaml
- infra/observability/collector/config.local.yaml
- infra/observability/monitoring/{slo_manifest,alert_policies,synthetic_checks}.json
- infra/observability/dashboards/{service_health,latency_reliability,models_agents_tools,
  dependencies_infrastructure,telemetry_safety}.json
- infra/observability/terraform/monitoring/{versions,variables,main,outputs}.tf
- infra/observability/terraform/monitoring/README.md
- docs/Telemetry/runbooks/{availability_burn,terminal_integrity,safety_violation,
  timeout_stall,power_automate_gateway,database_pool,diagnostic_persistence,
  model_provider,telemetry_exporter,trace_completeness,sse_delivery,
  planning_source_gaps,infrastructure_capacity,synthetic_checks}.md
- docs/Telemetry/{02_TELEMETRY_DATA_CONTRACT,06_SRE_SLOS_METRICS_ALERTS,
  21_SLO_FORMULAS_AND_INITIAL_TARGETS}.md (compatible M9 mapping, not target redefinition)
- backend/observability/README.md; .agent/OBSERVABILITY_EXECUTION.md

New tests:
- backend/tests/test_observability_slo_contract.py
- backend/tests/test_observability_slo_evaluator.py
- backend/tests/test_observability_slo_populations.py
- backend/tests/test_observability_slo_rollups.py
- backend/tests/test_observability_slo_alerts.py
- backend/tests/test_observability_slo_api.py
- backend/tests/test_observability_sse_receipts.py
- backend/tests/test_observability_slo_iac.py
- backend/tests/test_observability_slo_performance.py
- backend/tests/test_observability_synthetics.py
- src/api/sseReceipts.test.ts
Extend existing contract/Collector/exporter/model-metrics/lifecycle/reliability/
persistence/migration tests at changed seams. No requirements/package dependency
change presumed; only add a pinned dependency if inspected provider adapter requires it.

### Sequential implementation and validation plan

Upon separate authorization:1) central contracts/formulas and deterministic fixtures;
2) required producer gaps, compact receipts/rollups and coverage/freshness;
3) bounded metrics/exporter/Collector parity and histogram transition;
4) backend provider/evaluator/API and alert state machine;
5) M8 SLO view + minimal SSE receipt source;
6) declarative cloud definitions/runbooks/disabled synthetic manifest;
7) isolated validations, regressions, performance, hygiene and individual gates.
All steps are M9 only; no M10 implementation or production activation by implication.

Planned tests (not run this planning pass):
- Every definition: zero/all-good/all-bad/mixed/excluded/stale; E=0 null; exact target;
  one over threshold; negative remaining budget; T=1 no division. Counts conserve.
- Windows: cutoff included/end excluded, just inside/outside28d, UTC/DST/future times,
  incomplete history; acceptance/terminal cohort crossover and unresolved grace.
- Lifecycle: accepted-before-lock, single finish/backstop, valid policy/source gap,
  after-accept cancellation, errors/timeouts, process-loss orphan, late closure vs
  late ingestion, receipt loss, reconciliation idempotency and cross-instance rollup.
- Classification: four bounded classes, overlap precedence, unknown/misclassified
  excluded from class distribution with coverage failure; never prompt reevaluation.
- Each latency threshold5/8/20/30/45: exact <= and just greater, percentile method,
  low count/missing/nonfinite duration, old/new bucket layout and counter reset.
- TTFT: streaming first_provider_output, retries, unknown/nonstream/no output,
  embedding/warmup/ingestion exclusions, no zero substitution.
- Trace: required/failed/applicable phases, no optional specialist/tool/Teams,
  timeline truncation, one root/correlation/closure, export acceptance vs storage
  evidence, intentional sampling vs accidental drop.
- SSE: message receipt vs relay only, duplicate/replayed/foreign receipt, voluntary
  disconnect, unknown loss, ASGI failure/backpressure, completed after disconnect,
  unavailable producer and missed ack with confirmed eligibility.
- Burn/alerts: each window/factor/minimum/sustain/exact threshold, activation/clear,
  paired-window AND, no-data/stale hold, group suppression and independent safety.
- Safety: synthetic confirmed markers for all four invariants produce critical
  incidents immediately; sanitizer rejection alone is not proof of leaked secrets.
  Never transmit unsafe real credentials/commands/content.
- API:401/403/environment scope, partial permissions, DTO bounds, all finite states,
  stale cached result, cost placeholder, deadlines/rate limits/concurrency.
- UI: backend values/objectives/budget/burn/window/freshness, unknown/no-data/unavailable,
  revocation clearing, cost awaitingM10; inspect no client formula duplication.
- Cardinality/privacy: exact name/unit/dimension registries, poison IDs/prose/URL/SQL/
  prompt/Teams/token fields, dashboard variables, both Collector configs and exemplars.
- Synthetics: fake model/Teams/knowledge/local DB/SSE only, source tagging, no write
  operations or uncontrolled external calls. Infrastructure unavailable is truthful.

Commands after checking isolated runner/sitecustomize guards still exist and block
non-loopback traffic (if absent recreate locally before running):
` .venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_observability_slo_contract.py backend/tests/test_observability_slo_evaluator.py backend/tests/test_observability_slo_populations.py backend/tests/test_observability_slo_rollups.py backend/tests/test_observability_slo_alerts.py backend/tests/test_observability_slo_api.py backend/tests/test_observability_sse_receipts.py backend/tests/test_observability_slo_iac.py backend/tests/test_observability_slo_performance.py backend/tests/test_observability_synthetics.py `
Then enumerate M0–M9 observability tests with rg and run through the same guarded
runner; run applicable full backend regressions via guarded manifest with live-only
tests explicitly inventoried, not silently skipped. Never boot normal application
lifespan or run migrations using inherited shared/cloud configuration.
`npm test`; `./node_modules/.bin/tsc -b`; `npm run build`; `git diff --check`.
Collector native validation/runtime fixtures only against loopback sink and explicit
fake cloud exporters. Terraform fmt -check -recursive; provider-free module validate;
monitoring module validate only with locally available pinned plugin/schema and no
remote backend/init/download. Missing tool/plugin means NOT VALIDATED, not pass.
No terraform plan/apply/import/state, live Google API test or shared DB migration.

Performance: synthetic bounded metric snapshots for one/all SLOs,28d minute buckets
(40,320 per series), class/provider combinations, API cached/uncached,1x/10x event
volume; injected clock, no sleeps. Record median/p95 CPU/memory/response size and
bounded query/rollup scaling; provisional local target evaluator<100ms full set,
API<2s query budget, producer synchronous increment under existing10ms instrumentation
budget. Report measured local limits, not production Monitoring scale or CPU guarantees.

### Individually planned M9 exit criteria

All statuses **NOT RUN / NOT IMPLEMENTED** until implementation and tests exist.

| Gate | Required evidence |
|---|---|
| C01 central full inventory | All doc21 service/model/dependency SLO definitions, targets/provisional status and cost metadata present |
| C02 exact populations | Accepted/excluded/cancel/policy/source-gap/failure semantics and positive unknown coverage tests |
| C03 rolling28d | UTC half-open boundaries, explicit watermark/cohort basis, configuration and cross-window tests |
| C04 availability distinct from completion | Safe application outcome vs any timely terminal; no HTTP200 substitution |
| C05 four latency classes | Server-owned bounded metadata, unknown coverage, root acceptance→terminal and threshold compliance |
| C06 TTFT | M3 first_provider_output and streaming user-operation population, null unknown |
| C07 trace completeness | Nontruncated applicability masks, trusted one-root closure, honest delivery/sampling evidence |
| C08 SSE | Actual browser receipt, connected eligibility and separate relay/backpressure/disconnect outcomes |
| C09 safety | Four zero-tolerance critical incident markers, no error-budget allowance |
| C10 budgets/burn | Deterministic formulas, negative overconsumption, paired windows and provisional factors |
| C11 missing/stale data | Finite states, null zero-event ratios, incomplete source/history cannot be healthy |
| C12 backend/API | Canonical providers/evaluator, bounded DTO, RBAC/environment scope and authorization tests |
| C13 M8 integration | Existing Settings SLO panel renders server values and honest cost/no-data states, no React math |
| C14 bounded unsampled metrics | Application views/exporter and both Collector filters agree; labels/values/exemplars machine tested |
| C15 histogram continuity | Exact thresholds supported, percentile method and old/new source history truthful |
| C16 durable cohort coverage | Compact receipts/rollups, orphan/late evidence/idempotency/local restart tests; no diagnostic warehouse |
| C17 alerts/dry-run | Activation/resolution/pairing/grouping/severity/stale/safety and safe evidence validated offline |
| C18 dashboards/runbooks | Actionable groups, all alert mappings, platform unavailable semantics, no fake contacts |
| C19 safe synthetics | General/knowledge/local DB/SSE/Teams fixture coverage; live schedule disabled without approval |
| C20 release/environment | Explicit environment separation and existing safe release/config correlation, missing unknown |
| C21 M10 boundary | Cost DEFINED — DATA SOURCE AVAILABLE IN M10; no financial ledger/estimation/accounting implementation |
| C22 imports/regressions/performance | M0–M9 + applicable backend/frontend tests, TS/build, Collector/IaC checks and measured performance |
| C23 repository/safety | Scoped diff/untracked review, preexisting work preserved, no remote mutation |
| C24 roadmap local acceptance | Health objectively measurable with real local producers→backend→UI; alert dry-run evidence retained |

Local completion requires every applicable local gate PASS. Cloud deployment/live
identity/Postgres concurrency/platform ingestion, notifications and alert factors
remain separately pending and must never be called production-validated. M10 cannot
start from this planning record; Ready for Next Milestone stays NO now.

### Cloud operations, risks and planning validation result

Cloud deployment status = **NOT EXECUTED — USER APPROVAL REQUIRED**.
Future operations: shared M9 additive operational migration after M7 migration;
backend/frontend deployment; Monitoring service/SLO descriptors, dashboards/alert
policies; Collector config deployment and self-metric scraping; production read-only
Monitoring adapter credential/identity wiring; optional approved synthetic schedule/
tenant. No IAM role/channel/webhook/service-account change is assumed necessary or
authorized. No project/account/tenant selected and no mutation command proposed/run.
Every eventual exact operation must supply all10 AGENTS.md impact/rollback fields
and separate approval; no production validation action is authorized by readiness.

Risks: async projection/capture loss can invalidate SLO coverage; crash/orphan cohort
receipts and multi-instance settlement need dedicated tests. Class metadata currently
incomplete; avoid unknown→general. Browser receipt/auth/connectivity evidence missing;
source stays unavailable until added safely. Sampling/export acceptance cannot prove
cloud storage. Histogram transition limits historical precision. Cloud cohort timestamp
constraints may require backend rollup evaluated-value source rather than native ratio;
prove formula/window parity before activation. Production identity remains absent.
Provisional targets/burn thresholds/contacts/channels are not rollout approval.
Terraform provider/schema availability may limit offline validation. No local planning
blocker; these are explicit M9 implementation/production validation gates.

Implementation performed: **NONE**. Actual repository file changed in this pass:
**.agent/OBSERVABILITY_EXECUTION.md only**. Validation: authoritative doc/source audit,
predecessor completion-evidence review, actual SDK histogram source inspection and
working-tree review; no M9 tests/builds or historical regression reruns claimed.
Planning final review PASS: SHA-256 comparison across829 backend/frontend/infra/
Telemetry-doc/execution-record files shows exactly this record changed, no added or
removed files. Required M9 plan topics and C01–C24 present. git diff --check PASS;
git status/tracked diff review preserves the preexisting dirty/untracked tree.
The execution record itself is preexisting untracked content, so its own status,
plan coverage and content hash were checked explicitly rather than relying on git diff.
Failures/repairs: no implementation failure; source gaps above are planned M9 work.
Known limitations: design only, no evaluator/API/UI/cloud runtime evidence yet.

**Ready To Implement M9: YES (local only, separate implementation instruction).**
**Ready for Next Milestone: NO. M10 BLOCKED. STOP after planning.**


## M9 implementation start — 2026-10-08

User authorizes M9 local implementation only; this instruction supersedes planning
cancellation and burn policy: confirmed USER_OR_CLIENT_CANCEL excluded from availability;
SERVICE_CANCEL and UNKNOWN_CANCEL_ORIGIN eligible bad. DEADLINE_TIMEOUT/SYSTEM_FAILURE
retain actual cause. Terminal completion counts all four timely terminal states.
Canonical burn defaults: strict >14.4 long1h/short5m; >6 long6h/short30m;
>1 long3d/short6h. No optional intermediate tier. PROVISIONAL — CALIBRATION REQUIRED.
Doc21 explicitly formalizes model and per-dependency reliability99.5%; include formal
objectives, direct Graph source unavailable. Cost metadata only until M10.
Scope/files/specifications/reuse/invariants/tests/exit criteria: preceding detailed
M9 plan, amended by the attached implementation corrections. Minimal authenticated
SSE receipts diagnostic-only; M7 verified identity gate unchanged. Add compact
operational tables and async receipts/rollups, no financial ledger/outbox. No remote
operations or deployments authorized. All production activation remains pending.
UI-impact: backend-owned SLO values in existing Settings; protected safe read contracts;
view-only provisional policy; no frontend formula truth. M10 blocked.
Ready for Next Milestone: NO until implementation and all local gates pass.


## M9 completion — 2026-10-08 — IMPLEMENTED AND LOCALLY VALIDATED

This completion section supersedes the historical M9 planning-only and in-progress
statuses above. User implementation corrections are authoritative. M10 is NOT
IMPLEMENTED BY DESIGN and is not started. No deployment or external mutation.

### Implementation and ownership

Canonical executable definitions: backend/observability/slo_contract.py. Sixteen
formal entries are grounded in doc21: availability99.5%, terminal completion99.9%,
four latency classes95% good <=8/20/30/45seconds, streaming first_provider_output
TTFT95% <=5seconds, trace completeness99.9%, SSE delivery99.9%, model reliability
99.5%, separate gateway/DB/storage/knowledge/direct-Graph reliability99.5%, and
cost-ledger completeness100% architectural target. Doc21 explicitly formalizes
model and dependency reliability, so those are not an unapproved formal expansion.
All numeric runtime objectives are PROVISIONAL. Direct Graph is source unavailable;
cost is DEFINED_NOT_EVALUATED/M10_COST_LEDGER/DATA_SOURCE_AVAILABLE_IN_M10 with no
counts substituted from models and no finite budget/burn. Tool failures/retries,
model/token volume, root/agent/tool/dependency timing, timeouts/stalls, queue/pool,
export/persistence and platform saturation are supporting signals, not extra formal
service objectives. No monetary calculation, pricing, allocation, ledger or outbox.

Availability population: accepted schema-valid/authorized canonical execution;
valid application outcomes include clarification/source gap/approval/safe rejection.
Only confirmed USER_OR_CLIENT_CANCEL is excluded after acceptance. SERVICE_CANCEL,
DEADLINE_TIMEOUT, SYSTEM_FAILURE and UNKNOWN_CANCEL_ORIGIN remain eligible bad.
Origin is bounded server metadata, never free-form inference. Terminal completion
counts exactly one timely COMPLETED/FAILED/TIMEOUT/CANCELLED independently of
availability. Overdue receipt settlement changes only SRE counts, never M2 state.
Versioned differential transactional receipt updates prevent repeated cohort counts;
browser acknowledgements are sticky and idempotent. Knowledge now counts a whole
retrieval once rather than inflating its population with five local stage spans.

Class precedence: two distinct primary specialists => COMPLEX_MULTI_AGENT;
otherwise governed routing => GOVERNED_TROUBLESHOOTING; registered Teams lookup =>
TEAMS_LOOKUP; explicit general route => GENERAL; otherwise UNKNOWN. Source routing
and operational topology facts are observed without changing intent or governance.
Unknown class/timing remains coverage rather than being guessed into a class.
Teams latency is the complete canonical M2 root duration, not gateway timing.

Canonical evaluation: UTC rolling28d, minute-aligned half-open [start,end), injectable
clock, deadline+120second ingestion/settlement watermark. Event-ratio good/bad counts
are exact, including threshold equality. Native/descriptive p95 is not a second SLO
truth. E=good+bad; allowed_bad=E*(1-T); consumed=bad/allowed_bad; remaining=1-consumed.
Negative remaining is preserved; no-data has null ratios/budgets and INSUFFICIENT_DATA.
100% architectural objectives have no finite burn. Freshness300s, partial history,
capture gaps, unknowns and minimum volumes remain explicit. Source loss cannot create
a green result. Clean capture can recover only after a new continuous full window.
Provider seam separates explicit fixtures, runtime rollups and an unavailable future
read-only Monitoring adapter. SQL disjoint-range aggregation bounds each snapshot to
six rows while preserving all canonical window counts; M7 diagnostic events are not
scanned as a 28-day analytical store.

Burn pairs (strict >, both windows, >=100 measured eligible events each, no unknown,
fresh complete coverage): fast PAGE long1h/short5m @14.4; sustained PAGE long6h/
short30m @6; slow TICKET long3d/short6h @1. Optional intermediate tier is absent.
Page uses one combined PromQL condition; slow is a separate logical policy. Queries
are generated from the canonical registry and explicit match keys, with exact
single-environment binding and fresh source-age gate. Source age continues aging even
if the writer stops. Local dry-run groups service burn, suppresses equivalent derivative
paging context, retains independent safety and holds incidents through missing data.
No Cloud Monitoring single-notification guarantee is claimed. All policies disabled,
notification channels empty/deployment inputs; baseline/source-pending policies do not
silently become active. No invented contacts. All actionable policies map to runbooks.

Safety observations: four registered confirmed codes only (unauthorized command
exposure, command-egress bypass, credential telemetry leak, hidden-CoT persistence),
critical independent incident conditions, zero tolerance, no percentage budget.
Tests use synthetic condition codes and harmless markers; no unsafe payload storage.

Trace assessment is unsampled and applicability-aware: root/correlation/terminal
closure plus all entered/closed phase occurrences and required agent/tool/model/
dependency occurrences. Optional unused components are not required; retained traces
and exporter loss are separate from structural instrumentation completeness. Tracker
updates are guarded and serialized; observer errors do not affect business outcomes.

Minimal SSE receipt observes browser receipt of canonical message.completed only.
No response rendering/read/approval/business semantics. Existing verified principal,
OWN_PROGRESS, exact owner digest/environment/UUID and expiry gates apply. Receipt
submission is bounded, best-effort, idempotent, no retries and does not await on chat
completion. Server endpoint flushes scheduled diagnostic updates within its total2s
budget to avoid a fast acknowledgement racing the emission projection. Sync turns,
intentional pre-completion disconnects, relay/backpressure failures, missing/unknown
receipts and confirmed browser receipt have distinct operational treatment. No
receipt can modify M2 terminal state, session persistence, authority or commands.

Additive migration b37e90a14c62 follows9f71c2a64e08 and adds three SRE-only tables.
Finalized receipt retention2d, aggregate retention35d; no business content. Async
bounded1024publication queue, short write/read deadlines, gap/failure coverage,
settlement and pruning; opt-in SLO_ENABLED defaults false and does not create schema.
Metric dimensions exactly environment/registered operation/registered status/bounded
window; no identifiers/release SHA/content or exemplars. Root accepted traffic/duration,
execution-local active gauge, exact SLO/budget/freshness and projection health extend
existing M3–M6 metrics. Histogram contractv2 includes5/8/20/30/45seconds, documented
cutover rather than silently merging old/new bucket streams. Both Collector configs
apply SDK-equivalent finite-name/label/type/content boundaries and preserve seconds.
Active gauge is local execution telemetry; durable cross-instance lookup stays M7.

UI evaluation: creates information required by Settings→Observability & FinOps→SLOs;
protected safe GET /slos and /slos/{slo_id} contracts supply values. Current-milestone
UI replaces M8 placeholder inside existing layout/patterns. RBAC stays server-owned;
policy/IaC settings are view-only; no frontend SLO/budget/burn calculations. States,
provisional objectives, window, negative budget, burn, freshness and coverage displayed.
FinOps tab/accounting implementation remains untouched. A captured safe backend DTO
fixture validates cross-language decoding/rendering. Normal chat sees no unrestricted
diagnostics and completes if diagnostic receipt submission fails.

Five focused source-controlled dashboards: service health; latency/reliability;
models/agents/tools; dependencies/infrastructure; telemetry/safety. Direct dependency
is named Power Automate / Teams gateway. Model panels contain token volume, no money.
Descriptive histogram percentiles use native Monitoring alignment; platform Cloud Run
CPU/memory and Cloud SQL connections/storage require exact deployment inputs. Catalogs
checked read-only: https://docs.cloud.google.com/monitoring/api/metrics_gcp_p_z and
https://docs.cloud.google.com/monitoring/api/metrics_gcp_c. Collector internal/platform
and direct Graph absence remain explicit; BigQuery/monetary workloads are deferred.
Safe synthetic registry covers five approved paths with explicit adapters, bounded
execution and synthetic workload tag, disabled by default. Local general/SSE/knowledge/
SQLite and existing Teams fixture tests provide evidence; no live schedule/tenant.

### Validation, failures and repairs

- Full applicable backend: guarded offline `pytest -q backend/tests
  --ignore=backend/tests/manual`: **5273 passed,12 skipped,681 warnings,131.22s**.
  Full preceding run5272passed; final run adds capture recovery. Focused final M9
  evaluator/rollups/source/alerts/IaC/metrics/API/integration/synthetics: **213passed,
  4.50s**, includes an additional explicit BURNING-state check after full collection.
  Counts overlap and are not added together. Performance test passes in full suite.
- Existing predecessor tests plus actual M9 SDK→Collector→loopback sink, poisoned
  high-cardinality protobuf injection, new ChatService/SRE integration: **22passed,
  23.12s** focused. Collector0.160.0 native validates both production/local configs;
  production syntax validation never starts a GCP exporter/deployment.
- Frontend entire suite: **53files/926tests passed**,10.17s. `npm run build` includes
  TypeScript `tsc -b` PASS and Vite production build PASS (2052modules,3.29s).
- New additive migration: isolated SQLite upgrade/downgrade and PostgreSQL offline
  SQL validation pass within full migration tests. No PostgreSQL server/concurrency
  or shared-cloud migration is claimed.
- Terraform fmt-check recursive PASS. Existing provider-free module validated in
  prior scoped validation. Monitoring module: backend-disabled scratch module,
  pinned signed Google7.0.0 provider/committed lock, Terraform1.14.x validate PASS.
  Local plugin handshake socket needs sandbox escalation; clean `env -i` schema
  validation has no cloud credentials/state/plan/apply. No remote resource access.
- Native PromQL parser0.5.0, pinned wheel isolated in /private/tmp: **55 final queries
  parse**, including39alert conditions/16non-histogram dashboard queries. Native
  Monitoring filter/percentile JSON is static; ingestion/query binding not live proven.
  Distinct short/long population parity tests verify all pairs; strict equality,
  volume/unknown/stale/no-data and recovery tests pass. Generated artifact drift checks
  pass. UI only formats API percentages, never computes SLO truth.
- Full-suite skips:11tests require absent real knowledge corpus;1parameter is an
  intentionally indistinguishable None pending-entry case. No M9 test skipped.
  Warnings include existing ADK/Starlette deprecations and SQLite worker cleanup;
  passing tests do not certify real corpus/provider/platform behavior.
- Initial repairs: UI assertion matched both objective/current labels; changed to
  explicit two matches. Removed stale unused placeholder test import for TypeScript.
  Sandbox local socket failures rerun with guarded loopback allowance. Route security
  inventory now includes exact3M9routes. Seven stale regression failures repaired
  only in tests: compare shared physical model delegates rather than agent wrappers;
  explicitly fake Source drawer contributor lookup; use existing model-driven time
  range in hallucination tests rather than newer deterministic retrieval branch.
  Assertions still test authority/provenance and shared transport ownership. No
  production governance/authority changes. Strengthened parity extraction so clauses
  cannot accidentally borrow a later window. Native parser rejected the modern quoted
  metric syntax; generated selectors now use standard __name__ string matching.
  Wrong library import in a scratch audit script was corrected without file impact.
  No automatic approval-review rejection or blocked cloud operation occurred.

Tests are guarded by /private/tmp/slopanoc-m7-test-guard/run_tests.py and sitecustomize:
cloud/production environment and proxies removed, explicit disposable SQLite URLs,
warmup/Vertex disabled, non-loopback socket connects rejected. Local socket/tool
escalation does not grant any external mutation. PyPI/provider catalog downloads
were read-only public artifact requests into scratch space, not cloud deployments.

### Performance measurements

Representative local dataset:28days,40320minute buckets/SLO,14runtime populations,
564480aggregate rows in isolated SQLite. Five repetitions except100queue publications.
No production-scale/capacity commitment. Full API measured through protected service
facade/provider/evaluator/DTO; HTTP serialization not included in those timings.

| Measurement | Median ms | Max ms | Repetitions |
|---|---:|---:|---:|
| one_slo_raw_40320_minutes | 25.5633 | 26.0735 | 5 |
| full_set_compact_evaluation | 0.5082 | 0.5261 | 5 |
| transactional_rollup_update | 1.1118 | 1.5876 | 5 |
| protected_api_full_set | 436.7199 | 467.4842 | 5 |
| bounded_receipt_publication | 0.0024 | 0.069 | 100 |
| Full16card SLO UI/jsdom (isolated run) |12.448|16.630|5|
| Full16card SLO UI/jsdom (full-suite concurrent load)|31.273|65.324|5|

### Individual blocking exit criteria

All statuses below are LOCAL validation, not production validation. The user explicitly
separates this local completion gate from later approved deployment/live calibration.

| Criterion | Result | Evidence |
|---|---|---|
| Canonical registry | PASS | slo_contract/manifest drift |
| Formal/supporting distinction | PASS | doc21 inventory |
| Provisional objectives | PASS | DTO/UI/policies/runbooks |
| UTC rolling28d evaluator | PASS | half-open boundary tests |
| Availability cancellation-aware denominator | PASS | five valid outcomes and five failure/origin cases |
| Terminal completion distinct | PASS | all4states/deadline/missing/late/duplicate/idempotency |
| Deterministic classification | PASS | bounded precedence/overlap/unknown tests |
| Canonical latency semantics | PASS | 95%good<=8/20/30/45 exact boundaries |
| TTFT M3 semantics | PASS | first_provider_output tests |
| TTFT coverage visible | PASS | unknown streaming observations/API/UI |
| Applicability-aware trace assessment | PASS | component/phase occurrence tests |
| Sampling-independent completeness | PASS | runtime structural facts before export/retention |
| SSE diagnostic/idempotent | PASS | owner/durable-repeat tests |
| SSE failure cannot alter chat | PASS | actualChatService/disconnect andfrontend failure tests |
| Zero data never healthy | PASS | all definitions zero/null states |
| Stale semantics | PASS | source aging/freshness tests |
| Correct error budgets | PASS | zero/exact/negative/T1 tests |
| Canonical burn pairs | PASS | fast/sustained/slow registry |
| Both windows required | PASS | distinct-window fixtures/strictthreshold |
| Alert definitions match formulas | PASS | generated pair parity |
| Monitoring multiplicity truthful | PASS | onecondition/grouping documentation |
| Hard safety zero tolerance | PASS | four confirmed codecritical SDK/Collector tests |
| Bounded/unsampled aggregates | PASS | SDK+Collector poison tests |
| Protected SLO APIs | PASS | allroles/identity/environment/DTO tests |
| M8 panel backend results | PASS | 926frontend suite/capturedDTO |
| Cost remains M10-owned | PASS | metadataonly/nullcountsbudgetburn |
| Source-controlled artifacts | PASS | 5dashboards/42policyinventory/15runbooks/localTerraform |
| Local alert dry-run | PASS | burn/recovery/stale/unknown/safety/grouping |
| Formula parity | PASS | allcanonicalpairs distinctpopulations |
| Regressions/build | PASS | 5273backend/213focused/926frontend/TSViteCollectorIaC |
| M10 unimplemented | PASS | hash/diffaudit/noledgerpricing |
| No cloud mutation | PASS | localguardedcommandsonly |

### Known limitations and production activation gate

Cloud deployment status = NOT EXECUTED — USER APPROVAL REQUIRED.
Cloud Monitoring deployment: NOT EXECUTED.
Alert policy deployment: NOT EXECUTED. Dashboard deployment: NOT EXECUTED.
Production alert/noise calibration: NOT LIVE-VALIDATED.
Production SSE receipt validation: NOT LIVE-VALIDATED.
Production metric ingestion/descriptor binding, identity/owner continuity, platform
series, multi-instance PostgreSQL locking/crash behavior and receiver/channel grouping:
NOT LIVE-VALIDATED. M10 cost ledger: NOT IMPLEMENTED BY DESIGN.

Exact pending operations, to be separately specified/approved before execution:
shared Cloud SQL additive migration b37e90a14c62 after M7 9f71c2a64e08; backend/frontend
and Collector deployment with effective policy; Monitoring dashboards/disabled alert
policies; deployment-owned notification wiring; production identity prerequisite;
approved tenant/synthetic scheduling; approved live read-only ingestion/receipt/noise
validation. Target project/account/environment/resources are not inferred or selected.
No apply, plan, import, state mutation, IAM/channel/API/billing change or external
notification has been executed. Each eventual action needs all10 AGENTS.md impact/
security/cost/rollback fields and exact approval. Approval for local M9 is not approval
for any pending operation. No automatic remote remediation exists.

Operational capture is bounded best-effort telemetry, not accounting-grade delivery.
Lost publications invalidate coverage; aggregate recovery needs a new complete window.
Crash-before-write/distributed fault behavior needs approved production validation;
local SQLite does not establish PostgreSQL concurrency or multi-instance guarantees.
Full-window warm-up is required before a fresh deployment can report HEALTHY. Cloud
Monitoring adapter is an explicit future seam; runtime rollups are current source.
Unconnected platform sources and baseline-pending capacity/source-gap/synthetic
policies remain disabled rather than inventing effective thresholds. Source-controlled
policy deployment does not establish real signal availability. Numeric objectives and
minimum-volume/noise defaults remain provisional. No performance claim beyond local
synthetic measurements. Previous local M0–M8 work and unrelated dirty work preserved.

### Actual files changed and repository review

Compared SHA-256 against the pre-implementation836file baseline, including untracked
predecessor sources. No baseline file removed. Scoped production changes are additive
observer/metric/API/UI/IaC integrations; ancillary test changes only repair compatibility
and hermetic local fixtures. No unrelated production file changed in M9. Full tracked
status/diff/stat and untracked inventory inspected; baseline hashes distinguish M9
changes from the preexisting dirty M0–M8 tree. git diff --check PASS. No secrets, exports,
databases, Terraform cache/state or generated production payloads added.

Modified existing files:

- `.agent/OBSERVABILITY_EXECUTION.md`
- `alembic/env.py`
- `backend/agents/team_manager/operational_routing.py`
- `backend/api/app.py`
- `backend/api/chat_service.py`
- `backend/api/observability_routes.py`
- `backend/knowledge/retrieval/service.py`
- `backend/observability/README.md`
- `backend/observability/agent_instrumentation.py`
- `backend/observability/attributes.py`
- `backend/observability/config.py`
- `backend/observability/dependency_instrumentation.py`
- `backend/observability/dependency_metrics.py`
- `backend/observability/execution_metrics.py`
- `backend/observability/metrics.py`
- `backend/observability/model_instrumentation.py`
- `backend/observability/runtime.py`
- `backend/observability/turn_trace.py`
- `backend/tests/test_api_security_contract.py`
- `backend/tests/test_conversation_target_regression.py`
- `backend/tests/test_latency_diagnosis_pass.py`
- `backend/tests/test_observability_collector.py`
- `backend/tests/test_observability_migrations.py`
- `backend/tests/test_r1_r3_correctness_regression.py`
- `backend/tests/test_r2_authoritative_retrieval_enforcement.py`
- `backend/tools/knowledge/runtime.py`
- `docs/Telemetry/02_TELEMETRY_DATA_CONTRACT.md`
- `docs/Telemetry/21_SLO_FORMULAS_AND_INITIAL_TARGETS.md`
- `infra/observability/collector/config.local.yaml`
- `infra/observability/collector/config.yaml`
- `src/api/observability.ts`
- `src/api/streamChat.ts`
- `src/components/shell/settings/observability/Panels.test.tsx`
- `src/components/shell/settings/observability/SlosPanel.tsx`

Added files:

- `alembic/versions/b37e90a14c62_sre_operational_rollups.py`
- `backend/observability/slo_alerts.py`
- `backend/observability/slo_api_models.py`
- `backend/observability/slo_contract.py`
- `backend/observability/slo_evaluator.py`
- `backend/observability/slo_iac.py`
- `backend/observability/slo_metrics.py`
- `backend/observability/slo_rollups.py`
- `backend/observability/slo_service.py`
- `backend/observability/slo_sources.py`
- `backend/observability/slo_synthetics.py`
- `backend/tests/test_observability_slo_alerts.py`
- `backend/tests/test_observability_slo_api.py`
- `backend/tests/test_observability_slo_evaluator.py`
- `backend/tests/test_observability_slo_iac.py`
- `backend/tests/test_observability_slo_integration.py`
- `backend/tests/test_observability_slo_metrics.py`
- `backend/tests/test_observability_slo_performance.py`
- `backend/tests/test_observability_slo_rollups.py`
- `backend/tests/test_observability_slo_sources.py`
- `backend/tests/test_observability_slo_synthetics.py`
- `docs/Telemetry/runbooks/availability_burn.md`
- `docs/Telemetry/runbooks/cost_ledger_completeness.md`
- `docs/Telemetry/runbooks/database_pool.md`
- `docs/Telemetry/runbooks/diagnostic_persistence.md`
- `docs/Telemetry/runbooks/infrastructure_capacity.md`
- `docs/Telemetry/runbooks/model_provider.md`
- `docs/Telemetry/runbooks/planning_source_gaps.md`
- `docs/Telemetry/runbooks/power_automate_gateway.md`
- `docs/Telemetry/runbooks/safety_violation.md`
- `docs/Telemetry/runbooks/sse_delivery.md`
- `docs/Telemetry/runbooks/synthetic_checks.md`
- `docs/Telemetry/runbooks/telemetry_exporter.md`
- `docs/Telemetry/runbooks/terminal_integrity.md`
- `docs/Telemetry/runbooks/timeout_stall.md`
- `docs/Telemetry/runbooks/trace_completeness.md`
- `infra/observability/dashboards/dependencies_infrastructure.json`
- `infra/observability/dashboards/latency_reliability.json`
- `infra/observability/dashboards/models_agents_tools.json`
- `infra/observability/dashboards/service_health.json`
- `infra/observability/dashboards/telemetry_safety.json`
- `infra/observability/monitoring/alert_policies.json`
- `infra/observability/monitoring/slo_manifest.json`
- `infra/observability/monitoring/synthetic_checks.json`
- `infra/observability/terraform/monitoring/.terraform.lock.hcl`
- `infra/observability/terraform/monitoring/README.md`
- `infra/observability/terraform/monitoring/main.tf`
- `infra/observability/terraform/monitoring/outputs.tf`
- `infra/observability/terraform/monitoring/variables.tf`
- `infra/observability/terraform/monitoring/versions.tf`
- `src/api/sloContract.ts`
- `src/api/sloFixture.ts`
- `src/api/sloTypes.ts`
- `src/api/sseReceipts.test.ts`
- `src/api/sseReceipts.ts`
- `src/components/shell/settings/observability/SlosPanel.test.tsx`

**Exit Criteria: all32 blocking local criteria PASS.**
**Ready for Next Milestone: YES (local progression only). STOP. M10 not started.**

## M10 — FinOps Runtime Usage Ledger: planning only — 2026-10-08

**M10 — PLANNED — NOT IMPLEMENTED.** Current authorization is audit and planning
only. This section supersedes historical M10 blocked-by-M9/not-started wording.
Last completed milestone: M9. M11 remains blocked. Ready for Next Milestone: NO.
No implementation, migration authoring, tests of new functionality or deployment
performed in this pass. Only this execution record is changed.

### Entry gate and authoritative scope

Entry gate PASS: matrix and final acceptance sections record M0 VALIDATED COMPLETE
and M1–M9 IMPLEMENTED AND LOCALLY VALIDATED, with each local progression gate YES.
M9 completion records all 32 blocking local criteria PASS, 5273 backend tests,
213 focused tests (overlapping), 926 frontend tests and build/native Collector/IaC
checks passing. These are historical results, not rerun or newly certified here.
M9 cost completeness is DEFINED_NOT_EVALUATED; production Monitoring deployment
pending; M10 ledger unimplemented by design; no cloud mutation recorded.
Actual source agrees: evaluator explicitly suppresses M10 counts; no FinOps package,
accounting table or active financial endpoint; FinOpsPanel is a static placeholder.

Read AGENTS.md, .agent/PLANS.md and the execution record; docs/Telemetry/README.md,
00_MASTER_BUILD_CONTRACT.md, 01_TARGET_ARCHITECTURE.md, 02_TELEMETRY_DATA_CONTRACT.md,
03_INSTRUMENTATION_AND_RUNTIME.md, 05_STORAGE_APIS_AND_UI.md,
07_FINOPS_ARCHITECTURE.md, 08_FINOPS_DATA_MODEL_AND_UNIT_ECONOMICS.md,
09_SECURITY_PRIVACY_RETENTION_SAMPLING.md, 11_SLOPANOC_INTEGRATION_MAP.md,
12_IMPLEMENTATION_ROADMAP.md, 13_TEST_VALIDATION_ACCEPTANCE.md,
14_CODEX_EXECUTION_GUIDE.md, 15_DEFINITION_OF_DONE.md, 19_AI_QUALITY_OUTCOMES.md,
20_FINOPS_ACCOUNTING_DURABILITY.md, 21_SLO_FORMULAS_AND_INITIAL_TARGETS.md,
22_RELEASE_CONFIG_CORRELATION_CI.md, 23_TERRAFORM_AND_OBSERVABILITY_AS_CODE.md.

The current explicit user scope overrides older roadmap M10 pricing/estimation
and old M3 durable-ledger wording. M3's approved extension assigns durability to
M10. M10 now owns quantities, capture/recovery, completeness, safe usage APIs/UI;
pricing, money, FX, billing, reconciliation, allocation, unit economics, budgets,
forecasting/anomalies remain later milestones. No billing settings prerequisite
or BigQuery client is needed for runtime capture. Do not edit the roadmap merely
to resolve this authorized scope; record the precedence here.

### Existing architecture and reuse

Reuse M3 ModelUsage/ModelUsageObservation, ModelAgent/ModelPurpose, ModelOperation
and ModelAttempt; model_provider's per-instance transport interception for GenAI
1.75.0/ADK1.33.0; existing ObservedModel delegation and remediation decorators.
ModelOperation currently snapshots a ContextVar synchronous sink. ModelAttempt
finish closes once, constructs the observation, invokes M9 observer then guarded
sink. No default sink, durable acknowledgement, start receipt or release/config
accounting fields currently exist. Generic guarded() swallowing is unsuitable as
an accounting acknowledgement. Preserve its diagnostic isolation elsewhere.

Reuse async SQLAlchemy engine/session conventions, UTCDateTime and Alembic's derived
application-table include filter; no ADK private engine/schema. M7 Database has a
bounded pool but lifecycle creation currently depends on projection_enabled.
M10 needs independent FinOps lifecycle/owned bounded pool against the SAME configured
application Cloud SQL DB, independent of M7 projection/SLO/OTel enablement. Reuse
Database class construction where compatible, with explicit accounting pool caps;
never reuse Coordinator slots or Writer.queue for accounting truth.
M9 Writer is also best-effort. Reuse its evaluator registry, Snapshot/Counts and
protected facade, not its publications as completeness denominator. Reuse Service
query deadlines, rate admission and environment checks; verified Principal/require;
existing strict frontend decoder, query hook, ReadState and Settings visual patterns.
Keep canonical chat background execution, session/rewind, governance and SSE intact.

### Complete M3 producer inventory

All following actual provider submissions share model_provider._physical ->
ModelAttempt.observe -> ModelAttempt.finish authoritative scalar projection.
Authority here means provider quantity observation, NEVER evidence/command authority.

| Producer / actual paths | Attribution and applicability | Metadata / semantics |
|---|---|---|
| Five agents: backend/agents/{team_manager,incident_manager,technical_authority_engineer,problem_manager,automated_operations_engineer}/agent.py | coordination/orchestration or specialist_reasoning; user turn when trusted context exists, background otherwise | streaming and nonstream generate_content; one final observation per physical attempt |
| Team Manager presentation variant, direct_read_fast_path.py and read_continuation_execution.py variants | presentation, specialist_reasoning, synthesis | same shared delegate/client; multiple model calls remain separate |
| backend/api/chat_service.py _retry_trusted_presentation_once; team_manager/direct_read_fast_path.py _run_trusted_presentation; source_requirements_completion.py request_source_requirements_declaration | presentation, synthesis, classification | logical business retries generate new operations, separately metered |
| team_manager/governed_knowledge_completion.py; incident_manager/provenance_compliance.py | incident_manager/remediation | bounded governance retries remain existing owners; capture transport only |
| technical_authority_engineer/agent_tool.py model remediation, structured output regeneration and action reselection | remediation, structured_output_repair, action_reselection | validation spans alone are not additional provider usage |
| backend/knowledge_ingestion/gemini_image_interpreter.py | km_image_interpreter/image_interpretation/ingestion; no user run borrowed | image input through generation; usageMetadata if present; no invented image unit counts |
| backend/config/model_warmup.py _run_warmup_request | system/warmup, no user correlation | real nonstream provider attempt included despite system origin |
| backend/tools/knowledge/dense_similarity.py _embed | knowledge_retrieval/embedding_query or embedding_document | SDK may split a batch into physical submissions; retain each separately; cached document vectors create no provider event |
| backend/knowledge/embeddings/service.py embed_text/embed_many | knowledge_embedding/embedding; inferred existing user/background workload | real Vertex request included; empty input, sync pseudo-vector and local fallback vector excluded; failed preceding real attempt remains included |
| Central SDK retries in model_provider.py (httpx/aiohttp), including aiohttp retry inside request_once | each submission attempt has its own identity/ordinal | retry can incur usage regardless of final transport outcome; do not collapse |
| Provider fallback | no separate live alternate-provider entrypoint found in source inventory | local pseudo-vector fallback is not metered; any later real fallback must use same producer seam and a new physical identity |

Generation available fields: promptTokenCount -> input; candidatesTokenCount ->
candidate; cachedContentTokenCount -> cached input; totalTokenCount -> total;
thoughtsTokenCount -> thought; toolUsePromptTokenCount -> tool input (snake aliases
accepted). Output exists only if candidate AND thought are present: candidate+thought.
Streaming snapshots replace, never add. Failed/truncated attempts retain PARTIAL
quantities. Provider omitted metadata is UNKNOWN. Embeddings: statistics.token_count/
tokenCount summed only when every physical response item reports valid counts;
metadata.billable_character_count/billableCharacterCount retained; no output tokens.
No cache-write, cached-output or authoritative item-count field currently exposed by
M3: nullable reserved quantity fields may be planned, never inferred or parsed anew.
Transport errors may yield no usage; this does not prove the request nonbillable.
Current other-provider/non-Gemini injected delegates can execute without physical
interception; no live alternate adapter found. Enabled accounting must detect
unsupported transport and refuse a completeness guarantee, not invent usage.

### Grain, identities, idempotency and quantities

One BASE usage ledger event per potentially metered physical provider attempt.
Run ID (nullable for system), logical_call_id, provider_request_id (server-generated
submission UUID, NOT provider billing request ID), attempt_id, ledger event ID are
separate conceptual fields even though BASE event ID derives from attempt identity.
Existing attempt_id is UUIDv5(NAMESPACE_URL,
logical_call_id:provider:provider_request_id:attempt_ordinal). These identities are
generated before invocation and frozen for accounting replay; UUIDs must not be
regenerated on ingestion retry. Ordinal is per submission, not globally per run.
Use versioned UUIDv5 accounting namespace + environment + attempt_id + operation_type
for usage_event_id; store attempt_id and enforce UNIQUE(environment,attempt_id) for
BASE events plus PK usage_event_id. Do not rely on run ID or trace export availability.
Intent/inbox enforce the same physical identity; corrections have separate IDs and
reference original BASE. Concurrent INSERT ON CONFLICT plus canonical scalar payload
hash comparison implements idempotent success. Same key/different metered payload is
an explicit conflict/gap, never update or second BASE. Retry 100+200=300; replay=300.

NULL is unknown; zero only when provider explicitly reports zero. Keep source and
availability (UNKNOWN/PARTIAL/KNOWN) and semantics_version. Cached input is an input
subset, never additional total. Candidate/thought are output components, not added
again to output; tool-input/total retained independently without unsupported formula.
Preserve M3 conflicts as PARTIAL. Never derive authoritative total from components.
Aggregate each nullable field with known-event and unknown-event counts; all-null
sum remains NULL, and partial sums are labelled observed quantities, not exact totals.
EMBEDDING operation_type keeps input tokens/billable characters, no fabricated output.
Operation type GENERATION versus EMBEDDING is distinct from ModelPurpose attribution.
No estimator, currency, price columns or monetary API values implemented in M10.

Workloads map user_turn -> USER_TURN, warmup -> SYSTEM_WARMUP, ingestion -> INGESTION,
background -> BACKGROUND; invalid/missing attribution becomes explicit UNKNOWN.
Reuse all nine current ModelAgent values plus safe UNKNOWN; retain all 13 current
ModelPurpose values plus UNKNOWN, without collapsing repair/reselection/classification.
Model/provider use safe configured names and registered provider codes; region only
trusted bounded deployment config, no endpoint URL. Environment canonical values are
local/development/staging/production (UI may say dev); populations never combined.
Persist trusted release ID/Git SHA/service revision/config hash from current resource
config; model/prompt config versions remain null if authoritative source absent.
Do not mislabel observability config hash as prompt/model configuration version.
Persist optional run/turn/trace/span; no session ID needed for M10 aggregates, omit.
No user/chat/case/fault identifiers, prompt/body/vector/error strings or arbitrary JSON.

### Accounting durability and bounded failure policy

Selected doc20 architecture: application-owned transactional Cloud SQL usage inbox
and immutable ledger with durable pre-call attempt receipts. A same-DB outbox cannot
survive total DB unavailability by itself; no such claim. No ephemeral Cloud Run disk
spool or M7 best-effort queue is accepted as cross-instance durability.

1. Enabled accounting creates stable attempt identity and durably commits minimal
   ATTEMPT_ADMITTED receipt before physical network invocation. Await bounded async
   SQLAlchemy transaction. Admission write failure means no new provider invocation;
   return safe bounded dependency failure through existing error handling, never
   rerun business work to repair accounting. This is an explicit M10 proposed
   accounting-admission policy, not an existing doc20 guarantee. Optional warmup may
   skip; embedding keeps existing lexical/local fallback; no authorization changes.
2. At finalized M3 observation, project once through strict accounting DTO. Await
   durable inbox insert before reporting capture accepted; attempt settlement and
   inbox commit occur together. Consumer upserts immutable BASE ledger and updates
   durable delivery/checkpoint plus aggregate contributions transactionally.
3. Prefer direct promotion in the same transaction for normal path; recover from
   previously committed inbox when promotion fails. Durable inbox pending != ledger
   persisted. An in-memory wake signal is permitted solely to accelerate scanning.
4. No synchronous SQL or future.result wait on event loop. Add a narrow async capture
   lifecycle around existing transport/finalization boundaries; current synchronous
   sink may observe test data but is never treated as durable ack. Await before
   nonstream return, streaming terminal stop/error and adapter cleanup; handle all
   SDK retries independently. Do not parse response usage again. Persist even when
   OTel/projection/SLO disabled. Install process-owned accounting before warmup and
   use it for ingestion/background tasks; avoid relying on chat-only ContextVar.
5. Proposed explicit configurable bounds: total attempt admission/capture budget
   2s each, maximum 3 accounting writes within budget with jitter; bounded concurrent
   writes 8, admission wait 250ms, recovery batch100, lease30s, recovery pass2s,
   shutdown3s. All effective read-only; cap by remaining business deadline and reserve
   cleanup time. Measure; revise defaults from evidence, never silently exceed them.
   No unbounded queue: backpressure at admission, pending durable rows remain in DB.

Crash boundaries: before intent commit no provider call; after commit/before dispatch
receipt conservatively denotes an uncertain attempt (may not have executed); during
provider or after response/before inbox commit receipt becomes unresolved accounting
debt, quantities potentially irrecoverable. After inbox commit full safe quantities
survive restart; after ledger commit/before ack replay is idempotent. Never invent
missing quantities or rerun provider to recover them. Pre-call receipt is not proof
of provider execution; keep these uncertainties separately visible and prevent a
HEALTHY claim for affected windows. Exact externally billable usage during this
nontransactional provider/DB interval cannot be guaranteed; M11 billing may later
resolve debt, but M10 must expose it now.

If final capture cannot commit, existing provider/business result is preserved,
COST_LEDGER_PERSIST_FAILED emitted safely, existing durable receipt remains unsettled,
new model admissions degrade/stop while store inaccessible. Process-local failure
health is only supplementary. During total DB outage APIs return unavailable, not
cached healthy. Recovery marks overdue receipts OBSERVATION_MISSING/EXECUTION_UNKNOWN;
receipt-only debt cannot recover exact quantity. No true pending payload claim if
payload never committed. Collision uses safe conflict reason code, no payload log.

Recovery workers lease inbox rows using PostgreSQL FOR UPDATE SKIP LOCKED, bounded
batch/lease/backoff; transactionally insert ledger, verify hash, mark delivered and
apply exactly-once aggregates. Multiple workers and expired leases converge. Exhausted
records remain durably visible for later bounded retry/operator investigation;
no deletion or automatic provider/tool/command/user-response replay. Shutdown timeout
leaves durable pending work recoverable. Tests inject cancellation at every boundary.

### Planned dedicated schema / migration / corrections

New backend/observability/finops/models.py owns separate FinOpsBase; same application
DB, logically independent tables. Reuse UTCDateTime; import metadata into Alembic
filter explicitly. Additive revision path
alembic/versions/c10a8f6e2d41_finops_runtime_usage.py follows b37e90a14c62;
verify revision uniqueness and actual head immediately before implementation.

| Table | Planned columns / integrity |
|---|---|
| finops_runtime_attempt | environment+attempt_id PK; logical/submission IDs, ordinal, provider/model/type/attribution, UTC admitted/start/deadline/settlement, bounded applicability/state, durable gap reason; no quantities guessed |
| ai_usage_ledger | usage_event_id PK; environment/physical IDs/type; immutable quantities listed above (signed adjustment only for correction); source/availability/semantics/accounting schema versions; timestamps observed/start/end/created; safe correlation/release/config; record_kind/original_event_id/correction reason/process; BASE uniqueness and nonnegative int64 checks |
| finops_usage_inbox | immutable allowlisted scalar accounting payload/hash, unique BASE identity; mutable delivery state, attempts, next_attempt_at, lease owner/expiry, bounded failure reason, persisted/delivered times; no raw provider object |
| finops_usage_bucket | environment/time bucket/registered safe dimension tuple, quantities with known/unknown counters and counts; transactional idempotent event contribution; no identifiers in dimension keys |
| finops_accounting_source | per environment coverage start/checkpoint/last observation/last persistence/freshness; durable gap/uncertainty state, recovery generation; no healthy reset on restart |

Initial indexes: ledger(environment,observed_at,usage_event_id), attempts(environment,
state,deadline_at), inbox(state,next_attempt_at,lease_expires_at), buckets(environment,
bucket_at). Add provider/model filtering composite only from measured query needs.
No speculative reconciliation-status index or partitioning now; benchmark PostgreSQL
before optional monthly partitions (unique identity enforcement must remain global).
No remote autogenerate, create_all on startup or shared schema application.

Doc20 requires explicit correction/reversal and late provider reports. Include minimal
immutable quantity-adjustment support in M10: original reference, bounded reason,
trusted process identity, UTC timestamp, exactly-once correction ID; original BASE
never updated. Unknown-to-known late usage uses referenced supplement semantics
without interpreting null as zero; repeated late observation must suppress duplicates.
No public correction editor, estimated token generation, billing reconciliation or
price adjustment yet. Correction events never increment physical attempt denominator.

Retention target confirmed doc00/doc09: runtime usage ledger/billing analytics24months,
enterprise may extend. Record calendar-month cutoff semantics, not invented730days.
No destructive pruning in M10; preserve originals, linked corrections, unfinished
inbox, unresolved attempts and future reconciliation holds. Later approved retention
must expire relationships safely; never cascade from session/conversation deletion.
Schema/quantity semantics version1 explicit on every event; optional monetary schemas
later migrate independently. No arbitrary provider-specific JSON extension required.

### Completeness source, metrics and M9 integration

Use canonical doc21 numerator exactly: physical eligible attempts with exactly one
persisted BASE accounting event / observed potentially billable provider operations.
Denied-before-provider, synthetic callback/local fake without provider submission,
cache hit/empty input/pseudo-vector excluded. Real failure/timeout/cancelled attempt
with possible billing remains eligible even with UNKNOWN quantities. Exclusion for
explicit nonbillable attempt requires server-owned proof, never status alone.
Duplicates/corrections add neither denominator nor numerator. Known observation
population is durably settled with inbox; unresolved pre-call receipts provide
additional uncertainty/debt. They cannot be silently dropped to report100%. Do not
use traces, ordinary model request metrics or M9 lossy receipt counts as denominator.

Pending inbox = eligible missing ledger until promotion, counts bad after settlement
watermark, never good. Late promotion moves original event-time bad->good via
transactional version guard, not append a second denominator. Gap/uncertain receipt
contributes unknown coverage and capture_complete=false so incomplete population
cannot show healthy even if measured ratio is100%. Separate quantity metadata coverage
KNOWN/PARTIAL/UNKNOWN by provider/type expectation (do not demand chat output for
embeddings). Unknown quantities can still be a good durable BASE capture.

FinOps source provides M9 Snapshot/Counts from durable buckets, coverage_start,
updated_at/watermark and capture_complete; finite half-open UTC window semantics
remain M9-owned. Composite source dispatches cost only to accounting, all others to
existing rollups. Remove hardcoded M10 suppression ONLY for activated durable source;
disabled source remains truthful DEFINED_NOT_EVALUATED. Registry remains formula
owner; source-independent definition does not imply deployed accounting. 100%
architectural target keeps budget/burn NULL; canonical operational threshold
<99.99% over24h receives real source via M9 alert registry, no divide by zero.
Expose observed ratio with freshness/coverage qualification, never healthy at zero,
stale source, pending uncertainty or truncated window. Source clock advances only
with successful durable coverage checks, not UI polling or provider-event count.

Bounded pipeline counters: observations_received, ledger_accepted, duplicate_suppressed,
persist_failed, replayed, retry_exhausted, accounting_gap, metadata_incomplete,
identity_conflict. Gauges pending/oldest_pending_age/source_age; capture/insert duration
seconds histograms. Register finite instrument names and exact labels environment,
provider (finite), operation_type, bounded status/reason only. No model IDs unless
mapped to existing finite registry, no event/run/trace/lease/user labels or exemplars.
Operational metrics are not financial totals/source of accounting truth. Extend SDK
and BOTH Collector validation configs together; machine-test poison labels/payloads.

### Protected APIs and M8 UI impact

Plan GET /api/observability/finops/runtime-usage, /ledger-health,
/ledger-completeness in existing observability router using require(FINANCIAL_DATA),
verified principal, role ceilings, explicit environment authorization and bounded
query facade. Existing FinOps is sole financial capability ceiling; Admin and SRE
DO NOT automatically have FINANCIAL_DATA. Health safe technical subset may separately
allow TECHNICAL_DIAGNOSTICS; no role union broadening or FinOps diagnostic access.
General M9 SLO view can expose only content-free completeness health under existing
operational capability; FinOps endpoint exposes same backend evaluator without
requiring operational_metadata. Test existing role distinctions, no frontend grants.

Aggregate usage request UTC [start,end), max31days, defaults24h, hourly/day bucket,
one approved grouping among provider/model/agent/workload/purpose/type; max100rows,
opaque scope-bound pagination max1000groups; safe filters and query budget2s,
concurrency4/rate60perminute reuse. Runtime backend bucket aggregates avoid raw ledger
scan per UI refresh; prohibit arbitrary dimensions/query/SQL. Responses include
quantity-known/unknown counts, attempts, exclusions, coverage and schema version;
no business correlation IDs or content. Health last observation/persistence/checkpoint,
pending/oldest age, gaps/exhaustion/conflict/freshness. Completeness delegates to M9.
Raw event endpoint is unnecessary and not planned. No monetary fields or false zeros.

UI-impact rule: (1) creates usage/health facts for Settings→Observability & FinOps;
(2) dedicated safe typed contracts needed; (3) replace current FinOpsPanel runtime
placeholder within M10, preserve later unavailable cards; (4) backend capabilities
apply, include denial/loading/error/no-data/stale/partial states; (5) settings are
view-only effective policy, no mutation/replay buttons. Existing layout, typography,
query cancellation/revocation clearing preserved. No browser financial/SLO formulas;
all quantities and completeness backend-owned. Normal chat receives no financial
or diagnostic data. Data & Retention may describe24month target as policy, never
claim active pruning; Access can explain unchanged financial capability gate.

### Ordered implementation plan and exact expected paths

When separately authorized, implement only M10 in order:
1. contracts/producer admission and final capture design tests;
2. FinOps models/additive migration/transactional repository;
3. durable admission/inbox/ledger/recovery and failure tests;
4. narrow M3 async integration for all producer paths including warmup;
5. transactional aggregate/completeness adapter and M9 activation;
6. protected APIs/typed FinOps panel and end-to-end tests;
7. full local validation/performance/diff review and individually evaluate gates.
No milestone progression until all gates pass.

New planned paths:
- backend/observability/finops/__init__.py
- backend/observability/finops/contracts.py
- backend/observability/finops/models.py
- backend/observability/finops/repository.py
- backend/observability/finops/usage_ledger.py
- backend/observability/finops/recovery.py
- backend/observability/finops/metrics.py
- backend/observability/finops/api_models.py
- backend/observability/finops/service.py
- backend/observability/finops/slo_source.py
- alembic/versions/c10a8f6e2d41_finops_runtime_usage.py
- backend/tests/test_finops_runtime_contract.py
- backend/tests/test_finops_runtime_ledger.py
- backend/tests/test_finops_runtime_recovery.py
- backend/tests/test_finops_runtime_postgres.py
- backend/tests/test_finops_runtime_api.py
- backend/tests/test_finops_runtime_metrics.py
- backend/tests/test_finops_runtime_integration.py
- backend/tests/test_finops_runtime_performance.py
- src/api/finopsTypes.ts
- src/api/finopsContract.ts
- src/api/finops.ts
- src/api/finops.test.ts
- src/components/shell/settings/observability/FinOpsPanel.test.tsx

Existing planned modifications:
- .agent/OBSERVABILITY_EXECUTION.md
- backend/observability/{model_context,model_usage,model_instrumentation,model_provider,model_adapter,config,runtime,schemas,slo_sources,slo_evaluator,slo_contract,slo_alerts,slo_iac}.py
- backend/api/{app,observability_routes}.py
- backend/config/settings.py
- alembic/env.py
- backend/tests/{test_observability_migrations,test_observability_model_coverage,test_observability_model_provider,test_observability_model_instrumentation,test_observability_slo_evaluator,test_observability_slo_api,test_observability_slo_iac,test_api_security_contract,test_observability_collector}.py
- src/components/shell/settings/observability/{FinOpsPanel,AccessPanel,DataRetentionPanel}.tsx (last two only truthful policy/capability text if needed)
- src/components/shell/settings/observability/Panels.test.tsx and src/components/shell/settings/observability/contract.test.ts
- src/api/{sloContract,sloTypes}.ts (only if strict decoder requires new source states)
- infra/observability/collector/{config,config.local}.yaml
- infra/observability/monitoring/{slo_manifest,alert_policies}.json (generated canonical artifact updates only)
- docs/Telemetry/runbooks/cost_ledger_completeness.md
- backend/observability/README.md
No business callsite refactor planned; all producers reuse centralized M3 ownership.

### Validation commands and required evidence (future, NOT RUN this pass)

Use existing /private/tmp/slopanoc-m7-test-guard/run_tests.py and network guard after
re-auditing isolation; remove cloud credentials/proxies, disable real models/warmup,
use explicit disposable DB. Never invoke app/secret resolver with inherited cloud
config. Planned commands:
- .venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests/test_finops_runtime_contract.py backend/tests/test_finops_runtime_ledger.py backend/tests/test_finops_runtime_recovery.py backend/tests/test_finops_runtime_postgres.py backend/tests/test_finops_runtime_api.py backend/tests/test_finops_runtime_metrics.py backend/tests/test_finops_runtime_integration.py backend/tests/test_finops_runtime_performance.py
- same runner for every backend/tests/test_observability_*.py (explicit argv glob expansion), then backend/tests --ignore=backend/tests/manual
- npm test -- --run; npm run build
- isolated migration test suite includes disposable SQLite upgrade/downgrade and
  PostgreSQL offline SQL without configured remote URL; real PostgreSQL roundtrip,
  constraints/concurrency/lease/crash transaction tests when available
- native Collector validate for both configs with production exporter never started;
  local SDK→Collector→loopback sentinel/cardinality test
- generated M9 artifact drift and alert dry-run tests; git diff --check

No postgres/initdb command found on PATH; docker exists but daemon/image availability
not checked and no container started. Future use genuine disposable loopback Postgres
only after verifying no remote Docker context, no shared mounts/network/credentials.
Missing Postgres evidence is a named validation limitation, never SQLite equivalence.
Concurrency blocking test remains required; production PostgreSQL confidence must
remain unproven until isolated genuine engine evidence passes.

Test scenarios: single authoritative attempt;20concurrent identical inserts;100/200
retry plus duplicates/late repeats;100/150/200cumulative stream yields200; nulllimits/
explicitzero/partial/conflicting tokens; failed/truncated/successful unknown usage;
embeddings/batch splitting/absent stats/cache/pseudo-vector; warmup and image/system;
all existing agent variants/attributions; FinOps independently on with OTel off;
unsupported SDK adapter degrades/admission fails before unobserved provider request;
DB unavailable before/after provider with exact business/provider invocation counts;
crashes before dispatch/after usage/before inbox/after inbox/after ledger; multiple
workers, expired leases, replay exhaustion, collisions, late quantity supplements;
privacy sentinels across inbox/ledger/log/API/metrics/UI; verified principal denial,
all6roles/capability/environment boundaries; migration unrelated schema preserved;
M9 100/100,99/100,pending,duplicate,unknown quantities,stale,zero,partial28day/24h
windows; UI future cards/no money/revocation/unknown counts; metric poison tests.

Measure capture separately from provider latency: median/p95/max admission, inbox,
insert, duplicate, aggregate/health query and recovery100batch; realistic100000event
synthetic dataset; concurrency/backpressure and outage latency; quantify total turn
and event-loop overhead against doc01 <3% CPU/<10ms instrumentation target (durable
network writes separately reported). No production capacity claim from SQLite.

### Individually planned exit criteria

Every implementation gate below PENDING; planning does not mark them PASS.

| ID | Blocking acceptance criterion / evidence |
|---|---|
| C01 | complete producer inventory and AST no-bypass gate |
| C02 | one BASE grain, stable physical identities and server-only attribution |
| C03 | DB uniqueness, duplicate hash conflict and20way concurrency proof |
| C04 | separate retry usage100+200=300 despite replays |
| C05 | cumulative streaming final200 not450 and truncated semantics |
| C06 | unknown versuszero preserved end-to-end and aggregate coverage |
| C07 | embedding quantities/no output/vector/fallback event inflation |
| C08 | warmup/image/background captured outside user correlation |
| C09 | immutable BASE and idempotent referenced corrections/late reports |
| C10 | durable inbox/admission stronger than M7, OTel/projection independent |
| C11 | crash receipt debt honest; post-inbox recovery survives restart |
| C12 | concurrent workers/replay/lease recovery converge exactly once |
| C13 | bounded failure/backpressure; no provider/tool/command/response replay |
| C14 | no raw payload/privacy sentinel across every accounting sink |
| C15 | bounded SDK/Collector accounting instruments, no ID labels |
| C16 | exact durable numerator/denominator and unresolved coverage |
| C17 | M9 source activation,100% no finite burn;24h99.99% alert dry-run |
| C18 | zero/stale/partial/gap cannot show healthy; metadata coverage separate |
| C19 | protected bounded aggregate APIs, allrole/env gates default-deny |
| C20 | M8 FinOps usage/health only, existing visual/query patterns |
| C21 | no monetary/pricing/billing/M11 functionality |
| C22 | additive isolated migrations/index/constraints preserve unrelated schema |
| C23 | real PostgreSQL concurrency/recovery semantics or explicitly blocked confidence gate |
| C24 |24month retention/version policy with no destructive/reconciliation-unsafe purge |
| C25 | measured overhead/high volume/backpressure/performance bounds |
| C26 | imports/full applicable regressions/frontend build pass |
| C27 | repository diff/untracked/secret hygiene and execution record evidence |
| C28 | no remote/cloud/shared DB mutation; pending activation explicit |

### Planning validation, risks and cloud status

Planning review confirms source inventory/ownership, identity formula, quantity
semantics, durable boundary and RBAC mismatch with assumed technical/Admin financial
access. Important risks: synchronous-to-async transport integration; pre-call receipt
uncertainty; same DB cannot save response payload during total outage; SDK compatibility
must not silently bypass accounting; potential durable write latency; no real Postgres
available on PATH; no current production verified identity; future reconciliation and
retention holds not implemented. These are explicit implementation/test work, not
permission to weaken accounting/governance. Ready To Implement M10: YES (local scope;
all implementation gates still pending; separate instruction required).

Cloud deployment status = NOT EXECUTED — USER APPROVAL REQUIRED.
No cloud change needed for planning/local implementation. Pending eventual actions:
apply additive c10a8f6e2d41 to exact approved shared Cloud SQL environment after verified
predecessor revisions; deploy backend/frontend/Collector accounting configuration;
activate environment-owned FinOps policy/verified identity and M9 coverage alert
artifacts if separately approved. Exact project/account/environment not selected.
No BigQuery/billing export/IAM/secrets changes required by M10; never infer permission.
Each actual proposal needs AGENTS.md all10operation/impact/rollback fields, with
migration lock/index/rollback risks and target revision. Additive downgrade drops
accounting data and is not a production rollback; prefer disable activation and
compatible software rollback, preserve ledger. No operation submitted for approval.
Local and live validation are separate; cloud-dependent claims stay NOT LIVE-VALIDATED.

Planning tests/builds NOT RUN (no implementation). Initial audit shell used absent
python executable; corrected to .venv/bin/python for local file hash snapshot.
No implementation failure, no remote action, no automatic approval rejection.
**Ready for Next Milestone: NO. M11 BLOCKED. STOP after planning.**

Planning final hygiene: git status shows only this tracked execution-record change;
git diff --check PASS; no new repository files. Hash audit of939files also detected
logs/backend-20261008-210639.log changing during this read-only source audit. No log
write, app startup or test was invoked by this pass; treat this as concurrent local
runtime activity, preserve it untouched and do not claim all non-plan hashes stable.
Initial strict baseline assertion therefore failed; scoped tracked diff and added-file
review establish this pass's planned edit. No log contents exported or inspected.
Accounting safe failure logging also needs existing backend/observability/logging.py,
redaction.py and attributes.py allowlists extended alongside schemas.py/metrics.py;
these are additional exact expected modification paths. Register typed event codes
without exception strings or payloads, reuse doc18 JSON/error correlation conventions.

## M10 local implementation authorization — 2026-10-08

User explicitly authorizes M10 only with attached accounting-state clarifications.
All implementation gates PENDING; M11 blocked. ADMITTED never enters denominator;
STARTED means physical transport dispatch boundary entered (not provider billing
confirmation). Commit admission and dispatch state in separate bounded transactions,
never hold a transaction across provider I/O. Started-but-uncaptured obligations
survive via durable receipt, even when final failure update cannot reach the DB.
Existing results survive final capture failures. Separate quantity coverage required.
Local code/migration/isolated tests authorized; no external mutation authorized.


## M10 — implementation and local validation evidence — 2026-10-08

Status: IMPLEMENTED AND LOCALLY VALIDATED. All blocking local gates PASS.
This supersedes historical planning-only status above. M11 NOT IMPLEMENTED BY DESIGN.
Ready for Next Milestone: YES for local progression only; STOP, no M11 authorization.
Production accounting readiness is NOT claimed.

### Implementation performed and ownership

Read the approved M10 plan and accounting-state clarification, current M3 physical
provider/usage path, M9 evaluator/source/pipeline, existing M7 verified principal/
capability/environment/query guards and M8 Settings patterns. Authoritative scope:
docs/Telemetry/README.md, 00_MASTER_BUILD_CONTRACT.md, 12_IMPLEMENTATION_ROADMAP.md,
14_CODEX_EXECUTION_GUIDE.md, 15_DEFINITION_OF_DONE.md, 13_TEST_VALIDATION_ACCEPTANCE.md,
01_TARGET_ARCHITECTURE.md, 07_FINOPS_ARCHITECTURE.md, 09_SECURITY_PRIVACY_RETENTION_SAMPLING.md,
18_STRUCTURED_LOGGING_STANDARD.md, 20_FINOPS_ACCOUNTING_DURABILITY.md and the
cost_ledger_completeness runbook. Existing detailed plan/inventory above remains
engineering memory; actual paths/evidence below are authoritative for implementation.

M3 remains the only scalar provider metadata extractor. M10 consumes the finalized
validated ModelUsageObservation, never logs, intermediate stream snapshots or raw
response objects. The physical SDK transport wrapper invokes awaited accounting
prepare before fn(); unsupported SDK/client shape fails closed while accounting is
active. ModelOperation captures the process-owned accounting service independently
of tracing/sampling/projection flags. No business callsite ownership was replaced.
AST producer/no-bypass inventory, M3 variants, direct embeddings and image adapters
remain in the focused regression gate. Tests cover actual pinned ADK/GenAI transport
with fake HTTP provider and separate DB reads before invocation.

Canonical closed states: ADMITTED, STARTED, PROVIDER_FINISHED, USAGE_CAPTURED,
NOT_STARTED, FINAL_CAPTURE_PENDING, FINAL_CAPTURE_FAILED, OUTCOME_UNKNOWN, CONFLICT.
Admission commits before STARTED, which commits before transport invocation. No DB
transaction spans provider I/O. STARTED is a durable conservative dispatch-entry
obligation, not evidence that a remote provider received or billed the request.
The unavoidable commit-to-network crash window remains durable outcome uncertainty.
Expired never-started ADMITTED closes as NOT_STARTED, excluded from denominator.

Admission+start has a 2s total default deadline, 8 concurrent writes, .25s semaphore
wait, <=3 DB attempts, short bounded backoff and at most .25s cleanup after failure.
Failure means zero physical provider calls, no BASE, no started population; existing
safe connector error carries accounting cause, not provider failure. Final accounting
has a separate 2s total budget plus <=.25s failure annotation. It cannot replay a
provider or change a successful result merely to recover accounting. Cancellation
retains its existing semantics and durable started receipt. If all DB writes fail
after the provider, the earlier STARTED receipt still records the unresolved debt;
the final quantities may be irrecoverably unknown. Recovery never fabricates them.

### Schema, identity, replay and corrections

Separate FinOpsBase contains five additive tables: finops_runtime_attempt,
finops_usage_inbox, ai_usage_ledger, finops_usage_bucket, finops_accounting_source.
Attempt state/delivery state may transition; BASE and referenced ADJUSTMENT events
are append-only through repository APIs. Strict allowlisted payloads persist scalar
M3 quantities, bounded provider/model/agent/purpose/workload, safe correlation,
release/revision/Git SHA/region/config hash, UTC times, usage source/availability and
schema/quantity-semantics/idempotency versions (all v1). No prompts, messages,
commands, SQL, URLs, tool data, credentials, response bodies or vectors.

Event identity is UUIDv5 over fixed v1 namespace + environment + physical attempt
UUID + operation_type, generated from stable server-owned identity. Logical call,
provider submission UUID and retry number are preserved in safe identity. Event PK
and partial unique environment+attempt BASE index enforce DB uniqueness. Hash
conflicts preserve original and mark bounded CONFLICT/debt. Twenty concurrent local
SQLite duplicates converge to one BASE and one aggregate contribution. No claim
of PostgreSQL-engine concurrency validation is made.

Capture durably commits strict safe payload to inbox; materialization atomically
inserts immutable ledger, adjusts compact minute bucket population/quantities and
marks delivery. Locks follow attempt -> inbox, are short-lived transaction worker
leases, SKIP LOCKED on PostgreSQL and released on crash. Background recovery only
replays accounting materialization: <=100 items/pass, <=2s process loop pass,
<=5 per-item failure attempts with bounded backoff; exhausted inbox stays durable
and visible. Background cadence30s; shutdown drain total3s plus DB-close <=3s.
Inbox retry never reruns providers, agents, tools, commands or responses.

Referenced signed corrections include original, reason, trusted process, timestamp
and unique correction ID. Late provider supplements can fill unknown quantities
using immutable adjustments, improving per-component known counts and quantity
coverage without changing original NULL or adding a BASE/denominator. Replay is
idempotent. Known quantities require explicit correction/reversal, not a silent
second supplement. No pricing/estimation/reconciliation engine was introduced.
24month retention policy is validated and reported read-only; no destructive
retention execution/scheduler activated, preserving future reconciliation history.

### Crash, semantic and safety evidence

A: before admission no execution/population. B: committed ADMITTED not dispatched
stays excluded and expires NOT_STARTED. C: committed STARTED then missing capture
survives repository/DB reopen as durable unresolved obligation; checkpoint marks
OUTCOME_UNKNOWN, provider count stays one. D: committed inbox reopens/replays exactly
once, including actual child os._exit(73) after commit. E: committed materialization
then acknowledgement loss/replay leaves one immutable BASE and unchanged totals.
Window E is deterministic crash-boundary simulation, not a production process-kill
validation. Local restart/crash tests are not a live Cloud Run/Cloud SQL guarantee.

Provider retries100+200 aggregate300 and replay changes nothing. Cumulative streaming
100/150/200 produces a single final200 BASE. HTTP retry transport tests create two
physical attempts; unavailable failed-attempt usage remains NULL. Unknown and
explicit provider0 remain distinct in ledger, safe DTO and UI. Embedding captures
M3 authoritative input7 with operation_type EMBEDDING, no invented output or vectors.
Warmup is SYSTEM_WARMUP with no user-run correlation. Additional transport tests
cover ingestion image interpretation and background repair/remediation, no estimates;
M3's existing nonturn default BACKGROUND is preserved. Restricted-field allowlist
negative tests cover prompts/responses/Teams/commands/tool args/SQL/URLs/credentials/
vectors/chain-of-thought at payload and nested identity boundaries. Existing M3
sentinels and actual Collector poisoned IDs are excluded from accounting sinks.

### M9 source, API, Settings and RBAC

M10 minute buckets feed M9 Snapshot(source=DURABLE_ACCOUNTING); M9 alone evaluates.
Eligible=durable STARTED obligations, good=durably materialized BASE; uncaptured
started obligations are bad. ADMITTED/NOT_STARTED do not enter either population.
Unknown quantities can satisfy BASE capture, while quantity_known/captured and
per-component known/unknown stay separate. Tests prove100/100=100%,99/100=99%,
90starts from100admitted/90captured=100%,100BASE with5unknown=100% capture/95%
quantity coverage. Zero eligible is INSUFFICIENT_DATA; source>300s old is STALE_DATA;
unavailable/conflicts/partial history cannot masquerade as healthy. The architectural
100% target has no finite error budget/burn. Separate canonical24h operational
coverage alert triggers below99.99%, only with fresh nonzero usable data. Artifact
is disabled/no notification channels and remains undeployed; M9 formula ownership
and existing evaluator/alert pipeline are reused.

Accounting pipeline counters: admissions_attempted, admissions_failed,
provider_started, observations, inbox_accepted, ledger_persisted, duplicates,
conflicts, replayed, persistence_failures, metadata_missing. Gauges pending/debt;
histograms admission_duration/capture_duration in seconds. SDK views/safety and
both Collector configs use finite names/labels (environment, operation, status,
window); no accounting/run/trace/session/user IDs or exemplars. Native pinned
Collector0.160 validation and actual SDK->Collector->isolated sink pass. JSON safe
failure logging reuses DEGRADED/COST_LEDGER_PERSIST_FAILED without exception text.

Three financial capability GET APIs: /api/observability/finops/runtime-usage,
/ledger-health, /ledger-completeness. No raw ledger or mutation endpoint. Existing
verified principal, FINANCIAL_DATA capability, environment guard, HMAC scoped
cursor, shared read semaphore/rate limits and2s query cap remain server-owned.
Usage range<=31days, minute-aligned bounds, page<=100, aggregate scan<=1000groups.
Tests cover all roles, unauthenticated/unverified-production, denied financial
capability, allowed FinOps, invalid range/cursor/environment and safe DTOs. Existing
operational SLO and financial completeness endpoints share one M9/M10 source.

UI impact evaluation: creates Settings information (usage/coverage/health/freshness)
required now; supplies only aggregated safe API contracts; M8 FinOps panel update
belongs to M10; financial RBAC applies server-side; retention/config is view-only.
Existing SLO panel needs no new formula or separate data path. React formats backend
values, uses shared cancellable/stale/access read-state patterns and fixed page
ranges, preserves layout and M11–M14 future cards. No money/currency/pricing or
chat/governance changes. No Terraform/UI runtime configuration mutation path.

### Actual files changed

- `.agent/OBSERVABILITY_EXECUTION.md`
- `alembic/env.py`
- `alembic/versions/c10a8f6e2d41_finops_runtime_usage.py`
- `backend/api/app.py`
- `backend/api/observability_routes.py`
- `backend/observability/README.md`
- `backend/observability/config.py`
- `backend/observability/finops/__init__.py`
- `backend/observability/finops/api_models.py`
- `backend/observability/finops/contracts.py`
- `backend/observability/finops/metrics.py`
- `backend/observability/finops/models.py`
- `backend/observability/finops/recovery.py`
- `backend/observability/finops/repository.py`
- `backend/observability/finops/service.py`
- `backend/observability/finops/slo_source.py`
- `backend/observability/finops/usage_ledger.py`
- `backend/observability/logging.py`
- `backend/observability/metrics.py`
- `backend/observability/model_adapter.py`
- `backend/observability/model_instrumentation.py`
- `backend/observability/model_provider.py`
- `backend/observability/runtime.py`
- `backend/observability/slo_alerts.py`
- `backend/observability/slo_api_models.py`
- `backend/observability/slo_contract.py`
- `backend/observability/slo_evaluator.py`
- `backend/observability/slo_iac.py`
- `backend/observability/slo_metrics.py`
- `backend/observability/slo_sources.py`
- `backend/tests/test_api_security_contract.py`
- `backend/tests/test_finops_runtime_api.py`
- `backend/tests/test_finops_runtime_integration.py`
- `backend/tests/test_finops_runtime_ledger.py`
- `backend/tests/test_finops_runtime_metrics.py`
- `backend/tests/test_finops_runtime_migrations.py`
- `backend/tests/test_finops_runtime_performance.py`
- `backend/tests/test_finops_runtime_recovery.py`
- `backend/tests/test_observability_collector.py`
- `backend/tests/test_observability_migrations.py`
- `docs/Telemetry/runbooks/cost_ledger_completeness.md`
- `infra/observability/collector/config.local.yaml`
- `infra/observability/collector/config.yaml`
- `infra/observability/monitoring/alert_policies.json`
- `src/api/finops.test-support.ts`
- `src/api/finops.test.ts`
- `src/api/finops.ts`
- `src/components/shell/settings/observability/FinOpsPanel.test.tsx`
- `src/components/shell/settings/observability/FinOpsPanel.tsx`
- `src/components/shell/settings/observability/ObservabilitySettings.test.tsx`
- `src/components/shell/settings/observability/Panels.test.tsx`

### Validation commands and actual results

All backend tests used /private/tmp/slopanoc-m7-test-guard/run_tests.py: inherited
cloud/shared DB/proxy variables removed, isolated SQLite/test doubles, external
network blocked; Collector ports are loopback only. Approved sandbox escalation
was solely for these authorized local loopback tests. No approval rejection.

- `.venv/bin/python /private/tmp/slopanoc-m7-test-guard/run_tests.py backend/tests --ignore=backend/tests/manual`: **5329 PASS,12 SKIP,687 warnings,201.21s**. Existing optional skips; no new blocking test waived.
- Focused `backend/tests/test_observability_*.py backend/tests/test_finops_runtime_*.py --ignore=backend/tests/test_finops_runtime_performance.py`: **1000 PASS,8 warnings,119.00s**. Includes M0–M10, M3/provider, M6, M7/security, M9 and native/actual Collector integration.
- Supplemental final ledger/integration run after adding10 strict payload negatives and3 nonturn attribution cases: **34 PASS**. These final test-only additions were validated independently after broad suite collection; production implementation was already final for that suite.
- Earlier final focused ledger/API/recovery/integration: **46 PASS**, including shared financial/operational source, abrupt exit/reopen, late supplement and cancellation/failure bounds.
- Final standalone100000-row performance gate: **1 PASS,54.67s**, actual report below.
- `npm test -- --run`: **55files,932 PASS,11.69s**.
- `npm run build`: **PASS** (TypeScript `tsc` plus Vite production build;2053modules,3.54s).
- Both Collector configs native validate and all seven actual local pipeline sample classes PASS in focused/full tests; accounting duration unit and poisoned ID validated.
- Additive upgrade/downgrade/index/FK/check parity and offline PostgreSQL DDL PASS; full existing migration graph/regressions PASS. No startup automatic DDL.
- `git status --short`, tracked diff/stat, new file inventory and `git diff --check`: scoped M10 only, no secrets/data exports/local DB contents in changes; PASS.

Failures and repairs: initial test exporter API mismatch corrected to records;
frontend fixture import/name corrected; existing FinOps heading retained for Settings
navigation; local Collector environment variables supplied. Initial unprivileged
loopback bind failed; authorized local escalation ran Collector tests. Actual
Collector revealed duration unit reset to1; both configs corrected to seconds and
native/actual pipelines rerun. TypeScript test fixture's null-only inference fixed
with nullable-number type. Final review standardized transaction lock ordering,
bounded prepare/final-capture totals and failure annotation, preserved cancellation,
added persisted usage source/availability, and corrected late-supplement aggregate
known/coverage handling. New remediation test initially expected UNKNOWN; actual M3
nonturn default is BACKGROUND, corrected test to preserve owner semantics. All final
blocking validations pass; no architecture criterion silently waived.

### Local performance evidence

Isolated SQLite, shared local machine also running regressions, not production
PostgreSQL/Cloud SQL capacity. Hot-path sample count30, read sample count5;
recovery batch measurement is1 observation (median/p95 both that observation).
100000-row bulk fixture is index/read-load setup, NOT runtime ingestion throughput.
Bounded fixture batch1000,100030 persisted BASE rows before100pending replay items.
Reported raw RSS peak growth is macOS bytes; this measures fixture memory growth,
not production stability or a CPU overhead/capacity guarantee.

| Operation | median ms | p95 ms | n |
|---|---:|---:|---:|
| admission | 1.745 | 2.345 | 30 |
| capture | 2.502 | 4.224 | 30 |
| completeness_100000 | 151.497 | 153.644 | 5 |
| duplicate_capture | 2.126 | 2.609 | 30 |
| duplicate_materialization | 1.014 | 1.249 | 30 |
| health_100000 | 9.866 | 77.985 | 5 |
| materialization | 4.762 | 8.482 | 30 |
| recovery_100_batch | 501.814 | 501.814 | 1 |
| start | 2.737 | 4.156 | 30 |
| usage_100000 | 47.004 | 54.327 | 5 |

Bulk fixture100000 rows: 50.298s; RSS peak growth 74579968bytes (~71.1MiB), bounded1000rows/batch. Failed admission/start tests prove0 provider calls; timeout/cancellation/backpressure preserve durable obligation. Durability is not weakened to reduce latency.

### Exit criteria — individually evaluated

User-approved section86 local blocking criteria:

| Criterion | Result |
|---|---|
| Accounting attempt state machine is explicit | PASS |
| ADMITTED is distinct from provider STARTED | PASS |
| Provider cannot start before durable admission | PASS |
| Failed admission results in zero provider calls | PASS |
| Started-but-uncaptured attempts leave durable debt | PASS |
| Accounting debt survives restart | PASS |
| Committed inbox survives restart | PASS |
| Immutable BASE ledger exists | PASS |
| DB-enforced idempotency works | PASS |
| Concurrent duplicates produce one event | PASS |
| Conflicting duplicates are visible and preserve the original | PASS |
| Provider retries count separately | PASS |
| Accounting replay never invokes the provider | PASS |
| Streaming cumulative usage is not double-counted | PASS |
| Unknown remains distinct from zero | PASS |
| Embeddings are represented correctly | PASS |
| Warmup/system usage is retained | PASS |
| Bounded attribution is preserved | PASS |
| Privacy passes | PASS |
| Ledger completeness uses accounting-required starts, not admissions | PASS |
| Quantity coverage remains separate | PASS |
| M9 completeness evaluates from M10 data | PASS |
| M8 FinOps shows runtime usage and health only | PASS |
| No monetary calculation exists | PASS |
| Local migration passes | PASS |
| Failure/restart tests pass | PASS |
| Applicable regressions pass | PASS |
| M11 remains unimplemented | PASS |
| No cloud/shared DB mutation occurred | PASS |

Approved planning C01–C28 map to these implementation/test gates. C23 is PASS for
explicitly reporting the production confidence block, as the user permits strongest
isolated validation when real PostgreSQL is unavailable; PostgreSQL-engine
concurrency is NOT represented as proven. No later roadmap cloud exit criterion
has been treated as permission to deploy.

### Known limitations and pending cloud operations

- Shared Cloud SQL migration: NOT EXECUTED — USER APPROVAL REQUIRED.
- Production PostgreSQL concurrency: NOT LIVE-VALIDATED. PostgreSQL server tools not available; installed Docker desktop-linux daemon/socket unavailable on read-only inspection. No shared DB fallback attempted. Offline PostgreSQL DDL and SQLite20way convergence do not prove production locking/isolation.
- Production provider/accounting crash behavior: NOT LIVE-VALIDATED.
- Production FinOps API/frontend/Collector deployment, verified financial identity and performance/capacity: NOT LIVE-VALIDATED.
- Crash after STARTED commit before actual remote receipt remains outcome uncertainty/debt; no provider billing confirmation is inferred.
- Total DB outage after provider can irrecoverably lose final quantities; durable started obligation survives and is exposed, never converted to zero or estimated usage.
- Admission-failure health counter is execution-process aggregate; durable started/pending/conflict debt is cross-restart DB truth. A DB outage cannot durably record another admission-failure update to that same DB.
- Quantity corrections are internal trusted referenced events only; no reconciliation/billing/price workflow. Retention execution disabled;24month policy encoded only.
- Local performance/memory measurements are environment-specific; no production throughput/CPU/memory guarantee.
- M11 billing integration: NOT IMPLEMENTED BY DESIGN.

Cloud deployment status = NOT EXECUTED — USER APPROVAL REQUIRED.
Exact pending actions: apply additive c10a8f6e2d41 after b37e90a14c62 to separately
approved shared Cloud SQL target; deploy approved backend/frontend and Collector
configuration; enable environment-owned FINOPS policy and verified principal
integration; create/activate disabled source-controlled accounting coverage alert
only with exact project/environment/notification inputs. No exact remote target is
selected. Each proposal needs all10 AGENTS.md operation/impact/security/cost/risk/
rollback fields and migration-specific lock/data/index/rollback detail. No remote
mutation proposed/executed in this implementation. Recovery rollback should disable
activation/use compatible software while preserving ledger; downgrade drops new
accounting data and is NOT a production-safe rollback method.

Ready for Next Milestone: **YES (local gates)**. **STOP. M11 NOT STARTED.**
