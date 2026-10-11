# Local Phase-1 pipeline validation — 2026-10-11

M16 INCOMPLETE; C41/C42 not PASS; M17 UNSTARTED. Local SQL capture + outbox +
publisher + fake transport + projector + GET/strict frontend DTO are implemented.
Cloud/shared DB changes: **NONE**. Final validation results are recorded below.

Actual local tests cover canonical run/accounting rollback, abrupt child-process
crashes, expired leases and token safety, retry/backoff/exhaustion, queue pressure,
1/2/100 deliveries, conflicting duplicates, out-of-order terminal precedence,
receipt/projection rollback and recovered failure state, privacy sentinels at every
sink, GET-only RBAC/environment reads, and a harmless ChatService/FakeRunner turn.
No real provider/equipment/command is invoked. First persisted terminal emits no
invented acceptance. Model counts do not claim full agent lifecycles or token totals.

See [measurement JSON](HYBRID_PHASE1_LOCAL_MEASUREMENT.json) for four alternating
50-operation batches per arm of actual M10 STARTED transactions, capture disabled
versus enabled. Publisher/projector CPU is measured separately. This is isolated
SQLite, fake transport and explicit ticks, not PostgreSQL or M16 overhead acceptance.
No propagation SLO, cloud latency or production capacity proof is claimed.

New migration d26a1b7c9e40 directly follows c16f4a8b2d90. Six additive tables/five
indexes; disposable SQLite upgrade/downgrade/parity and PostgreSQL offline DDL.
Recovered historical bytes and reconciliation merge unchanged. Full Alembic imports
pass only with temporary Python pgvector 0.5.1 in
`/private/tmp/slopanoc-hybrid-phase1-test-deps`; repository dependency manifests and
shared database extensions were not changed. Disposable PostgreSQL remains unavailable.

Production adapter, verified Pub/Sub ingress, secure workload/DB identities,
confirmed resource binding, shared migration/recovery and full M16 gates remain
pending. **Ready for Phase-1 GCP provisioning: NO. Ready for Next Milestone: NO.**

## Final results

- Backend: **407 unique affected tests PASS**: final sandbox main run 405 PASS,
  one socket-dependent test deselected; two exact loopback tests PASS with approved
  local-only escalation (including the separately excluded reader process test).
  The main run includes 33 new Phase-1 runtime tests and six migration test files.
- Frontend: **278 tests PASS / 40 files** in affected API/console/settings suite;
  `npm run build` PASS (TypeScript + Vite). Existing jsdom scrollTo/ref warnings
  do not change results. New hybrid DTO/client tests: 8 PASS.
- Compilation/imports, migration parity/static graph, offline DDL, immutable
  historical source hashes, source-pack/doc links and `git diff --check`: PASS.
- Alembic: 18 revisions, sole head d26a1b7c9e40, parent c16f4a8b2d90; six tables,
  five indexes. Full Python migration imports PASS with isolated pgvector 0.5.1.
- Native Collector 0.160.0 capability/config validation and safe pre-tail nop
  evaluation-branch loopback regression PASS. Active Collector config/pin unchanged
  by this pass. Operational Pub/Sub remains OPTIONAL / DISABLED; no cloud exporter
  was started. Mermaid syntax manually reviewed; renderer/parser NOT RUN.

Focused STARTED transaction means, 200 operations per arm:

| Measurement | Baseline | Capture enabled | Added |
|---|---:|---:|---:|
| Wall ms/operation | 2.737 | 4.543 | 1.806 |
| Process CPU ms/operation | 2.415 | 4.038 | 1.623 |

Async publisher: 343.7 ms CPU / 200 events;
projector: 412.2 ms CPU / 200 events.
Fresh event→projection: 51.2 ms;
event→local read: 57.4 ms. Explicit ticks
exclude poll wait; no propagation SLO or whole-ANOC <3% conclusion.

## Failures repaired / environmental attempts

Initial local tests used missing turn-origin metadata and assumed a retry would
precede older pending work; fixture assumptions corrected without changing ordering
or canonical identity rules. A canonical debt-resolution hook initially checked
transient state after capture had changed it; repaired to use retained gap reason.
Unsafe constructed-model serialization warnings could include rejected values;
warning-free sink serialization and sentinel assertions now pass. A first terminal
snapshot now emits terminal only, preserving unknown acceptance instead of inventing
an earlier durable RUNNING state. Processing failures resolve on successful retry
including duplicate delivery. Failed failure-store writes cause RETRY, not false ACK.

Two initial pytest invocations named nonexistent storage test files and collected
nothing; corrected to the actual repository/persistence suites. The default migration
import initially failed for missing pgvector; temporary local installation repaired
that environment gate without changing dependency manifests or a DB extension.
Sandbox package download initially failed DNS; isolated package download escalation
succeeded. Two socket tests failed sandbox bind restrictions; local-only escalation
passed both. No test thresholds, historical sources or M16 criteria were waived.

## Explicit acceptance and deployment limits

Requested LOCAL catalogue/atomic capture/publisher/fake transport/projector/durable
projection/read/privacy/ordering/duplicates/crashes/build/validation/docs: **PASS**.
The harmless reality test is actual ChatService + FakeRunner, not a live Gemini,
Teams or equipment invocation. Local fake transport is volatile; isolated safe
spool in crash tests represents a delivered message, not production broker durability.
First run capture is M7 asynchronous/coalesced persistence, not synchronous business
admission. Agent/tool/approval/action lifecycle capture is explicitly deferred where
no shared canonical durable owner exists. Token/cost truth remains M10–M14.

PostgreSQL/Cloud SQL locks, SQL roles/grants, broker durability/auth/DLQ semantics,
production binding, cost/load/retention/rebuild and live recovery: **NOT VALIDATED**.
Production adapters/activation: **NOT IMPLEMENTED**. Shared migrations/deployment:
**NOT EXECUTED — USER APPROVAL REQUIRED**. M16 C41/C42 **NOT PASS**; full milestone
acceptance remains blocked. M17 UNSTARTED. No external message or resource mutation.
