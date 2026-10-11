
# 16 — Production Telemetry Backend and Collector Topology

## Production decision

The production topology is fixed:

```text
SLOPANOC application
        |
        | OTLP
        v
OpenTelemetry Collector
        |
        +--> Google Cloud Trace
        +--> Google Cloud Monitoring
        +--> Google Cloud Logging
        |
        +--> observability health metrics
```

FinOps uses BigQuery separately for billing, pricing, reconciliation and analytical workloads.

The application must not contain vendor-specific observability business logic beyond exporter/configuration adapters.

## Collector responsibility

The OpenTelemetry Collector is mandatory in production.

It must provide:
- OTLP ingestion,
- batching,
- retry/queueing,
- memory limiting,
- attribute filtering,
- redaction defense-in-depth,
- environment/resource enrichment,
- tail-sampling support,
- exporter health telemetry,
- bounded shutdown/flush behavior.

The Collector configuration is version-controlled as infrastructure-as-code.

## Collector processors

Production baseline must include:
- `memory_limiter`,
- `batch`,
- resource/environment enrichment,
- attribute filtering/redaction,
- tail-sampling processor when sampling is enabled.

Do not add processors that mutate semantic meaning of SLOPANOC events.

## Export destinations

### Traces
Primary: Google Cloud Trace.

### Metrics
Primary: Google Cloud Monitoring.

### Logs
Primary: Google Cloud Logging.

### FinOps
Primary runtime usage ledger: SLOPANOC durable relational store.  
Primary provider-billing analytics: BigQuery.

## Export failure behavior

A telemetry backend outage must not normally fail a user turn.

Required:
- exporter failure metric,
- dropped span/log metric,
- local bounded queue,
- retry with backoff,
- alert on sustained degradation.

Accounting-grade cost-ledger persistence has separate reliability rules in `20_FINOPS_ACCOUNTING_DURABILITY.md`.

## Semantic convention pinning

The repository must pin:
- OpenTelemetry SDK versions,
- instrumentation-library versions,
- semantic-convention version used for GenAI/HTTP/DB mapping.

Do not silently upgrade semantic-convention packages in production.

Every intentional semantic-convention upgrade requires:
- changelog,
- compatibility review,
- dashboard/query review,
- test update.

## Tail sampling production policy

Initial stabilization:
- 100% trace capture.

After stabilization, production tail sampling must retain 100% of:
- failed turns,
- timeout turns,
- stalled turns,
- safety events,
- governed operational turns,
- command-authority/approval flows,
- cost anomalies over configured threshold.

Healthy general-chat turns may be sampled at 10–20%.

Usage/cost ledgers and aggregate metrics are never sampled.

## Collector health

Monitor:
- accepted spans,
- exported spans,
- refused spans,
- dropped spans,
- queue size,
- retry count,
- memory pressure,
- exporter failures,
- collector CPU/memory,
- scrape/export latency.

Collector failure must appear in the SLOPANOC Health dashboard.

## B01 disposition — exceptional full-trace archive

Status: **RESOLVED BY ARCHITECTURE ADAPTATION** (user-approved 2026-10-09 planning
repair). The 90-day exceptional full-trace requirement is preserved, not waived.
These definitions are planning contracts, not claims of implemented components.

| Component | Purpose | Retention contract |
|---|---|---|
| Operational Trace Store = Google Cloud Trace | Full supported interactive traces/spans | Fixed 30-day provider retention for healthy sampled and exceptional traces |
| ExceptionalTraceArchive | Durable sanitized full exceptional OTel trace reconstruction | 90-day primary archive retention requirement; becomes deletion-eligible after horizon |

For exceptional traces, days 0–30 use Cloud Trace plus archive; days 31–90 use archive
only, subject to independently confirmed delivery. M7 diagnostic/run summaries,
SLO aggregates, application logs and metrics do not substitute for full traces.
Cloud Trace is never configured or advertised as retaining spans for 90 days.
Provider reference: https://docs.cloud.google.com/stackdriver/quotas .

The archive is observability-only: never evidence, command authority, routing,
approval, provenance authority, business persistence or a second application event
store. Archive expiry cannot delete operational or financial derived records.
M10 ledger, M11 billing, M12 reconciliation, M13 economics and M14 forecast/anomaly
retention dependencies remain independently governed; no false archive dependency
is added to them.

### Eligibility and server-owned classification

Archive only canonical exceptional ALWAYS_KEEP equivalents: SAFETY, GOVERNED,
FAILED, TIMEOUT and EVER_STALLED (including stalled then completed). Explicitly
approved command-authority/approval paths are GOVERNED; cost anomalies over the
configured threshold are eligible when server-owned trace linkage exists (existing
production tail policy above). Operational cancellation or pipeline failure is not
an automatic new archive class: it must match an approved exceptional class, or be
explicitly approved before the versioned registry admits it. Unknown eligibility
is PENDING/UNKNOWN until classified; do not quietly archive all healthy traffic.
Safety/governed/ever-stalled flags survive successful terminal outcome.

Classification uses bounded metadata from existing M2–M6/M9 owners, terminal root
status and trusted governance/safety observations, not prompt/response content or
free-form span names/messages. Healthy ordinary traces follow normal sampling and
Cloud Trace retention, never this 90-day archive. SDK remains 100% capture; healthy
10–20% tail reduction is provisional after coverage validation. ALWAYS_KEEP
exceptional traces route to archive even when healthy sampling is 10%.

### Full safe trace schema and deterministic encoding

Archive envelope v1 wraps sanitized OTLP ExportTraceServiceRequest ResourceSpans,
retaining OTel semantics instead of inventing an unrelated event model. Envelope
metadata: archive schema version, environment, trace ID, retention/eligibility
class, telemetry schema version, redaction-policy version, sampling/archive-policy
version, trusted terminal time, first durable complete commit time, retention-eligible
at, completion state and logical content digest. Per retained span preserve applicable:

- trace ID, span ID, parent span ID, registered bounded span name and span kind;
- start/end UTC nanosecond timestamps, duration derived without rounding, canonical
  OTel status plus SLOPANOC status and canonical safe error classification;
- allowlisted safe resource metadata and instrumentation-scope name/version/schema URL;
- allowlisted typed span attributes and span events, with event name/timestamp/attributes;
- safe trace links only when explicitly registered/validated; current production
  projection denies links, so they remain absent in v1 unless explicitly approved;
- environment and capture-time policy/schema versions, preserving hierarchy/timing.

No status description, arbitrary scope metadata, unrestricted links/tracestate/
baggage/attributes or raw exceptions are preserved. Prohibit prompts/responses,
Teams bodies, raw tool args/results, command/SQL/binds/DSN, sensitive URLs/HTTP auth,
credentials/secrets, document contents, vectors, raw billing identifiers, arbitrary
user identity and hidden thought/model reasoning. Numeric thought-token usage is
not reasoning content. Archive allowlist is at least as strict as Cloud Trace.

Use a deterministic versioned serialization profile over OTLP protobuf fields:
validate types, sort resource/scope groups and spans by canonical safe identity,
sort unordered attribute keys, retain event ordering with timestamp/ordinal, and
serialize deterministically. The logical digest covers safe OTLP plus immutable
policy/classification metadata, excludes mutable acknowledgements/retry counters/
object generation and compression bytes. Deterministic compression (e.g. gzip
with fixed header timestamp) may be selected later; decoding yields identical
logical content and complete reconstruction. Reject unknown schema/unsafe payload,
conflicting repeated span ID content or cross-environment trace groups.

### Privacy pipeline and stable pluggable transport

```text
application allowlist
    -> environment validation
    -> Collector redaction / attribute enforcement
    -> tail decision / exceptional classification
    -> Cloud Trace export (independent queue)
    -> exceptional-only archive branch (independent queue):
       ExceptionalTraceArchiveExporter transport contract
       -> stable OTLP exporter
       -> Exceptional Trace Archive Bridge
       -> dedicated durable GCS object archive
```

The diagram denotes sanitized fan-out after classification; Cloud Trace's successful
acknowledgement is not a prerequisite for archival export, nor vice versa. No raw
branch bypasses application/Collector sanitization. Environment filter precedes
resource enrichment. Final resource projection follows detection. Bridge validates
again; it is defense in depth, not permission to emit unsafe producer data.

ExceptionalTraceArchiveExporter defines send/ack/health with bounded payload/batch/
queue/deadline/retry contracts independent of storage. Use the pinned stable OTLP
transport supported by the production Collector, with TLS/verified service identity
(or declared isolated local loopback in tests), to the dedicated bridge. Do not
forward normal trace batches to the archive sink then hope it discards them.
Both branches consume the sanitized retained trace; archive routing explicitly
requires its server-owned exceptional class. Tail timing/affinity/capacity and safe
metadata survival must be proved using the pinned production distribution.

Component review: contrib google_cloud_storage exporter v0.160.0 metadata declares
traces DEVELOPMENT and logs ALPHA, distribution contrib. It has insufficient
stability to be a mandatory production architecture dependency. Source:
https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/exporter/googlecloudstorageexporter/metadata.yaml .
The user-approved doc 26 operational Pub/Sub branch is additional to the archive,
with pinned compatibility/privacy/failure qualification required before activation.
It is never substituted for stable OTLP -> bridge -> GCS.

### Bridge assembly, idempotency and completeness

OTLP request boundaries are not trace boundaries. Bridge assembly groups validated
spans by environment+trace ID, deduplicates identical span IDs and retains late
chunks within bounded terminal/export settlement window. Safe producer closure
metadata must declare the registered span population expected after cleanup; root
arrival alone or timeout alone cannot prove completeness. The manifest contains
only technical span identities/count/digest, not business content, and is itself
allowlisted. If multi-producer population is not provable, status stays UNKNOWN or
PARTIAL, never full-success. Required hierarchy closure includes every registered
expected span, safe events and valid timings; known external parents/approved links
are explicit rather than falsely reported missing internal spans.

Use bounded durable chunk staging and a final manifest to assemble large traces
without unbounded memory or silent truncation. Provisional local limits: 4MiB decoded
per request, 64MiB logical per trace, 10,000 spans, 256 safe events per span, at most
256 pending traces and bounded spool bytes/TTL. Enforce tighter existing producer
caps where applicable. Reject/mark incomplete on limit exhaustion; do not claim
complete after trimming. Capacity/window values remain versioned configuration and
subject to M16 validation, not production throughput claims. Privacy validation
happens before durable staging. OTLP acknowledgement means durable chunk acceptance;
it does not mean full exceptional trace committed. Crash recovery must preserve
accepted safe chunks and manifest state, with bounded retry and safe health reporting.

Identity: environment+trace ID+archive schema/policy version+safe logical digest.
Create immutable content-addressed chunks/envelope/manifest with conditional create;
retries of identical bytes acknowledge existing validated content. Different content
with same identity is CONFLICT; late valid extra spans append a new complete envelope
version with explicit supersession, never overwrite old history ambiguously. An
incomplete assembly is not a committed complete archive. Retention starts at the
first durable complete commit for that version; retry does not refresh TTL. Later
complete superseding versions retain their own full 90-day horizon. Staging never
expires a required chunk before its referencing live complete manifest. No business,
model or tool execution is retried to reconstruct missing spans.

Archive completeness is independent of M9 instrumentation completeness and Cloud
Trace export/retention. Denominator: unique trusted eligible exceptional traces
requiring archive, including failed/unconfirmed deliveries. It must come from
unsampled server-owned eligibility observations before archive export, not only
what the bridge receives. Numerator: eligible traces with a durable COMPLETE
manifest confirming the expected sanitized span population. Join only environment+
technical trace identity; count duplicates once. Snapshot/window/settlement rules
show PENDING, FAILED, UNKNOWN, UNAVAILABLE and PARTIAL separately. Missing denominator
coverage or confirmation makes the ratio unknown/unavailable, never 100% by absence.

Archive failure never fails the business turn, changes command/governance behavior,
replays a provider/tool call or logs a raw fallback. Preserve independent Cloud Trace
export; surface 90-day archive coverage degradation, bounded retries/backlog/drop
counts and safe failure categories. Metrics use bounded environment/archive result/
retention class/failure category only; no trace/run/session/user/object path labels.
Configured, attempted, chunk-accepted and complete-committed are distinct truths.

### Storage target and retention semantics

Production target: dedicated GCS archive bucket per canonical environment with no
public access, uniform bucket-level access, separate least-privilege service writer
and authorized service reader, no browser-direct access. Writer/reader identities
and exact bucket/project/region are later reviewed activation bindings. Platform
at-rest encryption and authenticated TLS in transit assumed; no custom encryption
or IAM deployment is claimed. Proposed Standard storage avoids conflating the word
archive with a GCS storage class/minimum-storage-duration choice.

Bounded deterministic object layout:
`<environment>/<UTC-commit-date>/<trace-id>/<schema-version>/<logical-digest>/...`
with fixed envelope/manifest/chunk suffixes. Every segment is validated; no user,
session/business content or raw billing identifier in object paths. Trace IDs are
restricted internal lookup keys and never metric labels. Retrieval must not infer
arbitrary paths from client input; use the authorized manifest resolution contract.

90-day primary retention eligibility is measured from durable complete envelope
commit, with the same anchor in the local evaluator; this conservatively covers
90 days after terminal capture and never shortens on upload delay. GCS lifecycle
is asynchronous and must be authored to protect all manifest/chunk objects until
that horizon. Proposed final immutable envelope objects use age>=90 days; any chunk
layout needs manifest-aware cleanup or aligned custom-time rules so older staged
chunks cannot expire before their final manifest. Idempotent retry does not create
new objects or reset their age. Staging TTL is separate from primary retention and
cannot delete committed manifest dependencies.

Lifecycle eligibility is not guaranteed physical byte deletion at exactly day90.
Soft delete is a separate platform policy; current supported buckets default to
7 days, and soft-deleted data remains recoverable/charged until that horizon ends.
Final soft-delete/versioning/hold settings must be explicit in approved production
configuration; do not silently disable recovery or enable indefinite copies.
A strict maximum physical deletion deadline requires a separate security/legal
disposition. No legal-hold system or locked retention policy is invented here.
References: https://docs.cloud.google.com/storage/docs/lifecycle and
https://docs.cloud.google.com/storage/docs/soft-delete (checked 2026-10-09).

### Protected reconstruction / local acceptance / production handoff

Read contract: verified technical_diagnostics capability plus explicit authorized
canonical environment, validated trace ID and bounded version/page selectors.
Resolve only safe manifests in that scope; never accept arbitrary object names,
remote URLs or cloud credentials. Read-only reconstruction returns the sanitized
OTLP envelope, hierarchy/timing/events, policy versions and truthful completeness/
retention state. Require an existing operational capability as well if the route
is mounted under the existing run facade. Canonical role ceilings unchanged: User,
FinOps, Admin and Auditor gain no implicit archive retrieval. Missing/unauthorized
object lookup must not expose cross-environment existence. No manual archive
modification, reclassification, delete UI or public signed-URL bypass.

M15 plans a local filesystem sink/spool in a disposable isolated test directory
implementing the SAME eligibility, schema, deterministic encoding, commit/version,
privacy, retention metadata and protected retrieval contract. Local OTLP Collector
-> loopback bridge -> local sink plus injectable clock prove the policy without
GCS credentials/bucket/SDK service access. Local atomic file commit simulates object
conditional-create; transport/storage adapters remain pluggable. No production
writer or live archive transport is declared validated by local doubles.

Existing Diagnostics may show separately confirmed Cloud Trace retained, Exceptional
archive retained, Not retained by policy, Archive pending/failed/unknown/unavailable.
No large archive browser is required. Missing retained trace does not imply failed
execution; successful API/store confirmation, not probability/configuration, determines
status. Keep configuration read-only and protected data out of durable browser storage.

M15 local tests: Cloud Trace30day contract, five minimum exceptional classes/ordinary
exclusion, complete OTLP hierarchy/timing/status/event reconstruction, every prohibited
sentinel, pre-sink redaction, business-safe failure/no replay/independent Cloud Trace,
idempotency/conflicts/late chunks, environment and diagnostic capability denials,
100%->10% healthy sampling preserves archive eligibility and M9/M10–M14 truth,
90day local evaluator boundaries, completeness denominator and unknown confirmations.

M16 handoff (not started): burst throughput/backpressure/outage/partial chunk loss,
retry/idempotency, tail memory, queues, Collector/bridge restart, malformed OTLP,
simulated GCS errors/latency and reconstruction performance. M17 handoff (not started):
exactly approved bridge deployment, dedicated bucket/lifecycle, writer/reader IAM,
service identity/environment separation, monitoring/alerts and retrieval runbook.
Live bridge/GCS writes/IAM/lifecycle/throughput/outage/retry/retrieval validation occurs
only in that approved production-validation sequence. M15 may be IMPLEMENTED AND
LOCALLY VALIDATED without it; the full production definition of done still requires
it. Local IaC bucket/lifecycle/IAM/bridge/routing authoring is allowed only on future
M15 implementation authorization. No deployment/cloud mutation in this planning repair.

## Independent operational reader deployment target

Doc [25](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md) adds a separate Observability
Read API runtime consuming durable/published data and protected archive reads.
ANOC telemetry producers → OTLP → Collector → Cloud Trace/Monitoring/Logging
remain unchanged; exceptional archive transport/classification/retention remain
unchanged. Reader is neither Collector, archive bridge/writer nor accounting worker.
Collector/exporter/ANOC health must come from confirmed producer sources, not the
reader process. Independent health/compute and bounded read-only archive access
are future deployment requirements, not implemented/live-validated resources.

## Approved Collector operational Pub/Sub branch

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) refines Operational Telemetry Pub/Sub to OPTIONAL / SCALE-TRIGGERED, disabled
and restricted to a curated measured subset; full unsampled OTLP replication is
not required for Phase 1. The pinned
Google 0.160.0 manifest includes googlecloudpubsub exporter; pin unchanged. Branch
is optional target design, not enabled. Native exporter emits OTLP protobuf with
CloudEvents attributes, not internal JSON envelopes. Future safe projector adapter
normalizes deterministic record identities; ce-id alone is not rebatching dedup.
Fan out from the existing environment/scope/redaction gate before cloud/archive
tail decisions. Independent bounded exporter queue preserves Cloud Trace/archive
semantics and keeps operational stages from waiting for terminal tail classification.
The dormant example/local nop composer cannot publish. This does not substitute
Pub/Sub for stable OTLP -> exceptional archive bridge -> dedicated GCS.
