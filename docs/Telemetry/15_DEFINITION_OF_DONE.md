# 15 — Production Definition of Done

The build is complete only when every applicable item is demonstrably true.

## Correlation

- [ ] Every turn has `trace_id`.
- [ ] Every turn has trusted `run_id`, `turn_id`, `session_id`.
- [ ] Nested agent/tool/dependency spans preserve context.
- [ ] Browser and backend correlate by run.

## Runtime observability

- [ ] Request acceptance visible.
- [ ] Session load visible.
- [ ] Turn planning visible.
- [ ] Thread/pending interaction visible.
- [ ] Context Broker visible.
- [ ] Every agent visible.
- [ ] Every model call visible.
- [ ] Every tool call visible.
- [ ] Every external dependency wait visible.
- [ ] Final authority visible.
- [ ] Provenance summary visible.
- [ ] Command egress visible.
- [ ] Persistence visible.
- [ ] SSE completion visible.
- [ ] UI render visible.

## Stuck request diagnosis

- [ ] Active runs queryable.
- [ ] Current stage/agent/tool/dependency queryable.
- [ ] Stage elapsed time queryable.
- [ ] Last progress queryable.
- [ ] Watchdog emits stalled state.
- [ ] Heartbeat differentiates alive/disconnected.
- [ ] All external waits have deadlines.
- [ ] No indefinite processing state.

## Reliability/SRE

- [ ] Structured error taxonomy.
- [ ] Retry accounting.
- [ ] Timeout/stall metrics.
- [ ] p50/p95/p99.
- [ ] Availability/completion SLOs.
- [ ] Model/tool/dependency reliability metrics.
- [ ] SSE delivery metric.
- [ ] Trace completeness metric.
- [ ] Error budgets where applicable.
- [ ] Zero-tolerance safety alerts.
- [ ] Synthetic monitoring.
- [ ] Runbooks.

## FinOps runtime usage

- [ ] Every billable model call creates ledger record.
- [ ] Input/output token usage recorded.
- [ ] Cached tokens recorded where available.
- [ ] Retries included.
- [ ] Estimated usage explicitly marked.
- [ ] Every turn has estimated direct cost.
- [ ] Price version stored.
- [ ] Cost ledger coverage measured and effectively 100%.
- [ ] Ledger is not sampled.

## Billing/reconciliation

- [ ] Billing export enabled.
- [ ] Pricing data available.
- [ ] Stable normalized billing views.
- [ ] Estimated vs billed reconciliation.
- [ ] Variance visible.
- [ ] Direct/shared/unallocated classification.
- [ ] Allocation method/version/confidence.
- [ ] Unallocated cost visible.

## Unit economics

- [ ] Cost/turn.
- [ ] Cost/completed turn.
- [ ] Cost/model and agent.
- [ ] Cost/governed answer.
- [ ] Cost/Teams investigation.
- [ ] Cost/knowledge retrieval.
- [ ] Cost/troubleshooting case where outcomes exist.
- [ ] Cost/resolved incident where outcomes exist.
- [ ] Quality/latency/cost comparison available.

## Budget/forecast

- [ ] Total SLOPANOC budget.
- [ ] Environment budgets.
- [ ] Team/use-case/model budgets are implemented when those allocation dimensions are available; otherwise the dimension is explicitly marked unavailable rather than silently omitted.
- [ ] MTD actual.
- [ ] Month-end forecast.
- [ ] Cost anomalies.
- [ ] Token/model-call anomalies.
- [ ] Observability cost tracked and budgeted.

## Security/privacy

- [ ] Central redaction.
- [ ] No credentials in telemetry.
- [ ] No hidden chain-of-thought.
- [ ] No raw Teams bodies by default.
- [ ] No full prompts/responses by default.
- [ ] RBAC server-enforced.
- [ ] FinOps/content access separated.
- [ ] Retention implemented: Cloud Trace fixed 30 days; sanitized full exceptional archive 90-day primary horizon, distinct from summaries; provider lifecycle/soft-delete semantics disclosed.
- [ ] Archive full reconstruction, privacy, eligibility, idempotency and technical-capability/environment isolation locally validated.
- [ ] Archive completeness/health separate from instrumentation completeness and Cloud Trace export.
- [ ] Live exceptional archive bridge/GCS/IAM/lifecycle/retrieval and production failure validation completed under exact-operation approval for full production completion (not claimed by M15 local acceptance).
- [ ] Sampling implemented.
- [ ] Deletion/anonymization interaction documented.
- [ ] Secret-leakage tests.

## Platform health

- [ ] Exporter health visible.
- [ ] Dropped telemetry visible.
- [ ] Cost-ledger failures visible.
- [ ] Instrumentation overhead measured.
- [ ] Observability cost visible.
- [ ] Multi-instance behavior validated if applicable.

## Acceptance scenarios

### A — Successful Teams lookup
Trace shows planning -> IM -> Graph -> synthesis -> authority/provenance -> cost -> UI completion.

### B — Stuck Graph call
Current dependency and elapsed time are visible, stall emitted, timeout eventually terminal.

### C — Governed troubleshooting
Trace shows TAE -> knowledge search/selection -> ProcedureAction -> Command Authority -> provenance -> egress -> cost.

### D — Model reasoning
Non-authoritative reasoning provenance/disclosure visible; it grants no command authority.

### E — Cost anomaly
FinOps identifies affected model/agent/use-case, token/call change, estimated/billed impact and safe run references.

### F — Telemetry outage
SLOPANOC continues safely while observability degradation is itself visible and alerted.

## Final quality statement

The design qualifies as a 9.5+ production implementation only when it provides:

> **End-to-end traceability + measurable reliability + safe operational diagnosis + 100% runtime usage accounting + provider billing reconciliation + allocation + unit economics + budget/anomaly control + security/privacy + operational ownership.**


## Production backend / infrastructure

- [ ] OTel Collector deployed/configured as code.
- [ ] Traces exported to Cloud Trace.
- [ ] Metrics exported to Cloud Monitoring.
- [ ] Structured logs exported to Cloud Logging.
- [ ] Collector health monitored.
- [ ] Cloud Run infrastructure monitored.
- [ ] Cloud SQL infrastructure monitored.
- [ ] BigQuery FinOps workload monitored.
- [ ] Graph and GCS dependency telemetry present.
- [ ] Cross-instance active-run status works through Cloud SQL.

## Release/config correlation

- [ ] W3C Trace Context from browser to backend.
- [ ] Git SHA on traces.
- [ ] deployment/revision ID on traces.
- [ ] model/version recorded.
- [ ] prompt/agent contract version recorded.
- [ ] tool schema version recorded.
- [ ] TurnPlan/telemetry/cost/pricing versions recorded.
- [ ] deployment markers appear in dashboards.

## Accounting-grade FinOps

- [ ] deterministic usage_event_id.
- [ ] duplicate protection.
- [ ] durable outbox/recovery.
- [ ] correction/reversal semantics.
- [ ] late-arriving usage supported.
- [ ] component/tariff price model implemented.
- [ ] Detailed Billing + Pricing primary sources.
- [ ] FOCUS supplemental only.
- [ ] EUR reporting plus native currency retained.

## Quality/value

- [ ] governed-answer outcome available.
- [ ] operator acceptance where explicitly captured.
- [ ] troubleshooting completion/resolution/escalation available.
- [ ] action success available where trusted.
- [ ] FTC is only shown when measurable.
- [ ] cost-quality-latency joins available.

## Observability-as-code / CI

- [ ] Terraform manages supported dashboards/alerts/BigQuery/IAM/retention resources.
- [ ] Collector config source-controlled.
- [ ] CI rejects unknown telemetry vocabulary.
- [ ] CI rejects metric cardinality violations.
- [ ] CI rejects prohibited telemetry fields.
- [ ] CI validates usage-ledger path for billable model calls.
- [ ] CI validates schema/version discipline.

## Profiling

- [ ] continuous CPU profiling enabled.
- [ ] memory profiling enabled where platform-safe.
- [ ] profile data correlates with release.
- [ ] profiling overhead measured and accepted.

## Console/read-plane production acceptance additions

Doc [25](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md) adds the following production
requirements; these unchecked items do not alter historical milestone results.

- [ ] Primary console `/resources/observability` uses the existing shell and preserves conversational state.
- [ ] Effective policy/configuration uses the current doc 25 Resources Configuration placement, read-only and without duplicate operational dashboards.
- [ ] Independent read app exposes only reviewed protected GETs and transport preflight; SSE receipts remain on ANOC.
- [ ] Reader startup/requests invoke no models, agents, tools, commands, financial jobs, retention jobs or business mutations.
- [ ] Independent API base/origin/auth/read DB bindings validated; existing verified capabilities/environment/cursor/privacy enforcement preserved.
- [ ] Reader outage/overload does not consume ANOC workers or trigger business replay; shared DB/resource contention is bounded.
- [ ] Producer health/effective policy, financial generations and trace retention remain truthful, scoped and source-backed; missing values are unavailable.
- [ ] Final unchanged M16 CPU and regression acceptance rerun after integration; C41/C42 not waived.
- [ ] Exact-operation-approved live isolation/read-privilege validation completed before production readiness is claimed.

## Hybrid production acceptance additions

See [26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md). These remain unvalidated production requirements:

- [ ] Critical records + outbox proven atomic under actual SQL rollback/crash.
- [ ] Separate audit/operational lanes, retry/DLQ/capacity and debt/loss visible.
- [ ] Publisher claim/lease/ack crash recovery idempotent; no business replay.
- [ ] Projector writes only approved projections; receipt/update atomic and conflict-safe.
- [ ] Pinned Collector privacy-before-Pub/Sub and bounded OTLP normalization proven.
- [ ] Historical capture/unknown/source generations/freshness truthful.
- [ ] Complementary application/pgAudit/GCP audit privacy and maintenance validated.
- [ ] Background FinOps generations preserve M10–M14 authority; GET starts no job.
- [ ] Final unchanged C41/C42 and integrated failure/load qualification PASS.
- [ ] Exact approved resource/schema/role activation and live validation completed.

These checkboxes grant no mutation permission and mark no milestone complete.
