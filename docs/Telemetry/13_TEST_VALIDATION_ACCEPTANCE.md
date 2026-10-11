# 13 — Test, Validation and Acceptance

## Principle

Observability is not proven by a happy-path log line. It is proven when it correctly explains success, failure, timeout, retry, cancellation, safety decisions and cost.

## Unit tests

### Tracing
- root creation/finalization;
- nested spans;
- context propagation;
- terminal cleanup;
- exception recording.

### Active registry
- lifecycle;
- concurrent runs;
- current-stage updates;
- heartbeat;
- TTL/cleanup.

### Redaction
Inject fake:
- bearer token,
- API key,
- password,
- prompt content,
- Teams message.

Assert prohibited content never appears in exported trace/log/ledger/API data.

### Error taxonomy
Known exceptions map to stable codes.

### Cost estimator
- token -> cost;
- price version;
- cached-token pricing;
- currency;
- provider-missing usage estimates marked estimated.

### Allocation
- direct/shared/unallocated;
- deterministic sums;
- rounding;
- confidence/version.

## Integration traces

Validate expected topology for:
- general prompt,
- Teams task,
- troubleshooting,
- multi-agent support,
- governed command path,
- planning failure,
- source gap,
- pending interaction suspend/resume.

## Failure injection

Inject:
- model timeout,
- Graph timeout,
- Graph rate limit,
- DB timeout,
- knowledge failure,
- storage failure,
- trace exporter failure,
- cost-ledger persistence failure,
- SSE disconnect.

Assert:
- owning span identified,
- stable error code,
- terminal behavior,
- user-safe response,
- metric/alert emitted where appropriate.

## Watchdog

Block a dependency beyond stall threshold but below timeout.

Expected:
- `run.stalled`,
- current stage remains queryable,
- heartbeat/admin UI shows blocked stage,
- later completion/timeout transitions correctly.

## FinOps validation

### Coverage
Every synthetic billable model call creates exactly one ledger event.

### Retry cost
Retries increase usage/cost correctly.

### Pricing replay
Old usage recalculates exactly from its stored price version.

### Reconciliation
Synthetic billing rows produce expected variance.

### Allocation
Allocated + unallocated equals source cost pool within rounding tolerance.

### Unit economics
Aggregations reconcile to turn and period totals.

## SLO tests

Replay metric fixtures and verify:
- availability,
- latency percentiles,
- error budget,
- burn-rate thresholds.

## Frontend

Test:
- progress labels,
- run ID,
- heartbeat freshness,
- terminal completion,
- SSE disconnect,
- admin timeline,
- RBAC denial,
- FinOps pages exclude message bodies.

## Load tests

Measure:
- instrumentation overhead,
- exporter queue behavior,
- active registry concurrency,
- ledger write overhead,
- dashboard query performance.

## Multi-instance

If Cloud Run scales horizontally, verify:
- complete correlation,
- active status accessibility,
- no invalid process-local support assumptions.

## Acceptance evidence

Produce and retain:
1. successful end-to-end turn trace;
2. stalled/timeout trace;
3. governed troubleshooting trace;
4. per-turn cost breakdown;
5. billing reconciliation sample;
6. SLO dashboard;
7. alert evidence;
8. redaction evidence;
9. load/overhead report.


## CI contract tests

Validate:
- stage/status/error vocabulary,
- metric-cardinality policy,
- prohibited telemetry fields,
- span closure,
- model-call usage accounting,
- telemetry schema versioning,
- pricing schema compatibility.

## Release/config correlation tests

Assert representative traces include:
- Git SHA,
- release/revision,
- model,
- required schema/config versions.

## Infrastructure tests

Validate dashboards/alerts receive:
- Cloud Run,
- Cloud SQL,
- Collector,
- BigQuery,
- Graph dependency telemetry.

## Quality/outcome tests

Validate:
- unknown outcomes remain unknown,
- explicit/server-owned outcomes join correctly to turn cost,
- FTC is not fabricated.

## Profiling overhead tests

Compare profiling on/off:
- CPU,
- memory,
- p95 latency,
- throughput.

## Hybrid acceptance additions

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) requires strict version/environment/type/size/privacy tests; same-transaction
critical-record/outbox rollback; publish-before-confirm crash and expired lease race;
idempotent duplicate/conflict receipt + projection atomicity; stale/out-of-order
terminal protection; bounded OTLP/CloudEvents normalization and signal temporality;
independent lane outage/starvation/DLQ/replay; capture/freshness/debt unknown semantics;
read-only role enforcement and no business replay. Pure ports/fake tests do not
prove Cloud SQL adapters. Native Collector validates source configuration only;
production image/runtime/identity require approved live qualification. Rerun unchanged
C41/C42 after integration; no new CPU/SLO threshold replaces them.


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
