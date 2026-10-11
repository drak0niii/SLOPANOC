# 09 — Security, Privacy, RBAC, Retention and Sampling

## Core rule

Observability data is production operational data and must be governed like production data.

## Data classification

### Safe by default
- timing,
- stage/status,
- agent/tool/model names,
- token/count metrics,
- stable error codes,
- approved source identities.

### Restricted
- session/run/thread IDs,
- user identifiers,
- case/fault IDs,
- resource/chat IDs.

### Prohibited by default
- credentials/tokens,
- auth headers,
- full prompts,
- full assistant responses,
- Teams message bodies,
- attachment contents,
- knowledge document bodies,
- hidden chain-of-thought.

## Central redaction

All custom telemetry metadata passes through a single allow-list/redactor.

Responsibilities:
- permit known fields,
- remove secret-looking fields,
- truncate strings,
- sanitize error messages,
- optionally hash/opaque selected identifiers,
- reject unexpected nested payloads.

Do not rely on each caller remembering to redact.

## RBAC

Suggested roles:

### User
Own chat progress only.

### Operator
Safe active-run/timeline metadata.

### Developer/SRE
Technical trace/error details.

### FinOps
Cost/usage/allocation, not conversation content.

### Admin
Configuration/retention.

### Auditor
Read-only governed summaries according to policy.

Enforce authorization server-side.

## Retention tiers

Define independent configurable retention for:
- cost ledger — long,
- aggregate metrics — long,
- error/timeout traces — medium/long,
- governed operational traces — medium/long initially,
- healthy generic traces — shorter/sampled later,
- active-run state — TTL,
- domain diagnostics — bounded.

Final day counts depend on enterprise policy and compliance requirements.

## Sampling

Never sample:
- cost ledger,
- aggregate metrics,
- security/safety events.

Always retain initially:
- errors,
- timeouts,
- stalled runs,
- command authority/approval paths,
- governed operational turns if policy permits.

Healthy general traces can be sampled later after baselines exist.

## Deletion interaction

If sessions/conversations can be deleted, define whether related trace metadata is deleted, anonymized or retained under audit exception. Do not create a hidden shadow copy of deleted content.

## Encryption

Use platform encryption in transit/at rest. Apply production metadata access controls to telemetry databases and billing datasets.

## Secret leakage testing

Use fake secrets in automated tests and verify they never appear in:
- traces,
- logs,
- metrics,
- cost ledger,
- API responses.

If sanitizer detects a prohibited field, increment a safe counter without exporting the value.


## Fixed production retention

Initial targets:

| Data | Retention |
|---|---:|
| Ordinary full traces in Google Cloud Trace | 30 days, provider-controlled |
| Exceptional full traces in Google Cloud Trace | First 30 days, provider-controlled |
| ExceptionalTraceArchive: safety/governed/failed/timeout/stalled and explicitly approved exceptional classes | 90-day primary archive horizon; deletion eligibility after horizon |
| Operational summaries | 30 / 90 days according to existing outcome class |
| Structured application logs | 30 days unless policy requires longer |
| Aggregate metrics analytical access | Minimum 13 months; longer provider retention satisfies this, with resolution disclosed |
| FinOps usage ledger / billing analytics | 24 months |
| Active-run status | short TTL after terminal state |

Enterprise/compliance policy may require longer retention.

## Fixed sampling policy

Initial stabilization: 100% trace capture.

After stabilization:
- retain 100% errors/timeouts/stalled/safety/governed operational turns;
- retain 100% cost ledger and aggregate metrics;
- healthy general chat may be sampled to 10–20%.

Sampling changes are versioned and observable.

## Exceptional archive privacy and retention semantics

B01 is RESOLVED BY ARCHITECTURE ADAPTATION, not a reduced requirement. Preserve
sanitized full exceptional OTel traces: Cloud Trace plus archive during days 0–30;
archive alone during days 31–90. Healthy ordinary traffic is not archived merely to
extend provider retention. Eligibility uses bounded server-owned metadata, never
prompt/response/free-form strings. Archive data is at least as restricted as Cloud
Trace: producer allowlist -> environment validation -> Collector redaction -> tail
classification -> independent Cloud Trace export and eligible-only archive export.

No prompts/responses, Teams bodies, raw tool args/results, command/SQL/binds,
sensitive URLs/auth data, credentials/secrets, document bodies/vectors, raw billing
source identifiers, arbitrary identity or hidden thought/reasoning enter the archive.
Only explicitly safe validated links may ever be preserved; currently denied links
remain absent, never broadened implicitly. Retention metadata includes schema,
redaction and sampling/archive policy versions; trace IDs are restricted lookup
identifiers, never metric labels. Retrieval requires technical_diagnostics and
server-enforced environment scope. No destructive UI or manual reclassification.

Dedicated GCS primary lifecycle target is 90 days of archive retention eligibility;
lifecycle execution is asynchronous and soft-delete retention is separately disclosed.
No guarantee of physical erasure at exactly day 90. A strict maximum-erasure/legal
hold requirement would require separate policy disposition, not invented compliance.
Archive retention has no dependency on M10–M14 financial persistence; deletion of an
archive object cannot delete its financial or operational derived records.

Google Monitoring retention is provider/type-controlled. Current custom/workload/
OTLP series can satisfy minimum 13-month analytical access with 24-month retention;
resolution differs: custom/non-OTLP workload original frequency 6 weeks then
10-minute intervals; OTLP/Prometheus original frequency 1 week, then 1-minute
intervals for 5 weeks, then 10-minute intervals. Other metric types may retain only
6 weeks and require separate analytical coverage if needed for the minimum horizon.
Do not impose artificial 13-month deletion or claim 13-month exact-minute history
from M9's 35-day working set. Public provider reference:
https://docs.cloud.google.com/monitoring/quotas (checked 2026-10-09).
Archive schema/transport/local-vs-live validation: doc 16.

## Hybrid event and database-audit privacy

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) applies this classification to outbox, Pub/Sub, projector receipts/failures,
DLQs and read models. Strict allowlisted payloads prohibit arbitrary dictionaries,
raw prompts/responses/Teams/tool/SQL/command bodies/secrets/hidden reasoning. IDs
remain restricted and never metric labels. Separate workload DB roles and service
identities constrain writes/reads; none is created. Broker access does not imply
UI capability; FinOps gains no content access. Unpublished/debt rows cannot expire
silently; published/dedup/retention/replay horizons require joint approval. M15
exceptional-only sanitized dedicated GCS 90-day target remains independent.
pgAudit parameters must stay off; SQL literal masking must be reviewed and tested
before activation because parameter-off alone does not suppress embedded values.


## Local Phase-1 hybrid refinement — 2026-10-11

[Doc 26](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) defines the locally implemented
authoritative owner transaction → audit/outbox → bounded publisher → fake transport
→ projector → durable projection → independent GET reader. Additive revision
`d26a1b7c9e40` descends from `c16f4a8b2d90`; no shared schema was changed.
`GET /api/observability/hybrid/activity` exposes bounded safe lifecycle/freshness/debt
DTOs with server RBAC/environment scope; no worker runs on reads. M15 policy applies
to typed audit fields; projections do not duplicate M10–M14 financial truth.
Operational Telemetry Pub/Sub is **OPTIONAL / SCALE-TRIGGERED**, disabled, limited
to a future curated measured subset. Full unsampled OTLP replication is not Phase-1
minimum. Existing OTLP/cloud/archive pathways remain unchanged. Local SQLite/fake
validation is not PostgreSQL/Pub/Sub qualification. M16 remains INCOMPLETE; C41/C42
are not PASS; M17 remains UNSTARTED. No cloud mutation or deployment authorization.

## Phase-1 production-readiness update — 2026-10-11

Production audit push uses restricted Cloud Run IAM plus Google signature/audience/verified-service-account checks; no browser/role/header trust. See HYBRID_PHASE1_PRODUCTION_READINESS.md. Optional identifiers follow the M15 EVENT/DATABASE registry; unknown remains absent. Reader human identity remains a separate pre-existing production gate.

## Execution journal status — 2026-10-11

Execution facts exclude raw tool arguments/results, retrieval queries/documents, prompts/responses, Teams bodies and hidden reasoning. Actor/target/source references are restricted, server RBAC and environment scoped. FinOps gains no raw operational content access. See [implementation](27_EXECUTION_AUDIT_JOURNAL_AND_CONTROLLED_EGRESS.md), [coverage](AUDIT_COVERAGE_MATRIX.md) and [remaining gaps](AUDIT_OWNERSHIP_GAP_DESIGN.md).
