# Phase-1 production readiness — local implementation, no provisioning

2026-10-11. M16 INCOMPLETE; C41/C42 not PASS; M17 UNSTARTED. Cloud/shared DB
changes NONE. This document supplements doc 26 without replacing its Mermaid or
approved two-lane architecture. Operational telemetry Pub/Sub remains OPTIONAL,
SCALE-TRIGGERED, DISABLED; full unsampled OTLP replication is not Phase-1 minimum.

## Real production composition

ANOC sole lifespan reads HybridConfig.from_environment and capture_for. Default
`SLOPANOC_HYBRID_AUDIT_ENABLED=false` causes no capture, outbox DML, publisher,
projector or Google client. Enabled capture requires matching environment and existing
M7 projection + M10 accounting flags; invalid required-owner construction fails startup.
It injects Capture into the actual M7/M10 repositories and existing CaseProgressionStore.
No alternative business runner, private ADK engine access, DB schema creation,
provider invocation, evidence selection, authority or command replay was introduced.
Case store retains its existing schema/persistence lifecycle; migration provisioning
must precede activation. Capture failure rolls back its owning SQL transaction.

Separate entry points (same reviewed application image):

```
python -m uvicorn backend.observability.hybrid.production:create_publisher_app --factory --host 0.0.0.0 --port 8080 --no-access-log
python -m uvicorn backend.observability.hybrid.production:create_projector_app --factory --host 0.0.0.0 --port 8080 --no-access-log
python -m uvicorn backend.api.observability_app:create_app --factory --host 0.0.0.0 --port 8080 --no-access-log
```

Factories are inert by default. Worker DB URLs are dedicated and never fall back to
ANOC business credentials. No startup migration or resource-administration client.
Publisher shutdown stops bounded worker then Google transport then pool; projector
shutdown closes admission, drains bounded requests, then pool. Reader stays GET-only.

## Google transport and message contract

Google-supported async GAPIC PublisherAsyncClient, pinned installed compatible
`google-cloud-pubsub==2.42.0`, google-auth 2.60.0. Client/ADC discovery is lazy at
first enabled publish. Dedicated publisher account can publish to one topic only.
No create_topic/subscription or IAM/admin calls. Topic requires exact configured
project/environment name. Semaphore rejects excess active admission, request deadline
2 s by default, finite transient AsyncRetry budget, no ordering/infinite retry.
Outbox retry is separate durable exponential backoff with terminal visible debt.

Body: strict version-1 AUDIT envelope ≤16 KiB, safe references only. Attributes are
exact event_id/schema_version/event_type/environment and present run_id/turn_id/trace_id,
all bounded by the typed schema; no arbitrary content. Broker messageId is a delivery
identity; event_id is the canonical deterministic idempotency identity. New optional
payload session/attempt/tool/approval/action reference fields are absent when unknown,
preserving legacy v1 digests when absent. Deploy new readers/projector before producing
new optional fields/events; an older strict catalogue must not be left consuming them.

[Google publisher reference](https://docs.cloud.google.com/python/docs/reference/pubsub/latest/google.cloud.pubsub_v1.publisher.client.Client).

## Request-driven push and authentication

Only projector exposes `POST /internal/observability/events/audit`. It is absent from
public/read-only API. IAM-protected Cloud Run grants run.invoker only to the dedicated
push account. All-users grants are prohibited. Application additionally verifies the
Google JWT signature, expiry, issuer and exact HTTPS audience using verify_oauth2_token,
then requires email_verified true + the configured service-account email. Certificate
request timeout 2 s; admission bounded even across caller cancellation. Certificate
transport/capacity failures return 503, invalid credentials return 401. No email/role/capability/browser header auth.
Test verifier injection requires explicit isolated local/development/test construction;
production environment rejects it. No production configuration bypass.

Audience = **exact verified projector Cloud Run service origin**, no endpoint path,
supplied identically to app and subscription OIDC policy. Existing Cloud Run proxy must
forward its verified Authorization token to the app. Human reader authentication is
separate: existing verified-principal/environment/capability policy remains required;
production human provider (IAP/OIDC role mapping) is still a pre-existing blocker,
not supplied by the machine push verifier. Development identity policy is not production
authentication and must not be exposed as unrestricted human access.

[Google authenticated push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions),
[Google token verifier](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.id_token.html).

Ingress caps wrapped request 24 KiB, decoded body 16 KiB; validates application/json,
wrapped message/subscription/data, strict base64, canonical event schema/privacy,
matching environment, finite canonical catalogue, messageId, exact routing attributes.
No raw rejected data or exception is logged/stored. Receipt+projection transaction
commits before successful acknowledgement. Same id/hash is duplicate/no-op; different
hash is conflict and existing projection cannot change. No business code is imported
or replayed by projector.

| Outcome | HTTP | Durable/broker behavior |
|---|---|---|
| Processed / duplicate | 200 | ACK only after transaction returns committed |
| Unauthenticated | 401 | No event processing; deployment/auth error requires repair |
| Disabled / DB outage / timeout / capacity | 503 | TRANSIENT_FAILURE; broker retries, eventual DLQ policy |
| Invalid content type / oversize / bad envelope/privacy | 415 / 413 / 400 | PERMANENT_INVALID; deliberate NACK to bounded broker DLQ |
| Subscription/event environment mismatch | 400 | ENVIRONMENT_MISMATCH; never project into another environment |
| Same id/different body | 409 | EVENT_CONFLICT; durable conflict counter; never overwrite |

Application failure table's existing INVALID code encompasses invalid/environment
mismatch; ingress response categories distinguish them. Generic malformed key is
bounded, never derived from raw bytes. Not every transport-level rejected request
has a durable DB row; broker delivery/DLQ retains the message. DB failure during
recording returns 503. Projector's local retry exhaustion never ACKs a transient
production push: ingress continues non-2xx until broker forwards or expires.

Future subscription policy: exponential backoff 1–60 s, max_delivery_attempts 10,
7-day message retention, attached 7-day DLQ inspection subscription, service agent
source-subscription subscriber + DLQ publisher. Google forwarding attempts are
**best effort**, not a guaranteed exact upper bound; alert/review DLQ/auth failures,
never retry malformed messages in an unbounded app loop. Review/quarantine replay is
manual, separately authorized, and replays projection only—not business execution.
No DLQ resources were provisioned.

[Google push status semantics](https://docs.cloud.google.com/pubsub/docs/push),
[Google DLQ semantics](https://docs.cloud.google.com/pubsub/docs/dead-letter-topics).

## Deployment/runtime recommendation

Recommend the represented Cloud Run **service with instance-based billing, CPU idle
false, minimum 1 / maximum 1 publisher instance**, polling every 0.1 s after successful
batches. SQL leases allow replacement instances and later carefully approved scaling.
A request-only CPU allocation service is unsuitable for this existing background loop.
Cloud Run **worker pools now support continuous background work and europe-west4**;
they are a valid later alternative, not mandatory extra infrastructure. The implemented
service health endpoint/lifecycle integrates directly with present service deployment
patterns. Jobs/scheduled multi-minute execution fails seconds-level freshness and is
not recommended. Minimum publisher instance has ongoing CPU/memory cost even when
idle. Benchmark explicit ticks do not prove production seconds-level latency.

[Google billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings),
[Google worker pools](https://docs.cloud.google.com/run/docs/deploy-worker-pools).

Projector: request-driven authenticated HTTPS push, minimum 0, no audit polling, bounded
request concurrency. Reader: independent GET service, minimum 0, role-bound read-only
pool. Read-only gcloud describe confirmed existing SQL connection
`pr-msn-dev-gl-slopai-01:europe-west4:sloc-anoc-sandbox01`; colocate all three in
**europe-west4**. Project number **995771529960** and existing database **slopanoc** verified read-only.
Schema public remains the migration target, not a live schema inspection claim. Filtered read-only
lists returned no proposed new topic/subscription/service/account names. Name syntax
checks: topic/subscription 3–255 chars, lowercase approved names; SA IDs 6–30 chars
(projector = 30); services ≤49 chars. No name reservations or resources were created.

## Exact configuration matrix

| Workload | Exact binding / defaults |
|---|---|
| ANOC | SLOPANOC_HYBRID_AUDIT_ENABLED=false; SLOPANOC_HYBRID_ENVIRONMENT=development; enabling also requires SLOPANOC_PROJECTION_ENABLED=true and SLOPANOC_FINOPS_ENABLED=true; SLOPANOC_OTEL_ENVIRONMENT=development; existing secure ANOC DB writer binding |
| Publisher | SLOPANOC_HYBRID_PUBLISHER_ENABLED=false; SLOPANOC_HYBRID_DATABASE_URL=secure dedicated publisher URL; SLOPANOC_HYBRID_PROJECT=pr-msn-dev-gl-slopai-01; SLOPANOC_HYBRID_AUDIT_TOPIC=projects/pr-msn-dev-gl-slopai-01/topics/slopanoc-development-audit-events-v1 |
| Publisher bounds | SLOPANOC_HYBRID_PUBLISHER_BATCH_SIZE=100; PUBLISH_CONCURRENCY=4 (adapter bound; current worker publishes serially); PUBLISH_TIMEOUT_SECONDS=2; PUBLISHER_LEASE_SECONDS=30; PUBLISHER_MAX_ATTEMPTS=10; RETRY_INITIAL_SECONDS=1; RETRY_MAX_SECONDS=60, all prefixed SLOPANOC_HYBRID_ |
| Projector | SLOPANOC_HYBRID_PROJECTOR_ENABLED=false; same project/environment/topic; SLOPANOC_HYBRID_DATABASE_URL=secure projector-only URL; PUSH_AUDIENCE=exact verified HTTPS service origin; PUSH_SERVICE_ACCOUNT=slopanoc-development-push@pr-msn-dev-gl-slopai-01.iam.gserviceaccount.com; PROJECTOR_MAX_INFLIGHT=32; PROJECTOR_DEADLINE_SECONDS=10, all hybrid-prefixed |
| Reader | SLOPANOC_OBSERVABILITY_READ_DATABASE_URL=secure reader-only URL; SLOPANOC_OTEL_ENVIRONMENT=development; READ_POOL_SIZE=2; READ_POOL_TIMEOUT=1; READ_READ_DEADLINE=2; READ_READ_CONCURRENCY=4, all SLOPANOC_OBSERVABILITY_ prefixed; SLOPANOC_OBSERVABILITY_READ_PRODUCER_POLICY_FILE=immutable mounted reviewed policy; explicit allowed origins; reviewed human auth policy |
| Local human auth only | SLOPANOC_AUTH_MODE=development + SLOPANOC_DEV_AUTH_POLICY_FILE=trusted local server policy; not a production human identity solution |

All flags false by default, distinct activation per workload. Unsupported operational
telemetry switch stays false; this pass does not parse/activate it. DB URL secret IDs,
versions, authenticated SQL login names, image digest, reader caller identity/auth
provider and actual projector URL must be owner-reviewed before deployment. No
password/token/secret values in repository. SQL role statements define NOLOGIN grant
roles; they do not create secret-bearing logins or assume their membership.

## SQL/migration and performance receipts

[Review-only role SQL](validation/HYBRID_PHASE1_DATABASE_ROLES.sql): ANOC additive
insert + hash-conflict SELECT; publisher outbox SELECT + delivery-column UPDATE only;
projector legacy and execution-derived tables with scoped SELECT/INSERT/UPDATE; reader approved sanitized
observability/financial tables SELECT. No business tables for projector/reader,
audit mutation, schema ownership, DDL or broad future/default grants.
Inspect pre-existing PUBLIC privileges and role inheritance before approval.

[Combined offline rollout](validation/HYBRID_PHASE1_PRODUCTION_ROLLOUT.sql) covers
historical d3f8b1c6a942 → reconciliation → d26a1b7c9e40. Includes progression,
M7–M15 objects and six hybrid tables/five indexes/FK/CHECK. Only DELETE/UPDATE are
alembic_version branch bookkeeping; no DROP, destructive ALTER, rename or business
DML. Historical migrations unchanged. Existing reconciliation recovery/PITR gate
still applies. Earlier daemon-access limitation was resolved for the explicitly authorized disposable rehearsal: **POSTGRESQL REHEARSAL PASS**, including additive e27b2c8d0f51 (19 revisions), role checks, no-op upgrade and cleanup. See validation/EXECUTION_JOURNAL_POSTGRES_REHEARSAL.json. The older combined SQL stops at d26; new migration remains a separately reviewable local Alembic revision.
No shared database was used as fallback. SQLite parity is not PostgreSQL acceptance.

Capture optimization removes repeated sink decode/hash validation and avoids an ORM
SELECT after a confirmed INSERT RETURNING; duplicate still checks hash. Audit/outbox
remain in caller transaction, fail/rollback together. Profile measurements include
SQL dispatch, execution and await, not just server time; residual ORM/session, other
owner updates and commit are not invented as a separate numeric pure-ORM estimate.
See [performance receipt](validation/HYBRID_PHASE1_PRODUCTION_MEASUREMENT.json).
New clean measurement: baseline/enabled wall 2.682/4.408 ms, added 1.726 ms (+64.33%);
CPU 2.342/3.831 ms, added 1.489 ms (+63.58%). Prior added wall/CPU 1.806/1.623 ms.
Separate profiled per-call wall phases: construction 0.032 ms, privacy 0.013 ms,
envelope serialization 0.012 ms, canonical serialization/hash 0.023 ms, audit
execute 0.923 ms, outbox execute 0.480 ms. SQL measurements include ORM/driver/await.
Profile includes multiple sinks; phase totals overlap and must not be summed.
Host variance and additional reference fields limit before/after attribution.
It reports baseline/enabled/absolute/relative overhead and measured construction,
privacy, envelope/canonical serialization, audit and outbox execute phases, with scope
and overlap limits. C41/C42 stay NOT PASS; local SQLite cannot qualify production CPU.

## Failure model and local readiness limits

SQL commit + Pub/Sub unavailable: owner remains committed, outbox RETRYING/visible
debt, later recovery publishes. Publish success + publisher process death before
settle: lease expires, duplicate delivery permitted, receipt applies once. Projector
unavailable: broker/mock retries; DLQ forwarding requires future reviewed policy/IAM;
business remains committed. Existing abrupt-child process tests prove crash windows
locally; Google adapter/auth/push tests mock only client/token boundaries.

Terraform standalone module at `infra/observability/terraform/hybrid`: audit/DLQ,
push/inspection subs, four accounts, scoped IAM, three Run services with dedicated
Cloud SQL/Secret Manager bindings and disabled publisher/projector flags. Provider
lock committed; local backend-disabled init/provider schema validation only. No plan,
apply/import/state operations. APIs, existing SQL/DB, registry/image, secret resources,
network and verified human auth are prerequisites, not speculative mandatory creates.
Manual creation of Terraform-named resources would need an independently approved
adoption/import decision; prefer reviewed IaC provisioning when separately authorized.

Ready For Phase-1 GCP Provisioning **NO** until isolated PostgreSQL historical upgrade,
repeat/no-op and grant rehearsal; reviewed backup/PITR; secure image/DB logins/secret
versions/network binding; exact projector URL/audience and production reader identity
policy/authorized caller are fixed. No whole-platform/full-audit/M16 acceptance claim.

## Audit completeness + database rehearsal follow-up (2026-10-11)

PostgreSQL rehearsal **PASS**, now using PostgreSQL 18 matching read-only Cloud SQL
metadata. The disposable pgvector instance reconstructed d3 then upgraded all 18
revisions to d26; repeat no-op, metadata/index/FK/CHECK inspection, actual restricted
role capture/rollback/claim/settle/projection/dedup/read and denial checks passed. Its
container and anonymous volume were removed. No new migration/event/owner in this
pass. [Receipt](validation/HYBRID_POSTGRES_REHEARSAL.json); executable local-only
`rehearse_hybrid_postgres.py` never accepts a shared database URL.

Recovery remains a concrete blocker: read-only existing instance PostgreSQL 18 /
Enterprise has automated backups=false, PITR=false, and backup list returned empty.
A successful verified recovery backup/restore is not available.
[Exact recovery requirements](HYBRID_RECOVERY_REQUIREMENTS.md).

Full auditability **NO**; tool-owner material-semantics stop and seven domain gaps
[documented](AUDIT_OWNERSHIP_GAP_DESIGN.md). Human IAP audience can be fixed without
provisioning: `/projects/995771529960/locations/europe-west4/services/slopanoc-development-observability-reader`.
[Verification, policy and origin contract](HYBRID_READER_IDENTITY_CONTRACT.md) finalized,
but current code is deny-only; actual verifier/policy deployment remains unimplemented.
No production auth enabled or tested.

Concrete remaining blockers: authoritative cross-domain journal/dispatch design and
implementation; verified recovery backup/restore; human IAP verifier and exact
subject/group/environment grants; approved image digest, SQL login/secret versions,
network/effective privileges and first-party HTTPS origin; generated projector HTTPS
origin/audience bound after disabled service creation. Earlier PostgreSQL-not-executed
status is superseded by this receipt, not by a cloud runtime claim. M16 C41/C42 NOT
PASS; M16 INCOMPLETE; M17 UNSTARTED. Provisioning NO, cloud/shared changes NONE.

## Execution journal status — 2026-10-11

Execution journal/controlled egress are implemented and locally validated, default disabled. New head e27b2c8d0f51 passed disposable PostgreSQL18 rehearsal. Full auditability NO; recovery, actual reader verification, deployment bindings and runtime performance gates remain blockers. Provisioning and shared migration readiness NO. See [implementation](27_EXECUTION_AUDIT_JOURNAL_AND_CONTROLLED_EGRESS.md), [coverage](AUDIT_COVERAGE_MATRIX.md) and [remaining gaps](AUDIT_OWNERSHIP_GAP_DESIGN.md).
