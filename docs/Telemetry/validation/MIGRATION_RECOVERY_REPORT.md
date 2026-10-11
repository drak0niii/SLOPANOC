# Deployment provenance and historical migration recovery

Scope: read-only Git/source/cloud metadata and database catalog inspection. No migration
implementation, migration execution, stamping, deployment, IAM or external mutation.
Current working tree preserved. M16 remains incomplete; M17 not started.

## Recovery result

Exact historical migration recovered YES, independently from fresh remote Git and the
sibling workspace's Git object database. No recovered migration was inserted into the
current `alembic/versions` directory. Bodies and hashes retained as forensic JSON only.

- Repository: https://github.com/drak0niii/SLOPANOC.git
- Introducing commit: `1f1cbccd2203f3889af0e2c0cf0ae1d750592b83`
- Author timestamp: 2026-09-13T22:46:28+03:00 (Git author timestamp, not deployment time).
- Branches retaining it: second, Third, audit.
- Path: `alembic/versions/d3f8b1c6a942_experience_records_source_namespace.py`
- Blob: `c72d3fd3401f6008126cf2e997c1b7ae10483ecf`
- revision: `d3f8b1c6a942`; down_revision: `c7e2a4f9b83d`.
- Upgrade: adds `slopanoc_experience_records.source_namespace VARCHAR NOT NULL`,
  no server default, backfill or index. Downgrade drops that column (destructive).
- Migration source says the live table had zero rows when applied. That historical
  assertion is not a current row-count measurement and does not authorize replay.
- Image/build/deployed commit: UNVERIFIED, not inferred from a source commit.

## Exact commands and results

`git remote get-url origin`: configured repository above; current tree's refs untouched.
`git ls-remote origin > /private/tmp/slopanoc-remote-refs.txt`: successful fresh metadata.
`git clone --mirror https://github.com/drak0niii/SLOPANOC.git /private/tmp/slopanoc-recovery-remote.git`:
successful isolated mirror. Seven advertised branches, three tags. The full advertised
refs and exact SHAs are retained in MIGRATION_RECOVERY_SOURCES.json.
`git cat-file --batch-all-objects --batch-check=...` plus `git cat-file --batch`:
2046 remote blobs scanned for the exact revision; migration blob recovered.
`git log --all --diff-filter=A --format='%H %aI %s' -- '*d3f8b1c6a942*'`:
introducing commit above. `git branch --contains <commit>`: Third, audit, second.
`git worktree add --detach /private/tmp/slopanoc-recovered-source <commit>`:
isolated historical source inspected; this is a candidate source commit, not a proven
Cloud Run deployed commit. All advertised reachable commit history inspected; deleted
server refs, unavailable private refs and GitHub's server reflogs cannot be certified.

Local source walk: Desktop/SLOPANOC, Desktop/Kit Backup, Desktop/SLOP Innovation AI,
Downloads/SLOPANOC_POC_HARDENING_TRANCHE1, Downloads/SLOPANOC-docs-rebuilt.
Ignored dependency/build/Git folders during source-file walks. Inspected source text
in SLOPANOC-main.zip, SLOPANOC_POC_HARDENING_TRANCHE1.zip, SLOPANOC-docs-rebuilt.zip.
27 migration-related paths found, no exact revision in those working files/archives.
Sibling SLOPANOC-main2 has four baseline/progression migration files; its Git all-object
scan examined 10884 blobs and recovered the same exact historical migration via
origin/second, origin/Third, origin/audit. No sibling files or Git metadata changed.

## Cloud provenance and images

Exact project for all requests: `pr-msn-dev-gl-slopai-01`.
Read-only commands attempted:

```sh
gcloud run services list --project=pr-msn-dev-gl-slopai-01 --platform=managed --format='json(metadata.name,metadata.namespace,metadata.creationTimestamp,metadata.labels,status.url,status.latestReadyRevisionName)'
gcloud artifacts repositories list --project=pr-msn-dev-gl-slopai-01 --location=all --format='json(name,format,createTime)'
gcloud builds list --project=pr-msn-dev-gl-slopai-01 --region=europe-west4 --limit=100 --format='json(id,createTime,source,results.images,substitutions.COMMIT_SHA)'
docker version --format '{{json .Server.Version}}'
```

All three gcloud reads failed during token refresh: reauthentication failed; cannot
prompt during non-interactive execution. No login/config change/API enable attempted.
Consequently Cloud Run service/revision names, timestamps, images/digests, build labels,
source archives, Artifact Registry tags/provenance and Cloud Build executions were NOT
verified. Global/other-region builds and revision details were not queried after the
shared authentication failure. Do not interpret unavailable metadata as no resources.
Docker CLI exists; daemon socket `/Users/costi/.docker/run/docker.sock` unavailable.
IMAGE CONTENT NOT INSPECTED. No daemon started, tooling installed, images pulled,
containers created, application launched or shared-service connections from images.

## Historical migration chain

Recovered complete applied chain, one root and one head at the recovered source:

`d2081e4fd455 -> b85242503972 -> 3e59584b1012 -> 9b6df6490c0e -> a1f3c9e07b21 -> c7e2a4f9b83d -> d3f8b1c6a942`

- d208: five baseline case/knowledge tables.
- b852: six case timestamps made timezone-aware.
- 3e59: attachment table, checks and indexes.
- 9b6d: TELCO profiles and assertions.
- a1f: evidence index, pgvector extension and three indexes (including GIN FTS).
- c7e: experience table and five retrieval indexes.
- d3f: experience source_namespace column.

Third branch also contains descendants, introduced by
`63e7256b1589f67bc28fe354ec388d7bde4cbbfd` (2026-09-17 author date):
`d3f -> e4a7c2b91d38 -> f1b6c3d05a27 -> b7c4e1a95d60`.
They add assertion supersession/retraction, operation approvals, operation claims.
Their columns/tables are absent from this live snapshot; they are NOT implied rollout
prerequisites simply because they are historical descendants. No existing migration
connects d3 to the current telemetry branch. Ancestors/descendants are recovered,
but an integrated graph connecting applied history to current target is not present.

## Live schema fingerprint and chronology

Catalog SELECTs ran through existing ReadDatabase; transaction_read_only=on confirmed.
PostgreSQL 18.4, database slopanoc, schema public. Revision exactly d3f8b1c6a942.
16 tables / 125 columns / 29 indexes / 119 catalog constraints (including 92
PostgreSQL NOT NULL entries). No user views/triggers. pgvector=0.8.5; plpgsql=1.0.
Normalized full per-table columns/types/nullability/defaults, PK/FK/indexes/constraints
are retained in MIGRATION_RECOVERY_SCHEMA_fca850228ccaed481dbd3fa31fd785cca494aa36122eefe0f7e1b1b277df9d09.json. SHA-256:
`fca850228ccaed481dbd3fa31fd785cca494aa36122eefe0f7e1b1b277df9d09`.

All ten application tables declared by the historical chain match the live column
names, types, nullability, defaults, declared PK/FK/UNIQUE/CHECK constraints and
secondary indexes. Parentheses emitted by pg_get_constraintdef for the five attachment
CHECKs were normalized by rendering expected parentheses equivalently, not waived.
The five ADK-managed tables and Alembic version table are separately retained in the
fingerprint and not claimed to be created by these application migrations.

Historical ORM comparison is separately recorded in MIGRATION_RECOVERY_MODEL_COMPARISON.json:
four TELCO ORM timestamps are timezone-aware but recovered migration/live columns are
naive; evidence_id and knowledge_id ORM types are VARCHAR while migration/live types
are TEXT. These are explicit declaration differences, not grounds for schema mutation.
Current ORM's six shared baseline/attachment tables match historical columns; current
case ORM adds missing progression. Current source no longer contains historical TELCO,
experience and hybrid retrieval model modules. No autogeneration was run.

Historical docs/KNOWLEDGE_CONTRACT.md sections 30.10.1/30.11 explicitly describe the
real DEV source_namespace migration; docs/MASTER_ROADMAP.md records the Sept 13 freeze
with DB at d3. This corroborates revision/source history, but is not independently
verified deployment evidence or an exact table CREATE timestamp. PostgreSQL catalogs
do not supply reliable table creation timestamps; none are invented here.

## Business progression and current graph

Separate missing business prerequisite: `7c4e9a2d1f05`, parent `3e59584b1012`, introduced
by commit `67680e1bd492af0a3e09c3a03c235b84a9c51b5d` on Oct 6. Migration header Sept 30
is author-declared creation date, not an observed deployment timestamp.
Creates slopanoc_case_troubleshooting_progressions, PK case_id, FK slopanoc_cases.case_id,
CHECK version>=1, seven columns. It predates M7 and is M7's parent. Absent from the
recovered Sept 13 historical tree; no evidence it belonged to the applied d3 branch.
It exists in the sibling working tree and current graph, but is missing in the DB.

Common Alembic ancestor is `3e59584b1012`. Historical child is 9b6d; current child is
7c4e. Current graph continues `7c4e -> 9f71c2a64e08 (M7) -> b37e90a14c62 (M9) ->
c10a8f6e2d41 (M10) -> d11b4a8c9e32 (M11) -> e12c6d8a4b50 (M12) -> f13a7c9d2e61
(M13) -> a14d2e6f8c70 (M14) -> b15e3f7a9c81 (M15)`. M8 has no SQL revision.
This is a genuine missing historical branch in the current source, not evidence of
an incorrect stamp. Schema compatibility does not prove stamp safety.

## Ranked reconciliation design (NOT implemented)

1. Safest: restore exact four historical branch migration sources with original hashes
   and IDs in a later authorized local reconciliation change, preserving existing
   current parents. Review an explicit Alembic merge between d3 and current target.
   Already-applied historical ancestors must not be replayed. Forward traversal from
   d3 should create the separately reviewed progression table and 31 telemetry tables,
   then merge heads. Preserve the existing ten business tables and five ADK tables.
   Do not include Third's unrelated descendants without a separate architecture decision.
2. Honest bridge/reconciliation revision only if restoring the graph is impractical.
   Must retain genuine d3 history and prove pre/postconditions, branch ordering and
   compatibility. Never fabricate d3, mark work applied that was skipped, silently
   rewrite applied parents, or conceal unexpected schema differences.
3. Controlled baseline/stamp: least safe and currently unnecessary given exact source
   recovery; requires independent proof every bypassed operation is represented,
   none is skipped, no destructive mismatch, correct dependencies and rollback.
   Schema similarity alone does not satisfy any stamping gate.

Before any rollout-ready request: construct/review the integrated graph locally;
verify original/common migration source identity; generate/review forward-only SQL
using isolated configuration (historical env.py ignores alembic Config URL); rehearse
on a clearly isolated disposable PostgreSQL database matching the fingerprint;
verify all 32 missing objects plus all constraints/indexes, ADK/business preservation
and RBAC/read-plane contracts; evaluate backups/PITR, locks/privileges and accounting
safety; resolve cloud provenance gap or explicitly record its operational limitation;
then present exact target/revision/operations/impact/rollback approval gate. No rollout
command is certified or executed in this pass. Unmodified current graph still cannot
resolve d3; recovered source alone does not make that command safe.

## Validation and boundaries

PASS: exact revision/body/parent recovery in fresh remote and sibling Git; seven-node
historical graph traversed without missing parents; ten application migration schemas
compared with live read-only snapshot; normalized 16-table fingerprint generated;
models separately compared with differences recorded; business prerequisite separated.
BLOCKED: Cloud Run/Artifact Registry/Cloud Build provenance (authentication), image
filesystem inspection (Docker daemon), integrated local graph/rollout (not implemented).
No application source or migration source files changed. No runtime tests needed for
forensic-only artifacts. JSON parsing/structural assertions and git diff check required.
Cloud / Database Changes Performed: NONE.
Safe To Prepare Migration Rollout: NO (recovery succeeded; integrated graph and rehearsal
remain unimplemented). M16 remains incomplete. Ready for Next Milestone: NO.
