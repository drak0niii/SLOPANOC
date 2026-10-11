# Hybrid Architecture / Local Scaffolding Validation — 2026-10-11

Scope: approved architecture and inert local contracts/configuration. M16 INCOMPLETE;
M17 UNSTARTED. Cloud / Database Changes Performed: **NONE**.

## Final validation

197 tests PASS, zero failures/errors/skips, one existing Starlette/AnyIO deprecation
warning. Exact command, file hashes, test counts/timing, failures/repairs and current
limitations: [JSON receipt](HYBRID_ARCHITECTURE_LOCAL_VALIDATION.json).

Contract tests cover strict schema/size/environment/content denial, false/negative/
overflow counts, publication states/leases, disabled configuration, deterministic
digest, disposable SQLite critical+outbox rollback, isolated receipt/projection
rollback, duplicates, conflicts and stale versions. Fake adapters illustrate ports;
they do not prove a Cloud SQL implementation or producer/publisher crash recovery.

Pinned native contrib 0.160.0 validates existing local/production source configs,
composed nop branch and future Pub/Sub exporter schema (validate only, never start).
Actual loopback pipeline shows new operational branch sanitized before tail decision;
existing Cloud Trace/archive routing/privacy/outage regressions pass. Google pinned
manifest independently proves production distribution component inclusion, not live
Google image/service behavior. Pin/image/active configs unchanged by this pass.

Static 17-node migration graph retains sole c16f4a8b2d90 head and exact recovered
source hashes. No migration added/applied, no ordinary historical imports or shared
PostgreSQL rehearsal claimed. New modules import/compile. Documentation source pack,
local links, phase/resource inventory and git diff --check pass. All requested
architecture classifications are present. Mermaid source manually reviewed; native
syntax validation NOT RUN (no installed parser/CLI). Frontend unchanged by this pass;
frontend tests/build NOT RUN. Full M16 CPU/load/regression gates NOT RUN.

## Failures and repairs

Initial contracts needed explicit union-level input hiding, a genuinely positive
source-version type and AnnAssign-aware graph parsing. Repaired before final suite.
Sandbox disallowed loopback binding; exact local test escalation approved. One
existing M15 archive rglob test encountered transient removal of its staging folder;
focused rerun and final full scoped suite passed with no baseline code change.
New branch fixture initially contained prohibited links, correctly dropping entire
spans. Corrected fixture removes links while retaining poisoned raw attributes,
status and event values; privacy gate unchanged. All attempts retained in JSON.

## Unvalidated target requirements

No SQL outbox adapter, real same-transaction producer integration, publisher,
OTLP-to-envelope adapter, projector service/projection schema, database roles or
production subscription/identity/resource exists as a result of this pass. No live
Pub/Sub/SQL/GCS/BigQuery/pgAudit/Monitoring qualification. Read-plane/UI source gaps
remain unavailable, historic omissions remain unknown. Reconciliation/recovery
blockers unchanged. Full auditability is a target, not a historical capture claim.

Ready For GCP Provisioning Review: **YES — planning review only**. Confirm exact
project/environment/account/resource/network/auth/cost bindings and complete runtime
implementation/recovery/M16 gates before any later activation. No mutation approved.
Ready for Next Milestone: **NO**. C41/C42 not PASS; STOP before M17/cloud mutation.
