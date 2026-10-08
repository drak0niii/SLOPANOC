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
- [ ] Retention implemented.
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
