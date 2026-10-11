# Phase-1 pre-migration recovery requirements — review only

Target: **development**, `pr-msn-dev-gl-slopai-01`, Cloud SQL
`sloc-anoc-sandbox01`, existing database `slopanoc`, `public` schema.
Read-only metadata on 2026-10-11: PostgreSQL 18 / Enterprise, automated backups disabled,
PITR disabled, retainedBackups=7 COUNT (ineffective while backups disabled), configured
transactionLogRetentionDays=7 (not evidence of retained logs). Read-only backup listing
returned no entries. No recovery point is verified. No instance/database data copied.

## Required before any shared migration

1. Obtain exact approval for an on-demand **standard full Cloud SQL instance backup**
   of the existing instance. Record backup ID, successful completion, source instance,
   timestamps, location, retention and restore permissions. An export of selected
   tables or a local schema-only rehearsal is not a recovery backup.
2. Verify the backup is usable by an independently approved restore to an isolated
   recovery instance; validate schema, revision, application integrity and restricted
   access. Do not copy customer content into development/test receipts. Record metadata
   and checks only. Confirm restore RTO and recovery point with the owner.
3. Proposed retention expectation: keep the pre-migration backup at least 30 days and
   until post-rollout acceptance/rollback window closes, whichever is later. Do not
   auto-delete it. Recommend at least 7 successful daily backups plus the retained
   pre-migration backup. These are proposed policy values, not current configuration.
4. Recommend separately approved PITR with 7-day transaction-log retention and verified
   available recovery window. Enabling/disabling PITR restarts the instance; schedule
   interruption, connection recovery and cost/storage review before exact approval.
   No PITR setting is changed here. If PITR is declined, document the potentially lost
   writes since backup and require owner acceptance before migration.

[Google instance settings](https://docs.cloud.google.com/sql/docs/postgres/instance-settings)
documents the restart; [backup overview](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/backups)
and [PITR configuration](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/configure-pitr)
provide the service recovery model.

## Migration and abort checkpoints

Before start: exact database/version/schema/grants/extensions and sole revision verified;
no concurrent migrations; tested PostgreSQL 18 graph; recovery backup/restore verified;
approved maintenance window; current metrics baseline; no hybrid activation. Stop if
any mismatch, missing recovery, insufficient privilege/storage, unknown locks or a
backup is still pending/failed. Historical d3 files remain unchanged.

Proposed revision path: `d3f8b1c6a942` -> existing observability branch -> merge
`c16f4a8b2d90` -> additive hybrid `d26a1b7c9e40`, 18 graph revisions. No new migration
in this pass. Review the exact forward SQL before approval. Set bounded session lock
and statement timeouts with database-owner review; abort rather than wait indefinitely.
During migration, errors/timeout, blocked business requests or unexpected DDL/grants
require stopping subsequent revisions and checking actual committed revision/schema.
PostgreSQL transactional DDL rolls back its failed transaction; never assume all prior
revisions rolled back without inspecting the database.

After commit: verify all expected tables/indexes/FKs/checks, revision and effective role
membership, restricted-role behavior, unchanged ADK ownership, app startup and normal
business flow. Keep capture/publisher/projector disabled if validation fails. Prefer
forward repair and restore previous service revision/config; retain additive tables
and any queued audit records. Never destructive downgrade as automatic recovery.

## Restore / incident procedure (requires separate exact approval)

Freeze affected writes and stop producers/workers under incident authority. Identify
last known good backup or available PITR timestamp **before** migration/incident and
record expected loss of intervening writes. Restore to a separately approved isolated
Cloud SQL recovery instance using that backup ID or PITR source/timestamp; do not
silently overwrite the existing instance. Restrict access, verify schema/data integrity,
revision and application checks, assess RPO/RTO, then separately approve credential/
network/endpoint cutover and restart. Retain the damaged instance for investigation.

Financial history and critical audit/outbox must be reconciled for writes after the
restore point. Rebuild projections from validated immutable source references; receipt
and hash dedup must match retained source. Broker redelivery must never rerun commands,
approvals, model calls or accounting business operations. An ambiguous external action
remains UNKNOWN pending authoritative downstream reconciliation. Restore is not an
automatic tool retry. Roll back service cutover to the retained original only after
owner validation. Every backup/PITR/restore/cutover operation needs its exact ten-point
approval; this document and local rehearsal grant none.
