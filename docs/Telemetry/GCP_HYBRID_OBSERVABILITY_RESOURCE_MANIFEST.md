# GCP Hybrid Observability Resource Manifest

Review proposal, 2026-10-11. **DO NOT CREATE THEM. No resources were created or
modified.** PHASE 1 MINIMUM = the authoritative audit pipeline + existing reader.
Operational Telemetry Pub/Sub is **OPTIONAL / SCALE-TRIGGERED**, **DISABLED**,
limited to a future curated measured subset. PHASE 2 = expanded production audit,
history and financial infrastructure. These groups do not advance M16 or M17.

## Exact proposed development binding

Project: **`pr-msn-dev-gl-slopai-01`**, environment: **`development`**;
verified existing Cloud SQL region and recommended Cloud Run region: **`europe-west4`**.
Read-only describe confirmed this on 2026-10-11; project number **995771529960**. This is a concrete
proposal using the previously inspected sandbox candidate, **not owner-confirmed
activation binding or an approval**. Existing SQL instance: **`sloc-anoc-sandbox01`**,
database **`slopanoc`**, schema **`public`**; reuse after verification. No new SQL
instance/database is proposed. Production project/account/tenant is unconfirmed and
has no create proposal in this pass. Confirm target identity, network, project number,
resource ownership/name availability and recovery prerequisites before approval.

All IAM account emails below use `@pr-msn-dev-gl-slopai-01.iam.gserviceaccount.com`.
Pub/Sub topics/subscriptions are global resources; proposed message storage policy
allows europe-west4. Review region endpoints/data residency independently; do not
assign a fictitious topic region. Every row below uses the exact project above.
Source-controlled Terraform is required where supported, but this pass creates no
Terraform resources/state and performs no apply/import/API enablement/grants.

## PHASE 1 MINIMUM — authoritative audit only

PHASE 1 REQUIRED infrastructure for future activation, **not currently deployed**:

| Resource type | Exact proposed name | Location | Identity / IAM |
|---|---|---|---|
| Service account | `slopanoc-development-outbox` | Project/global | Publisher only; topic `roles/pubsub.publisher`, conditional project `roles/cloudsql.client` restricted to the existing instance; approved DB role below. No keys. |
| Service account | `slopanoc-development-projector` | Project/global | SQL connection only: conditional project `roles/cloudsql.client` restricted to the existing instance; approved projection DB role. No model/tool/command permissions. |
| Service account | `slopanoc-development-reader` | Project/global | SQL connection only: conditional project `roles/cloudsql.client` restricted to the existing instance; select-only DB role. Preserve separately reviewed archive/financial reads. |
| Service account | `slopanoc-development-push` | Project/global | `roles/run.invoker` on projector only; no SQL or ANOC access. |
| Pub/Sub topic | `slopanoc-development-audit-events-v1` | Global; storage europe-west4 proposed | Outbox SA publisher on this topic only. Safe v1 audit envelopes; no OTLP. |
| Pub/Sub topic | `slopanoc-development-audit-dlq-v1` | Global; storage europe-west4 proposed | Pub/Sub service agent publisher; inspection operators subscriber only on inspection subscription. |
| Pub/Sub authenticated push subscription | `slopanoc-development-audit-projector-v1` | Global | Audit topic → projector `/events`; push SA/OIDC audience = approved projector URL. Pub/Sub service agent subscriber on source subscription. |
| Pub/Sub DLQ inspection subscription | `slopanoc-development-audit-dlq-inspect-v1` | Global | Audit DLQ; designated restricted operator group `roles/pubsub.subscriber` scoped to this subscription. No automatic replay. |
| Cloud Run service | `slopanoc-development-outbox-publisher` | europe-west4 | Outbox SA. Separate serial publisher, bounded batch/leases. Background CPU allocation/min-instance choice must be priced before activation. |
| Cloud Run service | `slopanoc-development-observability-projector` | europe-west4 | Projector SA. Verified authenticated ingress only; push identity sole broker invoker. SQL projection writes only. |
| Cloud Run service | `slopanoc-development-observability-reader` | europe-west4 | Reader SA. Existing independent GET app; approved first-party verified principal ingress and UI base URL. No producer/worker startup. |
| Existing Cloud SQL schema, additive migration | `sloc-anoc-sandbox01/slopanoc/public`, revision `d26a1b7c9e40` after `c16f4a8b2d90` | europe-west4 | Separately authorized migration principal, not worker identities. Six tables/indexes listed below. |
| PostgreSQL role | `slopanoc_hybrid_publisher` | Existing database | SELECT/UPDATE `observability_audit_outbox`; no business/audit/ledger writes. |
| PostgreSQL role | `slopanoc_hybrid_projector` | Existing database | SELECT/INSERT/UPDATE receipt, failure, run projection, agent projection only. No business authority/financial writes. |
| PostgreSQL role | `slopanoc_hybrid_reader` | Existing database | SELECT approved hybrid outbox status + receipt/failure/run/agent projections, preferably safe views; no DML. Existing approved read grants stay separately reviewed. |
| Existing ANOC DB-role grant | Existing ANOC role, identity must be verified before commands | Existing database | SELECT/INSERT critical audit + outbox in existing owning M7/M10 transactions; no grant of approvals/command authority. |

No new Collector, OTLP topic, telemetry subscription, operational DLQ, archive bucket,
BigQuery dataset, billing export, financial processor, alert evaluator, monitoring
dashboard or scheduled financial job is a Phase-1 minimum addition. Reuse existing
reader/SA/topic resources if verification shows these names already exist.

New migration tables: `observability_critical_audit`, `observability_audit_outbox`,
`observability_projection_receipt`, `observability_projection_failure`,
`observability_run_projection`, `observability_agent_projection`. The reconciliation
merge and its existing M7–M15 prerequisites must be validated independently first.
No shared migration, database role/user/grant or table operation has been performed.
SQLite local proof does not qualify PostgreSQL online locks/WAL/backup behavior.

### Platform identities and narrowly scoped IAM prerequisites

Dedicated push identity email:
`slopanoc-development-push@pr-msn-dev-gl-slopai-01.iam.gserviceaccount.com`.
Pub/Sub service agent identity has the Google format
`service-995771529960@gcp-sa-pubsub.iam.gserviceaccount.com`.
Project number was verified read-only; exact-operation approval is still required. Required scoped intent:
service agent `roles/iam.serviceAccountTokenCreator` on the push SA; subscriber on
audit projector subscription; publisher on audit DLQ topic. Review existing grants
before adding duplicates. No project Editor/Owner or service-account key is proposed.
Cloud SQL client IAM is a **project policy binding with an instance-restricting
condition**, not an instance IAM-policy write. Validate the condition and connector
access before activation; broad unconditioned project access is not proposed. See
[Cloud SQL IAM Conditions](https://docs.cloud.google.com/sql/docs/postgres/iam-conditions).
Push token creation / invocation and DLQ forwarding requirements were checked against
[authenticated push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions)
and [dead-letter topic IAM](https://docs.cloud.google.com/pubsub/docs/dead-letter-topics)
on 2026-10-11; no grants were applied.
DB login strategy (separate reviewed credential or IAM DB user/instanceUser) remains
an activation design input; `cloudsql.client` alone grants no table privileges.

Existing API prerequisites to verify read-only: `pubsub.googleapis.com`,
`run.googleapis.com`, `sqladmin.googleapis.com`, `iam.googleapis.com`,
`logging.googleapis.com`, `monitoring.googleapis.com`; `secretmanager.googleapis.com`
only where the approved credential binding needs it. Do not enable any implicitly.
Artifact/image delivery uses the existing approved build registry, not a new
unspecified resource. Separate secret grants `roles/secretmanager.secretAccessor`
would apply only to each approved existing workload credential secret, not all secrets.
Credential secret names/versions must be verified before an exact remote proposal.

### Configuration required before activation

These are **proposed production bindings**, not a claim that hybrid environment
parsing/Pub/Sub adapters/authenticated ingress are implemented. Local code consumes
explicit `HybridConfig` and injected sessions/transport only. Disabled by default.

| Workload | Exact configuration keys / proposed values |
|---|---|
| All hybrid workloads | `SLOPANOC_HYBRID_ENVIRONMENT=development`, `SLOPANOC_HYBRID_PROJECT=pr-msn-dev-gl-slopai-01`, `SLOPANOC_HYBRID_AUDIT_TOPIC=projects/pr-msn-dev-gl-slopai-01/topics/slopanoc-development-audit-events-v1`, `SLOPANOC_HYBRID_OPERATIONAL_PUBSUB_ENABLED=false`. |
| ANOC capture | `SLOPANOC_HYBRID_CAPTURE_ENABLED=false` until additive schema/table grants/owner tests are approved; existing SQL URL stays private. Explicit runtime capture factory binding still required for cloud activation. |
| Publisher | `SLOPANOC_HYBRID_AUDIT_PUBLICATION_ENABLED=false` initially; `SLOPANOC_HYBRID_PUBLISHER_BATCH_SIZE=100`, `SLOPANOC_HYBRID_PUBLISHER_LEASE_SECONDS=30`, `SLOPANOC_HYBRID_PUBLISHER_MAX_ATTEMPTS=10`, `SLOPANOC_HYBRID_RETRY_INITIAL_SECONDS=1`, `SLOPANOC_HYBRID_RETRY_MAX_SECONDS=60`, `SLOPANOC_HYBRID_SHUTDOWN_SECONDS=3`, `SLOPANOC_HYBRID_DATABASE_URL=<approved publisher credential binding>`. |
| Projector | `SLOPANOC_HYBRID_PROJECTOR_ENABLED=false` initially; `SLOPANOC_HYBRID_PROJECTOR_MAX_INFLIGHT=32`, `SLOPANOC_HYBRID_PROJECTOR_DEADLINE_SECONDS=10`, `SLOPANOC_HYBRID_DATABASE_URL=<approved projector credential binding>`, `SLOPANOC_HYBRID_PUSH_AUDIENCE=<approved service URL>`, `SLOPANOC_HYBRID_PUSH_IDENTITY=slopanoc-development-push@pr-msn-dev-gl-slopai-01.iam.gserviceaccount.com`. |
| Existing reader (implemented keys) | `SLOPANOC_OTEL_ENVIRONMENT=development`, `SLOPANOC_OBSERVABILITY_READ_DATABASE_URL=<approved select-only credential binding>`, `SLOPANOC_OBSERVABILITY_READ_PRODUCER_POLICY_FILE=/app/config/observability-producer-policy.json`, `SLOPANOC_OBSERVABILITY_READ_POOL_SIZE=2`, `SLOPANOC_OBSERVABILITY_READ_POOL_TIMEOUT=1`, `SLOPANOC_OBSERVABILITY_READ_READ_DEADLINE=2`, `SLOPANOC_OBSERVABILITY_READ_READ_CONCURRENCY=4`, `SLOPANOC_OBSERVABILITY_ALLOWED_ORIGINS=<approved first-party HTTPS origin>`. Existing published financial/source scopes remain separately bound. |
| Reader authentication / frontend | Production verified principal provider must be explicitly installed; do not use local dev headers as production identity. Current default auth is disabled/fail-closed. `VITE_OBSERVABILITY_API_BASE_URL=<approved reader HTTPS URL>`. No fake unverified production JWT configuration is invented. |

Cloud Run-generated URLs, first-party origin, exact secret bindings and DB-role
mapping remain unconfirmed. These placeholders contain no credentials and are
explicit blockers to exact deployment commands. The local fake queue depth is
128, not a Pub/Sub durability guarantee. Proposed broker controls to price/review:
7-day audit/subscription retention, 7-day DLQ retention, retry 10–600s, 10 delivery
attempts and 30s ACK deadline. These are proposals, not validated settings or SLOs.
No exactly-once delivery or global ordering requirement. Invalid/conflict quarantine
mapping must be tested in the future authenticated production adapter.

### Dependency, cost, risk and recovery review

Creation/activation sequence: verified bindings/recovery/API inventory → dedicated
identities and scoped grants → topics/subscriptions → disabled workload definitions
and secure DB/network configuration → separately approved additive migration/DB grants
→ verified transport/auth ingress → separately approved capture/consumer activation.
Subscription endpoint cannot safely consume before schemas/auth/atomic receipts exist.
Existing backup/PITR and reconciliation validation blockers still apply.

Cost drivers: audit event volume/size, broker retained backlog/DLQ, Cloud Run allocated
CPU/min instances, SQL connections/WAL/index/storage/write contention, reader requests.
There is no numeric EUR forecast without measured production traffic/retention/service
pricing; focused local timings are not a cloud cost estimate. Preserve ANOC connection
and CPU capacity. Cap concurrency/instances/pools using measured load before activation.

Expected data-loss risk: deleting audit/outbox/receipt tables or undrained topics loses
history and can defeat dedup; never use automatic destructive rollback. Recovery:
disable capture/workers, retain additive tables/outbox, restore approved service
revision/config, drain/replay safe events under an explicit procedure after recovery
validation. Broker retries never rerun business work. Migration downgrade is destructive
and is not a proposed production rollback. Every individual external mutation still
requires the ten-point exact user approval gate in AGENTS.md.

## OPTIONAL / SCALE-TRIGGERED

Only after a measured curated operational use case: topic
`slopanoc-development-telemetry-events-v1`, subscription
`slopanoc-development-telemetry-projector-v1`, DLQ topic
`slopanoc-development-telemetry-dlq-v1`, inspection subscription
`slopanoc-development-telemetry-dlq-inspect-v1`, safe subset adapter and isolated
telemetry capacity/IAM. **Disabled, no full unsampled OTLP replication requirement.**
No Phase-1 Collector runtime change or new Collector identity is required.
Optional read-only SSE/stream, scheduler, expanded time buckets/alerts and autoscaling
require evidence and independent approval. They are not locally implemented here.

## PHASE 2

PHASE 2 REQUIRED for the approved full production target where not already deployed:
reviewed pgAudit flags/extension/masking/access/retention and restart; GCP Data Access
Audit Logs; exceptional GCS archive infrastructure; BigQuery Detailed Billing/Pricing
exports and M11–M14 processing; reconciliation/allocation/budgets/forecasts; production
alert policies/dashboards/SLO delivery; retention/dedup cleanup and rebuild runbooks;
profiling and infrastructure observability. FOCUS supplementary, EUR primary.
Governance lifecycle event expansion requires real canonical durable owner transactions,
not telemetry inference. Separate proposals must name concrete resources/accounts.

## Status

Local Phase-1 SQL/worker/fake-transport/read contracts: **IMPLEMENTED AND LOCALLY
VALIDATED**. Production Pub/Sub adapter/OIDC boundary, PostgreSQL/live schema, secure
bindings, cost sizing and full M16 acceptance: **NOT VALIDATED / PENDING**.
Cloud deployment: **NOT EXECUTED — USER APPROVAL REQUIRED**.
**Ready for Phase-1 GCP provisioning: NO. M16 INCOMPLETE. M17 UNSTARTED.**

## Production-readiness resource classification (current)

| Classification | Exact resources / prerequisite |
|---|---|
| MUST CREATE FOR PHASE 1 | The two named audit/DLQ topics, two named push/inspection subscriptions, four named workload/push accounts, three named Cloud Run services, resource-scoped IAM grants in the Phase-1 table above |
| ALREADY EXISTS / REUSE | Project pr-msn-dev-gl-slopai-01 (number 995771529960), Cloud SQL sloc-anoc-sandbox01 in europe-west4 verified read-only; existing slopanoc database name verified read-only; public schema/login bindings still require owner confirmation; existing ANOC service, image registry/network/secure secret infrastructure after binding review |
| LATER | Full cross-domain audit ownership, reconstruction/retention worker, advanced financial resources already classified Phase 2, profiling expansion, pgAudit extension/configuration and additional dashboards |
| OPTIONAL | Operational telemetry Pub/Sub curated subset/adapter, Cloud Run worker pool alternative; neither required alongside minimum three services |

Read-only filtered listings returned no proposed development audit topic/subscription,
service/account names. Syntax limits validated locally; no reservations performed.
Minimum creation excludes a new SQL instance/database, BigQuery datasets, billing
exports, pgAudit, scheduler/jobs, new Collector or operational Pub/Sub lane.
Existing API/secret/image prerequisites are not assumed missing or added as mandatory
creates. Dedicated credentials must reference approved secret versions; no new secret
resource is defined by inference. Future manual creation must be reconciled with IaC
ownership under separately approved adoption/import; Terraform definitions do not
implicitly authorize it.

[Exact configuration/runtime/authentication and remaining blockers](HYBRID_PHASE1_PRODUCTION_READINESS.md).
[Review-only SQL roles](validation/HYBRID_PHASE1_DATABASE_ROLES.sql).
Local production components now implemented; old proposal matrix is superseded by
the exact executable configuration matrix in the readiness document. Provisioning
NO pending PostgreSQL rehearsal/recovery and verified deployment/identity bindings.
Cloud/shared DB changes NONE. M16 INCOMPLETE; M17 UNSTARTED.

## Confirmed without provisioning — audit review

Region `europe-west4`; existing SQL connection
`pr-msn-dev-gl-slopai-01:europe-west4:sloc-anoc-sandbox01`; database `slopanoc`;
read-only metadata PostgreSQL 18/Enterprise, backups and PITR disabled, no backup
entries. Proposed topic/subscription/service/account names above remain exact and
uncreated. Use the executable configuration matrix in the production-readiness
document (legacy proposal key names above are not executable settings).

Projector push audience must be the exact future Cloud Run HTTPS service origin
`https://<service-generated-host>` with no endpoint path, using
`SLOPANOC_HYBRID_PUSH_AUDIENCE`; endpoint adds `/internal/observability/events/audit`.
Push subject uses `SLOPANOC_HYBRID_PUSH_SERVICE_ACCOUNT`, capture uses
`SLOPANOC_HYBRID_AUDIT_ENABLED`, publisher uses `SLOPANOC_HYBRID_PUBLISHER_ENABLED`.
Do not guess a generated hostname. Human direct-IAP assertion audience is the distinct
fixed resource string `/projects/995771529960/locations/europe-west4/services/slopanoc-development-observability-reader`.
Reader first-party HTTPS UI origin, image digest and SQL credentials remain exact
binding blockers, not invented values. [Identity contract](HYBRID_READER_IDENTITY_CONTRACT.md).
Local PostgreSQL rehearsal PASS; cloud provisioning NO.
