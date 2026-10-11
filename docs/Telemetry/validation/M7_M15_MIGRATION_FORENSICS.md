# M7–M15 shared-database migration forensics

Date: 2026-10-11, Europe/Bucharest. FORENSIC ONLY; no migration execution, stamp, schema restoration, data export or cloud mutation.

## Decision

**NOT SAFE TO REQUEST MIGRATION EXECUTION APPROVAL YET.** The database revision has no recoverable source in this local checkout/object store. Its expected schema and parent are unknown. The exact connection to the local head cannot be certified. No offline rollout SQL was generated. Do not stamp, fabricate a no-op revision, change parent links, autogenerate against the shared database, or run upgrade head.

## Evidence and limits

- Actual reader database: PostgreSQL 18.4, `slopanoc`, `public`, via `127.0.0.1:5433`; `transaction_read_only=on` throughout catalog inspection. URL/credentials/application rows excluded.
- Proxy target: project `pr-msn-dev-gl-slopai-01`, region `europe-west4`, instance `sloc-anoc-sandbox01`. This is a shared Cloud SQL target, not a disposable local PostgreSQL server. Runtime auth is development; the resource names are DEV/sandbox-named, but the owner must confirm rollout environment and consumers.
- Current revision: `d3f8b1c6a942`. Local HEAD commit: `56effc90540d6d3e7e8e5add0086a5cf85d5607d` (Visit-Version). Current working tree is authoritative and remains unmodified except forensic artifacts/status.
- Git search: full non-shallow repo; local Visit-Version and cached origin/Visit-Version; all five tags; all reflogs; `git log --all --reflog -G d3f8b1c6a942 -- alembic`; and every one of 2,319 Git blobs, including unreachable objects. No missing migration source found. The single matching blob was the recent .agent/OBSERVABILITY_EXECUTION.md record, not historical migration code. No remote fetch performed.
- Introducing commit/branch/filename/down_revision/schema changes of the unknown revision: UNDETERMINED. No evidence establishes rename/deletion/rebase. Source may exist outside the local object store, in the original deployment artifact/checkout, another clone or an uncommitted file. Absence is not proof of an incorrect database stamp.
- Operational classification: **MISSING_HISTORICAL_MIGRATION (unrecoverable locally)**. BRANCH_DIVERGENCE, REBASE_HISTORY_DRIFT and INCORRECT_DATABASE_STAMP remain unproven alternatives. MULTIPLE_HEADS ruled out in the local tree.
- Read-only queries: catalog/table/index/column/constraint inspection only. No application table rows or financial records queried; no live migration environment invoked.

## Local migration graph

One root, one head, 12 revisions, no branch points, merge revisions, missing parents or orphan local revisions. Database revision is outside this complete local graph.

```text
base
  -> d2081e4fd455  baseline slopanoc cases and knowledge schema
  -> b85242503972  case timestamps use timezone-aware columns
  -> 3e59584b1012  chat attachments table
  -> 7c4e9a2d1f05  case troubleshooting progression (authoritative, versioned)
  -> 9f71c2a64e08  Add bounded operational observability storage; never ADK schema.
  -> b37e90a14c62  M9 compact SRE receipts/rollups, no accounting schema; local authoring only.
  -> c10a8f6e2d41  M10 dedicated runtime usage accounting; no shared database application.
  -> d11b4a8c9e32  M11 operational financial state only; no billing warehouse or shared application.
  -> e12c6d8a4b50  M12 additive derived reconciliation state; frozen application-only schema.
  -> f13a7c9d2e61  M13 frozen additive application-owned analytical persistence.
  -> a14d2e6f8c70  M14 financial controls; additive, source schemas untouched.
  -> b15e3f7a9c81  M15 retention checkpoints and archive receipts; no full trace database blobs.

d3f8b1c6a942 (shared database) -> UNKNOWN CONNECTION -> local chain
```

## Every local migration

| Revision | Parent | File | Purpose | Upgrade classification | Tables affected |
|---|---|---|---|---|---|
| d2081e4fd455 | base | d2081e4fd455_baseline_slopanoc_cases_and_knowledge_.py | baseline slopanoc cases and knowledge schema | CREATE_ONLY | slopanoc_case_context_items, slopanoc_case_memberships, slopanoc_case_session_links, slopanoc_cases, slopanoc_knowledge_objects |
| b85242503972 | d2081e4fd455 | b85242503972_case_timestamps_use_timezone_aware_.py | case timestamps use timezone-aware columns | ALTER_EXISTING | slopanoc_case_context_items, slopanoc_case_memberships, slopanoc_case_session_links, slopanoc_cases |
| 3e59584b1012 | b85242503972 | 3e59584b1012_chat_attachments_table.py | chat attachments table | CREATE_ONLY | slopanoc_chat_attachments |
| 7c4e9a2d1f05 | 3e59584b1012 | 7c4e9a2d1f05_case_troubleshooting_progression.py | case troubleshooting progression (authoritative, versioned) | CREATE_ONLY | slopanoc_case_troubleshooting_progressions |
| 9f71c2a64e08 | 7c4e9a2d1f05 | 9f71c2a64e08_observability_storage.py | Add bounded operational observability storage; never ADK schema. | CREATE_ONLY | observability_run_event, observability_run_status, observability_turn |
| b37e90a14c62 | 9f71c2a64e08 | b37e90a14c62_sre_operational_rollups.py | M9 compact SRE receipts/rollups, no accounting schema; local authoring only. | CREATE_ONLY | observability_sre_bucket, observability_sre_receipt, observability_sre_source |
| c10a8f6e2d41 | b37e90a14c62 | c10a8f6e2d41_finops_runtime_usage.py | M10 dedicated runtime usage accounting; no shared database application. | CREATE_ONLY | ai_usage_ledger, finops_accounting_source, finops_runtime_attempt, finops_usage_bucket, finops_usage_inbox |
| d11b4a8c9e32 | c10a8f6e2d41 | d11b4a8c9e32_billing_source_state.py | M11 operational financial state only; no billing warehouse or shared application. | CREATE_ONLY | finops_billing_ingest_state, finops_billing_projection_cache, finops_billing_publication |
| e12c6d8a4b50 | d11b4a8c9e32 | e12c6d8a4b50_finops_reconciliation.py | M12 additive derived reconciliation state; frozen application-only schema. | CREATE_ONLY | finops_reconciliation_generation, finops_reconciliation_input, finops_reconciliation_registry, finops_reconciliation_state |
| f13a7c9d2e61 | e12c6d8a4b50 | f13a7c9d2e61_finops_unit_economics.py | M13 frozen additive application-owned analytical persistence. | CREATE_ONLY | finops_unit_definition_version, finops_unit_denominator_snapshot, finops_unit_driver_version, finops_unit_economics_generation, finops_unit_economics_publication |
| a14d2e6f8c70 | f13a7c9d2e61 | a14d2e6f8c70_finops_controls.py | M14 financial controls; additive, source schemas untouched. | CREATE_ONLY | finops_anomaly_transition, finops_budget_definition_version, finops_control_generation, finops_control_input, finops_control_publication, finops_forecast_backtest_generation |
| b15e3f7a9c81 | a14d2e6f8c70 | b15e3f7a9c81_observability_policy.py | M15 retention checkpoints and archive receipts; no full trace database blobs. | CREATE_ONLY | observability_archive_receipt, observability_retention_checkpoint, observability_run_status, observability_turn |

CREATE_ONLY revisions include INDEX_ONLY statements on newly created objects. M15 adds indexes to the M7 operational tables. Downgrades drop created tables/indexes and are DESTRUCTIVE; they are not an approved recovery plan. The earlier timestamp migration is ALTER_EXISTING and must not be blindly rerun.

## Shared database schema inventory

Current: 16 public tables, 29 indexes, 125 columns and 119 catalog constraints. PostgreSQL 18 exposes 92 NOT NULL entries separately; the remainder is 16 primary keys, 5 FKs, 5 checks and 1 unique constraint. No user views or triggers reported by the inspected catalogs.

Schemas: information_schema, pg_catalog, pg_toast, public. Extensions: plpgsql 1.0 in pg_catalog, vector 0.8.5 in public. Neither extension needs enabling for M7–M15.

Existing tables:

- `public.adk_internal_metadata`
- `public.alembic_version`
- `public.app_states`
- `public.events`
- `public.sessions`
- `public.slopanoc_case_context_items`
- `public.slopanoc_case_memberships`
- `public.slopanoc_case_session_links`
- `public.slopanoc_cases`
- `public.slopanoc_chat_attachments`
- `public.slopanoc_experience_records`
- `public.slopanoc_knowledge_evidence_index`
- `public.slopanoc_knowledge_objects`
- `public.slopanoc_telco_context_assertions`
- `public.slopanoc_telco_context_profiles`
- `public.user_states`

Existing indexes (exact catalog definitions):

```text
CREATE UNIQUE INDEX adk_internal_metadata_pkey ON public.adk_internal_metadata USING btree (key)
CREATE UNIQUE INDEX alembic_version_pkc ON public.alembic_version USING btree (version_num)
CREATE UNIQUE INDEX app_states_pkey ON public.app_states USING btree (app_name)
CREATE UNIQUE INDEX events_pkey ON public.events USING btree (id, app_name, user_id, session_id)
CREATE INDEX idx_events_app_user_session_ts ON public.events USING btree (app_name, user_id, session_id, "timestamp" DESC)
CREATE UNIQUE INDEX sessions_pkey ON public.sessions USING btree (app_name, user_id, id)
CREATE UNIQUE INDEX slopanoc_case_context_items_pkey ON public.slopanoc_case_context_items USING btree (item_id)
CREATE UNIQUE INDEX slopanoc_case_memberships_pkey ON public.slopanoc_case_memberships USING btree (case_id, user_id)
CREATE UNIQUE INDEX slopanoc_case_session_links_pkey ON public.slopanoc_case_session_links USING btree (session_id)
CREATE UNIQUE INDEX slopanoc_cases_pkey ON public.slopanoc_cases USING btree (case_id)
CREATE INDEX ix_slopanoc_chat_attachments_owner_session ON public.slopanoc_chat_attachments USING btree (owner_user_id, session_id)
CREATE INDEX ix_slopanoc_chat_attachments_session_message ON public.slopanoc_chat_attachments USING btree (session_id, message_id)
CREATE INDEX ix_slopanoc_chat_attachments_status_created ON public.slopanoc_chat_attachments USING btree (status, created_at)
CREATE UNIQUE INDEX slopanoc_chat_attachments_pkey ON public.slopanoc_chat_attachments USING btree (attachment_id)
CREATE INDEX ix_experience_owner ON public.slopanoc_experience_records USING btree (owner_id)
CREATE INDEX ix_experience_owner_case ON public.slopanoc_experience_records USING btree (owner_id, case_id)
CREATE INDEX ix_experience_owner_recorded_at ON public.slopanoc_experience_records USING btree (owner_id, recorded_at)
CREATE INDEX ix_experience_owner_skill ON public.slopanoc_experience_records USING btree (owner_id, skill_id)
CREATE INDEX ix_experience_owner_type ON public.slopanoc_experience_records USING btree (owner_id, experience_type)
CREATE UNIQUE INDEX slopanoc_experience_records_pkey ON public.slopanoc_experience_records USING btree (experience_id)
CREATE INDEX ix_evidence_index_content_hash ON public.slopanoc_knowledge_evidence_index USING btree (content_hash)
CREATE INDEX ix_evidence_index_knowledge_id ON public.slopanoc_knowledge_evidence_index USING btree (knowledge_id)
CREATE INDEX ix_evidence_index_text_search_vector ON public.slopanoc_knowledge_evidence_index USING gin (text_search_vector)
CREATE UNIQUE INDEX slopanoc_knowledge_evidence_index_pkey ON public.slopanoc_knowledge_evidence_index USING btree (evidence_id)
CREATE UNIQUE INDEX slopanoc_knowledge_objects_pkey ON public.slopanoc_knowledge_objects USING btree (knowledge_id, version_label)
CREATE UNIQUE INDEX slopanoc_telco_context_assertions_pkey ON public.slopanoc_telco_context_assertions USING btree (assertion_id)
CREATE UNIQUE INDEX slopanoc_telco_context_profiles_pkey ON public.slopanoc_telco_context_profiles USING btree (profile_id)
CREATE UNIQUE INDEX uq_telco_context_profile_owner ON public.slopanoc_telco_context_profiles USING btree (owner_kind, owner_id)
CREATE UNIQUE INDEX user_states_pkey ON public.user_states USING btree (app_name, user_id)
```

Known earlier-schema comparison: all five baseline tables exist; all six case timestamps changed by b85242503972 are TIMESTAMPTZ; attachments and all three declared attachment indexes exist. **slopanoc_case_troubleshooting_progressions is missing**, so the database does not satisfy the known 7c4e9a2d1f05 prerequisite. Existing experience/evidence-index/telco-profile/assertion tables are absent from the current Alembic creation history; preserve them. Their presence does not prove authorship by the unknown revision.

A static column/type/nullability comparison against the five baseline tables plus attachments, with the six timezone alterations represented in memory, found **zero column differences** for those six known tables. This comparison did not execute any migration. It establishes compatibility of those objects with the known pre-progression baseline, not equivalence of the entire database to that revision. The detailed comparison is included in the shared schema metadata artifact.

The exact schema expected at d3f8b1c6a942 cannot be compared without its source. Broad compatibility with some earlier local revisions does not justify a stamp or claiming equivalence.

## M7–M15 table inventory

| Milestone | Revision | Table | Purpose | Physical FK dependencies |
|---|---|---|---|---|
| M7 | 9f71c2a64e08 | observability_run_event | Bounded run event timeline | None |
| M7 | 9f71c2a64e08 | observability_run_status | Durable cross-instance active/terminal run state | None |
| M7 | 9f71c2a64e08 | observability_turn | Retained terminal turn summaries | None |
| M9 | b37e90a14c62 | observability_sre_receipt | Pending/settled SLO contribution receipts | None |
| M9 | b37e90a14c62 | observability_sre_bucket | Minute SLO counters | None |
| M9 | b37e90a14c62 | observability_sre_source | Coverage and producer freshness evidence | None |
| M10 | c10a8f6e2d41 | finops_accounting_source | Coverage, debt and source freshness | None |
| M10 | c10a8f6e2d41 | finops_runtime_attempt | Unsampled billable attempt population | None |
| M10 | c10a8f6e2d41 | finops_usage_bucket | Minute usage/cost aggregates | None |
| M10 | c10a8f6e2d41 | ai_usage_ledger | Append-oriented usage/correction ledger | ai_usage_ledger, finops_runtime_attempt |
| M10 | c10a8f6e2d41 | finops_usage_inbox | Durable accounting delivery/retry inbox | finops_runtime_attempt |
| M11 | d11b4a8c9e32 | finops_billing_publication | Immutable provider publication manifest | None |
| M11 | d11b4a8c9e32 | finops_billing_ingest_state | Environment/source partition publication pointer | finops_billing_publication |
| M11 | d11b4a8c9e32 | finops_billing_projection_cache | Published normalized billing/pricing projections | finops_billing_publication |
| M12 | e12c6d8a4b50 | finops_reconciliation_input | Versioned reconciliation source snapshots | None |
| M12 | e12c6d8a4b50 | finops_reconciliation_registry | Versioned allocation/ownership rules | None |
| M12 | e12c6d8a4b50 | finops_reconciliation_generation | Immutable reconciliation/allocation result | finops_reconciliation_input, finops_reconciliation_registry |
| M12 | e12c6d8a4b50 | finops_reconciliation_state | Published period generation pointer | finops_reconciliation_generation |
| M13 | f13a7c9d2e61 | finops_unit_definition_version | Versioned unit definitions | None |
| M13 | f13a7c9d2e61 | finops_unit_denominator_snapshot | M9/M10 denominator snapshots | None |
| M13 | f13a7c9d2e61 | finops_unit_driver_version | Versioned allocation/unit drivers | None |
| M13 | f13a7c9d2e61 | finops_unit_economics_generation | Immutable financial/unit result | finops_unit_definition_version, finops_unit_denominator_snapshot, finops_unit_driver_version |
| M13 | f13a7c9d2e61 | finops_unit_economics_publication | Published snapshot/generation pointer | finops_unit_denominator_snapshot, finops_unit_economics_generation |
| M14 | a14d2e6f8c70 | finops_budget_definition_version | Versioned budgets | None |
| M14 | a14d2e6f8c70 | finops_control_input | Known-as-of control inputs | None |
| M14 | a14d2e6f8c70 | finops_forecast_backtest_generation | Immutable forecast backtests | None |
| M14 | a14d2e6f8c70 | finops_control_generation | Published forecast/budget/anomaly result | finops_budget_definition_version, finops_control_input |
| M14 | a14d2e6f8c70 | finops_anomaly_transition | Append-oriented anomaly transition history | finops_control_generation |
| M14 | a14d2e6f8c70 | finops_control_publication | Current selected control generation | finops_control_generation |
| M15 | b15e3f7a9c81 | observability_retention_checkpoint | Bounded retention progress/checkpoint metadata | None |
| M15 | b15e3f7a9c81 | observability_archive_receipt | Sanitized exceptional archive population/retention receipts | None |

M8 introduces no durable SQL objects. Application-level source dependencies (M12 consumes M10/M11; M13 consumes M9/M10/M12; M14 consumes prior financial outputs; M15 indexes M7) also apply even when no physical FK exists. All revisions additionally depend on their graph parent.

## Indexes and constraints

| Milestone | Index | Table / columns | Unique |
|---|---|---|---|
| M7 | ix_observability_active | observability_run_status (environment, status, heartbeat_at, run_id) | No |
| M7 | ix_observability_session | observability_run_status (session_id, started_at, run_id) | No |
| M7 | ix_observability_status_expiry | observability_run_status (expiry_at, run_id) | No |
| M7 | ix_observability_recent | observability_turn (environment, started_at, run_id) | No |
| M7 | ix_observability_summary_expiry | observability_turn (expiry_at, run_id) | No |
| M7 | ix_observability_summary_session | observability_turn (session_id, started_at, run_id) | No |
| M7 | ix_observability_terminal | observability_turn (environment, terminal_at, run_id) | No |
| M9 | ix_sre_due | observability_sre_receipt (settled, due_at) | No |
| M10 | ix_finops_attempt_debt | finops_runtime_attempt (environment, state, deadline_at) | No |
| M10 | ix_finops_bucket_time | finops_usage_bucket (environment, at) | No |
| M10 | ix_finops_ledger_time | ai_usage_ledger (environment, observed_at, event_id) | No |
| M10 | ux_finops_base_attempt | ai_usage_ledger (environment, attempt_id) | Yes |
| M10 | ix_finops_inbox_pending | finops_usage_inbox (delivered, exhausted, next_attempt_at) | No |
| M11 | ix_billing_publication_history | finops_billing_publication (state_key, published_at) | No |
| M11 | ix_billing_state_scope | finops_billing_ingest_state (environment, source, scope, partition) | Yes |
| M12 | ix_reconciliation_inputs | finops_reconciliation_input (environment, scope, period, kind, source_generation) | No |
| M12 | ux_reconciliation_registry_version | finops_reconciliation_registry (environment, version, ownership_version) | Yes |
| M12 | ux_reconciliation_period | finops_reconciliation_state (environment, scope, period) | Yes |
| M13 | ix_unit_snapshot_period | finops_unit_denominator_snapshot (environment, period, source) | No |
| M13 | ux_unit_driver_version | finops_unit_driver_version (environment, scope, version) | Yes |
| M13 | ux_unit_publication | finops_unit_economics_publication (environment, scope, period, kind) | Yes |
| M14 | ix_control_input_asof | finops_control_input (environment, scope, month, cutoff) | No |
| M14 | ix_control_backtest_asof | finops_forecast_backtest_generation (environment, scope, known_at) | No |
| M14 | ix_control_history | finops_control_generation (environment, scope, month, kind, issue) | No |
| M14 | ix_control_anomaly_history | finops_anomaly_transition (anomaly, at) | No |
| M14 | ux_control_publication | finops_control_publication (environment, scope, month, kind, selection) | Yes |
| M15 | ix_archive_receipt_environment_terminal | observability_archive_receipt (environment, terminal_at) | No |
| M15 | ix_m15_run_retention | observability_run_status (environment, expiry_at, run_id) | No |
| M15 | ix_m15_summary_retention | observability_turn (environment, expiry_at, run_id) | No |

31 created tables + 29 explicit secondary indexes (7 unique) + 31 primary-key constraints + 59 CHECK constraints + 20 FK constraints + 2 UNIQUE constraints. PostgreSQL adds backing indexes for 31 PKs and 2 UNIQUE constraints: **62 new indexes total**, assuming no preexisting target objects. Automatic constraint/index names and PostgreSQL NOT NULL catalog entries are not included in the explicit secondary-index count. The complete column/type/nullability/constraint/FK inventory is in M7_M15_TARGET_SCHEMA.json; partial BASE-ledger index predicate is retained in migration-inventory keywords.

Static migration declarations and actual ORM metadata agree on table sets, columns/types/nullability/PKs, check expressions, FKs and unique constraints. Only two migration-defined indexes are absent from ORM metadata: ix_m15_run_retention and ix_m15_summary_retention. Those source-controlled indexes are required; do not delete them via autogenerate. No upgrade/downgrade function executed; only literal SQLAlchemy schema expressions were interpreted into in-memory metadata for comparison.

## Missing objects and expected target

All 31 target tables, their 29 explicit indexes and table constraints are missing. The separate business progression prerequisite is also missing. No new schemas/extensions are declared by M7–M15. Existing public/vector/ADK/knowledge/experience objects must remain intact.

Existing 16 + exactly 31 additive telemetry tables = 47 tables. A tree-complete target also requires the missing business progression table: at least 48 known tables if it is approved and added. The exact reconciled total/final revision cannot be certified without the unknown historical schema and branch resolution. Desired current local head is b15e3f7a9c81; an approved genuine merge could instead introduce a new final revision.

## Required path and recovery decisions

1. Recover the exact missing file and its ancestors from the database deployer’s original checkout/release artifact/CI records or another complete clone; record source provenance and hashes. No restoration performed here.
2. Compare that recovered schema, including columns, keys, indexes, types, extensions and any data transforms, with the read-only shared schema snapshot. If inconsistent, investigate the actual applied operations/stamp separately.
3. Determine whether the recovered revision is an ancestor, a sibling branch or an independently stamped history. Simply restoring a file is sufficient only if existing parent links genuinely reconnect it to the intended target without inventing history.
4. If a real sibling branch is established, author/review an appropriate reconciliation/merge plan after exact structural and data comparison; a merge alone does not prevent replaying already-existing baseline objects. Do not insert the unknown revision between known revisions merely to make Alembic resolve.
5. Independently review missing 7c4e9a2d1f05 business progression prerequisite. It creates a new business-owned table with FK to slopanoc_cases; it is outside the 31-table observability delta.
6. Only after history is resolved: generate actual PostgreSQL offline SQL for the exact start:end range, review every statement, and rehearse against an isolated owner-approved restored clone. **No offline rollout SQL generated now**, because the graph-safety precondition fails.

## Conditional shared rollout — NOT APPROVAL READY

Target: project pr-msn-dev-gl-slopai-01 / europe-west4 / Cloud SQL sloc-anoc-sandbox01 / database slopanoc / public. Environment classification must be confirmed by the owner despite DEV/sandbox naming. Operation would add the approved target tables/indexes/constraints and advance alembic_version; reconciliation may require additional separately reviewed actions. Exact start/operation set remains unresolved.

After provenance/history/SQL/clone/backup gates pass, the intended invocation from the actual repo is `.venv/bin/python -m alembic -c alembic.ini upgrade b15e3f7a9c81`, using the already reviewed target connection. **This command currently fails to resolve d3f8b1c6a942 and must not be executed.** If a genuine merge changes the target, the exact revision/command must be revised before an approval request. No stamp, generic upgrade head or baseline replay is proposed as a shortcut.

- Backup/PITR: owner-verified existing recovery capability; approved pre-change backup if required, recorded restore point, tested restore to a separate instance, recovery-time expectations. Backup creation/restore/configuration also need exact-operation cloud approval.
- Schema inventory: repeat read-only catalog snapshot immediately before/after; preserve release/SQL hashes and known business schema/record-count invariants using approved content-safe checks. Capture business table ownership/consumer inventory. No application content export here.
- Transactions: inspect alembic/env.py (PostgreSQL transactional DDL, outer migration transaction; no AUTOCOMMIT or concurrent index statements currently present). Generated SQL/driver rehearsal must confirm whole-batch atomicity and timeout behavior before live use.
- Locks: CREATE TABLE/index creates new objects; FKs within telemetry add references only to new telemetry tables. M15 normal CREATE INDEX takes SHARE locks on M7 tables; they should be empty/new in an atomic fresh rollout. A separately approved progression FK can lock the existing slopanoc_cases reference table. Do not rerun the timestamp ALTER: it can rewrite/lock existing business rows.
- Runtime: UNKNOWN/unmeasured. Empty additive DDL is expected to be brief, but no duration SLA or safe lock window can be claimed without clone rehearsal and current load inspection. Proposed finite lock/statement timeouts must be reviewed with the owner.
- Runtime/operational impact: telemetry becomes queryable; no producer enabling/model calls/financial jobs included. Some readers may continue to report source unavailability until transaction commit; producer switches stay unchanged. Coordinate deployment only after schema readiness.
- IAM/security: no IAM changes planned. Migration operator must already have specifically approved DDL privileges; runtime reader stays read-only. Do not broaden runtime roles to make the migration run.
- FinOps: DDL adds catalog/storage/index overhead; no billing exports/jobs enabled; actual cost not measured. Backup/clone costs need separate approved review.
- Data risk: additive telemetry delta touches no existing business rows, but unresolved history prevents certifying the complete operation set. Backup and transactional rehearsal required.
- Failure halfway: if rehearsal confirms one atomic PostgreSQL transaction, errors/disconnect before commit roll back DDL and version together. After ambiguous disconnect, inspect version AND objects before retry; never stamp to hide inconsistency. If operations are non-atomic, stop and reconcile completed objects instead of rerunning blindly.
- Post-commit recovery: retain additive tables and roll back the reader/application release when feasible. Do not run destructive downgrades after accounting data exists. PITR restores must be owner-approved and consider unrelated shared writes; preferably restore into a separate instance and recover narrowly. Never drop financial history for convenience.

## Post-approved-rollout verification

Run M7_M15_POST_ROLLOUT_CHECKS.sql as read-only SELECTs. Require the approved final revision, every expected table/index/constraint, correct schema/types and unchanged business/ADK objects. Verify readers still use the same DB, transaction_read_only=on and bounded pools. Do not enable producers just to fabricate test data.

Under existing SRE policy, GET through http://localhost:5173: /api/observability/access, /health, /runs, /active, /slos, /slos/availability. Expect 200 with canonical empty run pages and valid health DTO; persistence=healthy means connectivity, not producer health. SLOs remain unknown/unavailable until durable population/freshness evidence exists.

Under the separate existing FinOps policy (same logical user, no combined ceiling), GET /api/observability/finops/runtime-usage, /ledger-health, /ledger-completeness, /billing-summary, /reconciliation, /allocation, /unit-economics. Expect 200 with genuine empty usage and canonical unknown/unavailable source states, not missing-table 503. Wrong capabilities continue 403. Billing NOT_CONFIGURED is expected without its bindings; existing empty ledger does not prove accounting coverage.

Open /resources/observability and /reliability, then /finops under the correct separate policy; require no access/DTO error, valid empty/source states, one unavailable source not breaking the page. Re-run shared health serialization/frontend validation/reader lifecycle/RBAC/zero-writes suites and build checks. Any missing-table 503 blocks progression.

## Separate blockers

- Billing source = NOT_CONFIGURED: requires separately approved bindings/exports/publication; no schema operation configures it.
- Archive source = NOT_CONFIGURED: requires separately approved read/archive integration; no schema operation deploys an archive source.
- Producer enablement/real population/freshness remains separate; schema creation alone does not produce records.

## Validation and artifact scope

Local Alembic heads/branches/history PASS; AST graph and object inventory PASS (12 revisions; 31 target tables); all-Git-object scan complete; read-only schema snapshot complete; model/migration static cross-check complete. No migration functions called. Initial local analysis scripts needed PYTHONPATH and PostgreSQL bytes normalization; corrected and repeated. No application/build regression run required for documentation/metadata-only work. git diff --check required before final delivery.

Artifacts: M7_M15_MIGRATION_INVENTORY.json (source operations/graph/hashes); M7_M15_TARGET_SCHEMA.json (31 table schemas/purposes/dependencies/model comparison); M7_M15_SHARED_SCHEMA_METADATA.json (catalog metadata only, no rows/credentials); M7_M15_POST_ROLLOUT_CHECKS.sql (SELECT-only verification; **not rollout SQL**). No historical source recovered/restored, no commit, no cloud/database mutation, no M17 work.
