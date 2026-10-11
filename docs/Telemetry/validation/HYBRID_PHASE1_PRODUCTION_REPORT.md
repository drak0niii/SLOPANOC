# Phase-1 production-readiness validation receipt — 2026-10-11

Local implementation/validation only. M16 INCOMPLETE, C41/C42 NOT PASS, M17 UNSTARTED.
Cloud/shared DB changes NONE. No deploy, provision, shared migration, IAM, pgAudit,
billing, remote Terraform plan/apply/import/state operation.

## Results

- 461 main affected backend tests PASS, one loopback Collector test deliberately
  selected into separate local-socket invocation; 2 local loopback/process tests
  PASS = **463 unique backend tests**. Final root cleanup/config/doc changes checked
  by additional 81-test focused rerun PASS (overlaps those tests, not added to total).
- 30 new production-boundary cases: actual ANOC capture ON/OFF; separate enabled
  publisher/projector factories; disabled workers no DB/ADC; canonical owner/env
  configuration; safe Google request/timeout; wrapped push schema/base64/privacy/
  environment/attributes; Google verifier signature/audience/verified email boundary;
  forged-header refusal; certificate outage retries; cancellation retains capacity;
  committed owner survives publisher failure/recovery; broker outage/DLQ mock;
  duplicate/conflict; optimized capture duplicate/hash conflict; Case CAS rollback;
  review SQL/combined rollout source checks. All clients/token transport mocked.
- Existing abrupt child-process commit/publish-death windows, atomic run/usage/debt,
  projection order/idempotency/privacy, GET-only server RBAC/environment isolation,
  migration reconciliation/historical source fingerprints and M15/M16 affected
  regressions included. No real provider/Teams/equipment or GCP tests.
- 278 frontend tests / 40 files PASS. TypeScript/Vite production build PASS. No
  frontend redesign, source changes or fake records introduced this pass.
- Terraform 1.14.0 fmt/check + Google 7.46.1 provider schema validate PASS.
  Init backend=false, cache /private/tmp, signed lock retained; no credentials/API
  queries/plan. Sandbox IPC bind required explicit local validation escalation.
- 18-revision actual module imports / sole d26a1b7c9e40 head PASS; recovered historic
  migration hashes/merge preserved. Combined offline d3→d26 SQL: six hybrid tables,
  five hybrid indexes, FK/CHECK plus progression/M7–M15, final version bookkeeping.
  Only DELETE/UPDATE target alembic_version. No DROP/destructive ALTER/rename/business
  DML. SQL grant table names verified against actual imported metadata; role behavior
  NOT validated on PostgreSQL.
- **POSTGRESQL REHEARSAL NOT EXECUTED**: Docker daemon socket missing. No shared DB
  fallback or data copied. Historical upgrade + repeat no-op + grant rehearsal and
  existing backup/PITR gate remain deployment blockers.
- Read-only gcloud confirmed project number 995771529960, existing Cloud SQL region
  europe-west4 and database name slopanoc. Filtered lists returned no proposed new
  audit topics/subscriptions/services/accounts. No schema contents or business data.
- Compile/import, exact resource-name syntax, document pack consistency, source
  review, git status/diff/untracked and git diff --check PASS. Original Mermaid
  preserved; no new diagram renderer or external design-file write.

## Performance

Clean isolated SQLite/M10 STARTED measurement 200/arm: wall baseline/enabled
2.682/4.408 ms, added **1.726 ms (+64.33%)**; CPU 2.342/3.831 ms, added
**1.489 ms (+63.58%)**. Previous added wall/CPU 1.806/1.623 ms. See detailed JSON.
Separate profiling identifies SQL execution/ORM/await as dominant: audit 0.923 ms,
outbox 0.480 ms per execute; construction 0.032 ms, privacy 0.013 ms, envelope
serialization 0.012 ms, canonical serialization/hash 0.023 ms. Profile spans multiple
sinks; totals overlap, cannot be summed or labeled pure ORM time. Additional fields
and host variance limit causal before/after claims. No production latency/CPU SLO or
C41/C42 PASS claim. Optimization preserves sink privacy and transactional guarantees.

## Failures and repairs

Initial two tests expected only seven events; adjusted for the actual additional turn
binding (three receipts/run flow). A legacy progression migration test used arbitrary
head on SQLite and reached historical PostgreSQL vector extension; now targets its
actual M7 progression revision, without changing historical code. Local default
Collector/socket invocation hit sandbox EPERM; separate local-only escalation PASS.
One abbreviated test name was corrected; no acceptance requirement skipped. Terraform
first syntax fmt found three one-line multi-argument blocks; repaired, formatted and
validated. Registry DNS blocked default init; approved backend-disabled local download
succeeded. Default provider IPC blocked schema load; local-only escalation succeeded.
Final review preserved verifier capacity across request cancellation and makes
certificate transport failure retryable; tests PASS. Case-store capture is installed
inside the existing lifespan's cleanup scope so failed startup cannot leave a new
capture binding installed. No unrelated local changes discarded.

## Exact remaining blockers

1. Disposable PostgreSQL full historical upgrade, repeat/no-op and role behavior.
2. Reviewed backup/PITR/reconciliation recovery prerequisite.
3. Reviewed immutable image, existing network/secure SQL logins/role memberships and
   exact secret IDs/versions; public schema binding/effective inherited privileges.
4. Exact verified projector origin/audience and deployment invocation protection.
5. Reader production human identity/role mapping, authorized invoking caller,
   frontend origin/reader URL and immutable producer policy binding.

Full auditability is not claimed: approval/dispatch/selection ownership and retrieval/
tool lifecycle gaps are classified in AUDIT_COVERAGE_MATRIX.md. These must not be
silently presented as captured. Phase-1 scope is partial canonical audit, not M17 UI.
Ready For Phase-1 GCP Provisioning NO. Ready for Next Milestone NO.
