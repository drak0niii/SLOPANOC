# 12 — Implementation Roadmap

## Delivery strategy

Build sequentially. Do not jump to dashboards before telemetry contracts are stable. Do not build FinOps dashboards before usage accounting is trustworthy.

---

## M0 — Architecture Contract

### Build
- create `backend/observability` skeleton;
- define stage/status/error enums;
- define attribute names;
- define redaction and metric-cardinality rules;
- define settings;
- define telemetry/cost schema versions.

### Exit
- no production behavior changed;
- one canonical naming contract exists;
- docs and code skeleton agree.

---

## M1 — OpenTelemetry Foundation + Production Collector

### Build
- OTel SDK initialization in FastAPI lifespan;
- production OTel Collector topology from `16_PRODUCTION_TELEMETRY_BACKEND.md`;
- structured logging foundation from `18_STRUCTURED_LOGGING_STANDARD.md`;
- trace/metric/log correlation;
- OTLP/local/no-op exporters;
- resource metadata;
- exporter-health metrics;
- bounded shutdown flush.

### Exit
- app boots with OTel on/off;
- sample span exports;
- exporter outage does not crash a user turn.

---

## M2 — Root Turn Trace

### Build
- root span in canonical `chat_service` event generator;
- active-run registry;
- session/planning/thread/context/finalization stages;
- terminal cleanup;
- safe run ID in SSE/API.

### Exit
- one prompt is reconstructable phase-by-phase;
- active run exposes current stage.

---

## M3 — Model Instrumentation + Usage Ledger

### Build
- every model call;
- TTFT where available;
- input/output/cached tokens;
- retry/finish/error;
- agent/role;
- one usage ledger row per call.

### Exit
- every synthetic model call attributable to run + agent + role;
- usage-ledger coverage = 100% in validation scenarios.

---

## M4 — Agent and Tool Instrumentation

### Build
- spans for Team Manager, TAE, IM, PM, AOE;
- centralized tool callbacks/wrappers;
- primary/supporting role;
- contribution/outcome classification.

### Exit
- no specialist/tool execution is invisible.

---

## M5 — Dependency + Infrastructure Instrumentation

### Build
- Microsoft Graph;
- knowledge internals;
- DB/pool;
- storage;
- other HTTP/provider calls.

### Exit
- a blocked external dependency is identifiable as current child span.

---

## M6 — Reliability Controls

### Build
- configurable deadlines;
- retry policy;
- watchdog;
- heartbeats;
- structured error mapping;
- safe cancellation;
- circuit breakers only where justified.

### Exit
- no indefinite Processing state;
- stalled and timeout states are visible;
- non-idempotent operations are not blindly retried.

---

## M7 — Persistence and Observability APIs

### Build
- turn summaries;
- active-run query;
- timeline query;
- retention/TTL;
- RBAC;
- multi-instance strategy.

### Exit
- a run can be retrieved by `run_id`;
- active-run support does not depend on local shell logs.

---

## M8 — UI Operational Visibility

### Build
- safe progress labels;
- run details;
- active-run admin screen;
- timeline/waterfall;
- governance/performance/cost views.

### Exit
- operator can diagnose a deliberately stalled dependency through UI/API alone.

---

## M9 — SRE Metrics, SLOs and Alerts

### Build
- golden signals;
- AI-specific metrics;
- initial SLO definitions;
- error budgets;
- burn-rate alerts;
- synthetic checks.

### Exit
- health is objectively measurable;
- alert dry-run validated.

---

## M10 — Runtime FinOps Cost Engine

### Build
- versioned pricing catalog;
- per-call estimator;
- per-turn rollup;
- per-agent/model/domain aggregation;
- usage-ledger coverage metric.

### Exit
- every model call has estimated cost;
- every turn has estimated direct cost;
- calculation can be replayed from usage + price version.

---

## M11 — Provider Billing Integration

### Build
- GCP detailed billing export;
- FOCUS export where applicable;
- pricing export/catalog;
- normalized BigQuery views;
- ingestion health.

### Exit
- provider billed costs load automatically;
- dashboards use normalized views, not raw evolving schemas.

---

## M12 — Reconciliation and Allocation

### Build
- estimated vs billed reconciliation;
- direct/shared/unallocated pools;
- allocation drivers;
- method/version/confidence;
- allocation coverage metrics.

### Exit
- unallocated spend is visible;
- reconciliation variance visible;
- shared allocation reproducible.

---

## M13 — Unit Economics

### Build
- cost/turn;
- cost/completed turn;
- cost/agent/model;
- cost/governed answer;
- cost/Teams lookup;
- cost/knowledge retrieval;
- cost/case/resolution where outcome data exists;
- quality-latency-cost joins.

### Exit
- engineering and FinOps can compare architecture/model choices with cost and quality.

---

## M14 — Budgets, Forecasting and Anomalies

### Build
- budget scopes;
- MTD actual;
- month-end forecast;
- threshold alerts;
- token/call/cost anomaly detection;
- observability-cost budget.

### Exit
- proactive anomaly/budget alert demonstrated end-to-end.

---

## M15 — Security, Retention and Sampling Hardening

### Build
- RBAC review;
- redaction adversarial tests;
- retention jobs;
- sampling;
- access audit if required;
- deletion/anonymization interaction;
- ExceptionalTraceArchive policy, safe full OTLP envelope, pluggable bridge/local sink, protected reconstruction and independent archive health/completeness (doc 16).

### Exit
- fake secrets cannot escape telemetry, including exceptional archive;
- retention and sampling behavior proven;
- safe exceptional full-trace reconstruction, eligibility, idempotency, environment/capability denial and 90-day local retention evaluator proven; ordinary traces excluded;
- Cloud Trace fixed 30-day retention and independent 90-day archive requirement truthful; M9 instrumentation completeness, Cloud Trace export and archive completeness separate;
- M15 may be IMPLEMENTED AND LOCALLY VALIDATED using an isolated archive sink; live bridge/GCS/IAM/lifecycle/throughput/outage/retrieval validation is deferred to explicitly approved M16–M17 production-validation steps. No M15 local cloud mutation or production validation claim.

---

## M16 — Failure Injection and Load Validation

### Build
- model timeout;
- Graph failure/rate limit;
- DB failure;
- knowledge failure;
- telemetry exporter outage;
- cost-ledger failure;
- SSE disconnect;
- multi-instance/concurrency;
- load/overhead measurement;
- exceptional archive burst/backpressure/outage/queue saturation, partial trace/chunk loss, retry/idempotency, tail-sampling memory, Collector/bridge restart, malformed OTLP, simulated GCS latency/errors and reconstruction performance. Live checks require exact approval; isolated failure tests can run locally.

### Exit
- observability still explains the system when dependencies fail;
- instrumentation overhead is within agreed budget.

---

## M17 — Production Operationalization

### Build
- final dashboards;
- final alerts;
- runbooks;
- owners;
- SRE and FinOps review cadence;
- production release checklist;
- separately approved exceptional archive bridge deployment, dedicated environment-isolated GCS bucket/lifecycle, service identity, least-privilege writer/reader IAM, monitoring/alerting and retrieval runbook; live transport/lifecycle/retrieval validation. No activation by roadmap implication.

### Exit
All applicable checks in `15_DEFINITION_OF_DONE.md` pass.

## Dependency rules

- M8 depends on stable M2–M7 data contracts.
- M10 depends on M3 usage accuracy.
- M11–M14 depend on durable M10 ledger.
- aggressive trace sampling must wait until M9 baselines and M15 policy exist.


---

## M18 — Release/CI/IaC Hardening

### Build
- W3C Trace Context;
- release/config/model/prompt/tool schema correlation;
- Terraform observability-as-code;
- CI observability contract gates;
- deployment annotations.

### Exit
- production regressions correlate to exact release/config;
- manual observability drift is detectable;
- CI blocks telemetry contract violations.

---

## M19 — Quality/Outcome + Continuous Profiling

### Build
- outcome model from `19_AI_QUALITY_OUTCOMES.md`;
- cost-quality joins;
- CPU/memory continuous profiling.

### Exit
- FinOps can compare cost/latency against available quality outcomes;
- profile data correlates with release/revision;
- profiling overhead measured.

## Separate architecture workstream — Observability Console + Read Plane

Introduced after the M16 validation baseline; **not M17** and not a new numbered
milestone. Doc [25](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md) defines a
documentation-only decision and future gated phases for GET composition, isolated
reads, transport, console/Settings migration, integrated qualification and reviewed
production handoff. Application implementation requires subsequent authorization.
M0–M19 sequence and M16 exit criteria remain unchanged. M16 is incomplete; C41 and
C42 remain FAIL; Ready for Next Milestone NO. Final unchanged CPU/regression
acceptance must be rerun after integration. This workstream cannot bypass the CPU
defect or authorize M17/cloud mutation.

## Hybrid event architecture workstream — no milestone advancement

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) records the user's authorized architecture/local-scaffolding pass. It adds
neither M17 production implementation nor permission to bypass M16. Future integration
of atomic audit outbox, separate publisher/projector and Collector operational branch
must undergo crash/duplicate/conflict/backpressure/privacy/authority validation and
final unchanged C41/C42 CPU/regression qualification. M16 INCOMPLETE; M17 UNSTARTED;
Ready for Next Milestone NO. Cloud operations remain separately approval-gated.
