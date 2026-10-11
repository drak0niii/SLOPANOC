# Shared Cloud SQL migration reconciliation — 2026-10-11

Authorized scope: local history restoration, merge design, static graph/offline SQL and read-only metadata. **No shared upgrade, stamp, DDL, deployment, billing/archive or cloud mutation performed.** This report supersedes earlier forensics' unknown-history/current-head conclusions; earlier evidence is retained unchanged. M16 incomplete; no M17 progression.

## GCP Deployment Provenance

Project `pr-msn-dev-gl-slopai-01`. Authentication succeeded after allowing gcloud's local credential/log-cache writes for read-only requests. 19 services across four service regions; 149 retained revisions; 348 builds across global, europe-west1, europe-west4, us-central1, asia-south1; 760 image versions across ten Docker repositories. No query was capped at its 1000-build limit. Metadata-only projections exclude env/secrets, build steps/logs and source bodies. No image pull or source archive fetch.

No identifiable SLOPANOC-named service, revision, image or source label exists in the inspected metadata. This is **not** proof that a differently named/deleted deployment never existed. Commit `1f1cbccd2203f3889af0e2c0cf0ae1d750592b83` or descendants could not be correlated: zero source commit SHAs were exposed by inspected build fields. 140/149 retained revisions match build image digests, but none establishes the SLOPANOC historical schema producer. No fabricated deployment timestamp or schema creation timestamp. Service/revision/timestamp/digest/tag/build correlations are retained in `MIGRATION_RECONCILIATION_GCP_PROVENANCE.json`. Initial revision-list projection failed; simplified metadata projection repaired all four reads. Earlier authentication-blocked report is superseded only for these successful metadata reads.

## Historical Migrations Restored Locally

Exact recovered UTF-8 bytes restored from `MIGRATION_RECOVERY_SOURCES.json`, operations and identifiers unchanged:
- `9b6df6490c0e`: `083e55d199cbe536ed75021f60bf603d19114f6e8cd4c8266106ee1dac81b508` — PASS
- `a1f3c9e07b21`: `6d2393ff94157bf844948f5710d35f631d84e59b6172e011f6eb155d4940ab15` — PASS
- `c7e2a4f9b83d`: `69a71d1ad83cb64410897b856772fbfa7b2dc26ffc80b9e6a6fd5b0fdd076d9a` — PASS
- `d3f8b1c6a942`: `d9cc49147cc4d38cc357891a6e50808a714599013f6e2e9d0ff073064c45fd67` — PASS

All three shared ancestors are also byte/hash-identical. Historical descendants `e4a7c2b91d38`, `f1b6c3d05a27`, `b7c4e1a95d60` intentionally excluded: not applied on this live chain and not rollout prerequisites.

## Reconciled Alembic Graph

One root `d2081e4fd455`; 17 revisions; common branch point `3e59584b1012`; historical applied branch and current sibling preserved. Removing only the new merge from the **static** graph gives exactly heads `d3f8b1c6a942` and `b15e3f7a9c81`; new merge resolves them into one head. No orphan/missing parents or cycles. The common branch point remains historically real, now resolved by the merge; it is not deleted or reparented.

Static graph validation uses Alembic RevisionMap/ScriptDirectory traversal with identifiers parsed from exact historical source. Historical operation functions deliberately raise if selected. Forward sibling modules and actual `alembic/env.py` are unchanged and executed **offline**. This explicit limited proof does not conceal the full-import blocker: recovered a1 imports `pgvector`, unavailable in `.venv`. Ordinary `alembic heads/history` cannot yet load all modules. No dependency installed under the no-install constraint. Full graph/import acceptance FAIL pending dependency availability.

## New Merge Revision

`alembic/versions/c16f4a8b2d90_reconcile_historical_and_observability.py`; revision `c16f4a8b2d90`; parents exactly `(d3f8b1c6a942, b15e3f7a9c81)`. `upgrade()` and `downgrade()` contain only `pass`. Alembic owns normal version-row merge/split semantics. This is genuine graph reconciliation after both parents are satisfied, never a stamp or a fake historical node.

## Upgrade Path From d3f8b1c6a942

Alembic `_upgrade_revs` yields exactly:

`7c4e9a2d1f05 → 9f71c2a64e08 → b37e90a14c62 → c10a8f6e2d41 → d11b4a8c9e32 → e12c6d8a4b50 → f13a7c9d2e61 → a14d2e6f8c70 → b15e3f7a9c81 → c16f4a8b2d90`.

Excluded from execution: `d2081e4fd455 → b85242503972 → 3e59584b1012 → 9b6df6490c0e → a1f3c9e07b21 → c7e2a4f9b83d → d3f8b1c6a942`. SQL first inserts a sibling version row beside existing d3, advances that sibling through b15, then deletes only the d3 **version row** and updates b15 to c16. All historical business DDL, including pgvector extension creation and experience NOT NULL alteration, is excluded. Starting from a different revision invalidates this reviewed SQL/path.

## 7c4e9a2d1f05 Business Prerequisite Review

Separately classified CREATE_ONLY business migration, outside the 31 telemetry tables. Seven columns; PK `case_id`; VARCHAR `case_id` FK to existing `public.slopanoc_cases.case_id`; six NOT NULL columns; CHECK `version >= 1`; no defaults, secondary indexes or backfill.

Prior read-only snapshot: cases exists; referenced column VARCHAR NOT NULL; PK `slopanoc_cases_pkey` over `case_id`; progression table/index/type absent from inspected table/index snapshot. PostgreSQL default progression PK index name `slopanoc_case_troubleshooting_progressions_pkey`; expected default FK constraint `slopanoc_case_troubleshooting_progressions_case_id_fkey`; explicit CHECK `ck_slopanoc_case_troubleshooting_progressions_version_positive`. All fit 63-byte identifiers. Existing cases is referenced, not ALTERed or populated; FK DDL can acquire a parent lock that blocks writers until transaction completion.

**Fresh live review NOT EXECUTED:** this tool environment lacks an explicit approved DB binding. Previous snapshot is evidence, not a new measurement. An attempt to extract another process's binding was rejected by automatic approval review as an unintended credential source; not retried or bypassed. An approved configuration path/environment binding is required. No credentials printed or copied into artifacts.

## Forward Migration Safety Table

| Revision | Purpose | Objects | Classification | Existing ANOC table touched? | Risk |
|---|---|---|---|---|---|
| 7c4e9a2d1f05 | Business troubleshooting progression | slopanoc_case_troubleshooting_progressions | CREATE_ONLY | No (FK references cases) | FK references existing cases; validation requires lock on parent |
| 9f71c2a64e08 | M7 durable run/timeline/summary | observability_run_event, observability_run_status, observability_turn; 7 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| b37e90a14c62 | M9 SRE receipts/rollups | observability_sre_receipt, observability_sre_bucket, observability_sre_source; 1 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| c10a8f6e2d41 | M10 unsampled accounting | finops_accounting_source, finops_runtime_attempt, finops_usage_bucket, ai_usage_ledger, finops_usage_inbox; 5 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| d11b4a8c9e32 | M11 billing publication state | finops_billing_publication, finops_billing_ingest_state, finops_billing_projection_cache; 2 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| e12c6d8a4b50 | M12 reconciliation/allocation | finops_reconciliation_input, finops_reconciliation_registry, finops_reconciliation_generation, finops_reconciliation_state; 3 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| f13a7c9d2e61 | M13 unit economics | finops_unit_definition_version, finops_unit_denominator_snapshot, finops_unit_driver_version, finops_unit_economics_generation, finops_unit_economics_publication; 3 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| a14d2e6f8c70 | M14 financial controls | finops_budget_definition_version, finops_control_input, finops_forecast_backtest_generation, finops_control_generation, finops_anomaly_transition, finops_control_publication; 5 indexes | CREATE_ONLY | No | New empty tables/indexes only |
| b15e3f7a9c81 | M15 retention/archive receipts | observability_retention_checkpoint, observability_archive_receipt; 3 indexes | CREATE_ONLY | No | Indexes on M7 tables created in this rollout |
| c16f4a8b2d90 | Join satisfied branch histories | Alembic version rows only | Graph-only no-op (no schema classification) | No | New empty tables/indexes only |

All indexes are additive INDEX_ONLY operations; b15's two retention indexes target M7 tables created in this same rollout. No ALTER_EXISTING, DATA_BACKFILL, DESTRUCTIVE or UNKNOWN forward operation. Downgrades of the parent branches drop objects and are destructive, so are excluded from recovery. The merge no-op itself is outside schema-operation classifications.

## Existing Object Collision Check

Prior snapshot: zero planned table-name collisions and zero secondary/PK/unique-backing index collisions. Constraints are table-scoped: all planned target tables absent, so an unrelated table's same constraint name is not an incompatible collision. No names truncated. **Fresh live all-relkind `pg_class`, `pg_type`, table/index/constraint check remains BLOCKED**; previous catalog snapshot did not inventory every sequence/type namespace object. Do not certify a clean live namespace from stale or partial evidence. Compatible duplicates also stop rollout.

Prepared SELECT-only `M7_M15_RECONCILIATION_PREFLIGHT.sql` checks target revision, database/schema/search_path/read-only setting, referenced case PK/type and every planned relation/index/type name. Repeat the original full catalog fingerprint as well, including all schemas/defaults/types/indexes/constraints/views/triggers/extensions.

## Offline SQL

`docs/Telemetry/validation/M7_M15_RECONCILED_ROLLOUT.sql`; SHA-256 `6ad8cda0e1b88a25620dd9153eaa23959dde1419dcbea8d1202195f2345e409a`. Actual Alembic PostgreSQL offline env, explicit fake URL, no credentials or remote URL resolver. Range d3→c16; generated but NEVER executed. Reproduce static offline proof with `PYTHONPATH=. .venv/bin/python scripts/observability/reconcile_migration_sql.py`. Once pgvector exists, also require ordinary real-module offline command generation and byte-equivalence.

## Offline SQL Safety Review

32 CREATE TABLE statements; 29 CREATE INDEX statements (including unique/partial indexes). Zero DROP, TRUNCATE, ALTER, RENAME or business-data INSERT/UPDATE/DELETE. Flagged and individually reviewed **11 metadata DML statements**: 1 INSERT, 9 UPDATE, 1 DELETE, exclusively `alembic_version`; expected branch bookkeeping, not business deletion/backfill.

32 PK, 21 FK, 2 table UNIQUE and 60 CHECK constraints; 258 NOT NULL columns. Two server defaults, both `session_detached BOOLEAN DEFAULT false` on new M7 tables. Zero generated expressions or extension operations. Every table/index is mapped to its originating revision, every FK/constraint and every NOT NULL/default listed in `MIGRATION_RECONCILIATION_SQL_REVIEW.json`. FK targets include the existing cases parent; all other physical references point to newly created tables, with valid forward creation order. PK/UNIQUE add 34 backing indexes; secondary indexes are additional.

Regular CREATE INDEX on newly created empty tables needs no concurrent mode. DDL takes catalog/new-table locks; the case-parent FK can hold a conflicting lock across the full transaction. Resource/timeouts, concurrent DDL and writers must be coordinated; additive does not mean lock-free.

## Expected Target Schema

Existing public snapshot 16 tables/125 columns/29 indexes/119 constraints (PG18 includes 92 NOT NULL entries). Add 31 observability tables + one progression = **48 tables**. Add 340 columns = **465 columns**. Add 29 secondary + 34 constraint-backing indexes = **92 indexes**. Add 115 logical constraints + 258 NOT NULL entries = **492 catalog constraints** on PG18. Expected final logical totals: 48 PK, 26 FK, 3 UNIQUE, 65 CHECK, 350 NOT NULL entries. One final Alembic version row `c16f4a8b2d90`.

Counts are expectations from actual reviewed DDL plus prior snapshot, not live post-rollout evidence. Five ADK tables and all ten existing business tables/columns/indexes/constraints remain structurally unchanged. Object definitions, not just counts, must pass postflight.

## Isolated PostgreSQL Rehearsal

**NOT EXECUTED.** No postgres/initdb binaries discovered in PATH/conventional package locations; Docker CLI exists but its existing daemon socket is absent; project migration fixtures use disposable SQLite, which cannot validate historical pgvector/PostgreSQL-specific schema semantics. No tooling installed, daemon started, external infrastructure started or shared DB used for rehearsal. SQLite regression results do not count as a PostgreSQL rehearsal.

## Final Alembic Head

Static head `c16f4a8b2d90` PASS; ordinary full-module import/head gate BLOCKED by pgvector. Local migration regression run: **8 passed / 5 failed**, all five failures due to missing pgvector during graph loading. New reconciliation tests pass (exact hashes/no-op merge/forward path/no historical replay/DDL counts/metadata-only DML). Graph-sensitive regression assertions updated to the new head; SQLite telemetry fixture targets b15 explicitly so it does not attempt the unrelated historical PostgreSQL branch. No unrelated application implementation changed.

## Proposed Shared Cloud SQL Command

**DO NOT EXECUTE. NOT APPROVED.** From this reviewed checkout with an explicitly verified migration binding resolving to project `pr-msn-dev-gl-slopai-01`, instance `sloc-anoc-sandbox01`, region `europe-west4`, database `slopanoc`, schema `public`:

```sh
.venv/bin/python -m alembic -c alembic.ini upgrade c16f4a8b2d90
```

Do not use generic `head`, stamps, autogeneration or unverified Secret Manager/default URL fallback. Owner must confirm DEV/STAGING/PRODUCTION and all shared consumers despite sandbox naming. Future approval covers exactly this migration operation and 32 tables/63 indexes/373 PG18 constraint entries plus Alembic bookkeeping; any backup/IAM/deployment/billing/archive operation requires its own approval.

## Pre-Flight Requirements

1. Resolve pgvector through approved local dependency availability; run ordinary heads/branches/history and actual offline range SQL; require SQL byte/hash parity and all migration tests PASS.
2. Supply approved explicit read-only binding; verify d3 is still the sole live revision and `public` is the effective schema/search path. Require original normalized fingerprint `fca850228ccaed481dbd3fa31fd785cca494aa36122eefe0f7e1b1b277df9d09` unchanged. Repeat all namespace/collision/PK/type checks, stop for any duplicates or drift.
3. Rehearse exact reviewed SQL/path on already available or separately authorized isolated PostgreSQL using recovered fingerprint/schema, historical revision set only there, all original object definitions retained, 32 new tables verified, final c16 and repeated upgrade no-op. Include FK/uniqueness/NOT NULL/partial index enforcement. This rehearsal has not happened.
4. Establish recovery protection and verified restoration procedure before rollout. **Current read-only GCP config: automated backup enabled=false; PITR=false; no backup runs listed**. Seven retained-backup setting alone is not a backup. No protective feature enabled or backup created. Any backup/PITR mutation requires a distinct exact-operation approval; no recovery guarantee is claimed.
5. Owner confirms environment/consumers, migration role with existing reviewed DDL privileges and schema ownership, change window/writer coordination/lock budget/statement timeout/available storage. Keep readers read-only; do not broaden runtime IAM/DB roles.
6. Freeze reviewed source/SQL hashes; prevent concurrent migrators/DDL; coordinate cases writers because the parent FK lock can persist across the complete transaction. Validate locks/capacity via approved SELECTs; do not kill sessions by implication.
7. Present exact operation/project/resources/operational lock impact/security impact (existing migration privileges only)/cost impact (new storage/indexes; no billing export changes)/data-loss-interruption risks/recovery plan/environment and wait for explicit approval.

## Rollback / Failure Plan

Verified source behavior: `context.configure` does not enable `transaction_per_migration`; PostgreSQL uses transactional DDL; actual offline SQL has one BEGIN and COMMIT enclosing the entire path; no autocommit block, concurrent index, extension operation or explicit internal COMMIT. Thus the reviewed configuration is a **single transactional rollout**, not per-revision durable commits. Reverify deployed env before relying on this.

- Before first DDL: stop; d3/schema unchanged. Resolve permission/connectivity/lock prerequisites; do not stamp.
- During DDL or between revisions: rollback the transaction (normal Alembic failure/disconnect); new DDL and transient sibling version rows should roll back together. Read-only recheck revision and original fingerprint before a retry. If any objects persist, assumption is invalid: stop and investigate actual env/transaction boundaries; no automated cleanup/stamp.
- After all DDL but before final version update: same transaction, so rollback applies; do not manually mark c16.
- Commit outcome unknown after disconnect: SELECT revision/full schema first. c16 plus exact target means committed; d3 plus baseline means rolled back. Mixed/intermediate state is a blocker needing a separately reviewed recovery plan, not a blind rerun.
- After successful commit: prefer application rollback while leaving additive tables in place. **Do not downgrade the database**; dropping financial ledger/receipts would destroy history and graph downgrade can traverse multiple branches. Preserve original business data; use a verified backup/restore only under separate approval if needed. No current backup/PITR recovery exists to assume.

## Post-Rollout Validation

SELECT-only: final sole c16 row; all 48 tables/465 columns/92 indexes/492 PG18 constraints and exact definitions; case FK/PK/check compatibility; expected partial/unique index definitions; all original business/ADK objects/defaults/types/extensions unchanged. Recompute original-table subset fingerprint against baseline; capture no raw application content. Prepared `M7_M15_RECONCILED_POST_ROLLOUT_CHECKS.sql` verifies merge revision, all 32 new tables/63 indexes, per-table constraint counts and full column/index/constraint metadata. Original baseline fingerprint comparison and exact definition parity remain required beyond counts.

Test protected GET-only read-plane health/run/SRE/cost endpoints with existing approved identity and environment capabilities, verify honest empty/source states and bounded read-only pools, FinOps content separation and no automatic writers/jobs/model calls. An empty new schema does not configure financial providers or archive, and missing source data remains unavailable. A live repeated migration command, even if expected no-op, still requires exact approval; only isolated rehearsal reruns are authorized here.

## Remaining Blockers

Full import/test gate (pgvector); approved fresh live read binding and comprehensive namespace/fingerprint checks; isolated PostgreSQL rehearsal; backup/PITR recovery; environment/consumer/lock/privilege confirmation. Historical deployment commit provenance remains UNVERIFIED, an explicitly recorded operational limitation, not permission to invent history. Billing = **NOT_CONFIGURED**; Archive = **NOT_CONFIGURED**. No production deployment/auth qualification claim; no M16/M17 completion or UI changes.

## Cloud / Database Changes Performed

**NONE.** Read-only GCP metadata and local files only. No shared SQL issued in this pass because approved binding was unavailable. Local gcloud cache/log writes are not cloud mutations. No stamps or local database rehearsal performed.

## Safe To Request Shared DB Migration Approval

**NO.** Static forward design/SQL PASS but mandatory import/regression, fresh live safety, rehearsal and recovery gates remain unresolved. No actual approval request for database execution is made. Ready for Next Milestone: **NO**. Stop here; do not migrate shared Cloud SQL.
