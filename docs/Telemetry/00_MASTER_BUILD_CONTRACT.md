# 00 — Master Build Contract

## Mission

Build a production-grade **Observability + Reliability + FinOps Control Plane** for SLOPANOC.

The finished platform must include:

- distributed traces,
- correlated structured logs,
- metrics,
- active-run visibility,
- model/agent/tool/dependency instrumentation,
- timeouts, watchdogs and structured failures,
- admin observability APIs,
- frontend progress and trace views,
- SRE SLOs and alerts,
- 100% AI usage ledger,
- runtime cost estimation,
- provider billing ingestion and reconciliation,
- direct/shared/unallocated cost allocation,
- unit economics,
- budgets, forecasting and anomaly detection,
- RBAC, redaction, retention and sampling,
- dashboards and runbooks.

This work must not change SLOPANOC's application governance semantics.

## Architectural planes

### Telemetry plane
Records what happened:
- traces,
- spans,
- events,
- logs,
- metrics,
- current stage,
- timing,
- bounded safe metadata.

### Reliability plane
Controls approved runtime protection:
- deadlines/timeouts,
- safe retries,
- cancellation,
- watchdog/stall detection,
- circuit breaking where justified,
- structured error taxonomy.

### FinOps plane
Records and reconciles:
- model tokens,
- request counts,
- retried usage,
- infrastructure/service usage,
- estimated cost,
- billed cost,
- allocation,
- unit economics,
- budgets/forecast.

### Governance plane
Existing application governance remains authoritative:
- TurnPlan,
- Context Broker,
- knowledge selection,
- applicability,
- ProcedureAction,
- Command Authority,
- approval,
- command egress,
- TurnAuthority,
- claim provenance.

Telemetry may mirror these decisions but never replace or influence them.

## Fixed production decisions

These decisions are approved and must not be re-opened by the coding agent:

- OTel Collector -> Cloud Trace / Cloud Monitoring / Cloud Logging.
- Cloud SQL is the durable cross-instance active-run status store; in-memory state is a cache.
- Production logs are structured JSON.
- Infrastructure telemetry covers Cloud Run, Cloud SQL, Gemini/Vertex, Graph, BigQuery, GCS, knowledge, network dependencies and the Collector.
- Initial SLO targets are defined in `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md` and later recalibrated from baseline evidence.
- Trace retention: normal successful traces 30 days; errors/timeouts/stalled/governed operational traces 90 days.
- Aggregate metrics retention target: 13 months.
- FinOps ledger/billing analytical retention target: 24 months, subject to enterprise policy.
- Sampling starts at 100%; later healthy general-chat traces may be reduced to 10–20%, while failed/timeout/stalled/safety/governed turns remain 100%.
- FinOps ledger is immutable/idempotent/accounting-grade with durable recovery.
- Primary billing source: Detailed Billing Export + Pricing; FOCUS is supplemental.
- Reporting currency: EUR, retaining native provider currency.
- Quality/value telemetry covers governed-answer rate, operator acceptance, FTC where measurable, incident resolution, escalation, troubleshooting completion, action success and automation-artifact success.
- Every trace correlates Git SHA, release/revision, model, prompt/agent/tool/schema/config versions.
- Browser/backend propagation uses W3C Trace Context.
- Terraform is the observability-as-code standard.
- CI/CD contains observability contract gates.
- Continuous profiling is included.
- RBAC roles: User, Operator, Developer/SRE, FinOps, Admin, Auditor; FinOps cannot read conversation/Teams content by default.
- Observability cost is budgeted and alerted as part of total SLOPANOC cost.

## Production invariants

### Trace invariants
- one user turn -> one canonical root trace;
- stable `trace_id`, `run_id`, `turn_id`, `session_id`;
- child work uses parent/child spans;
- every terminal turn ends exactly once as `COMPLETED`, `FAILED`, `TIMEOUT` or `CANCELLED`;
- `STALLED` is a watchdog state/event and may later transition to a terminal state;
- all spans are closed in success and failure paths.

### Safety invariants
Never store by default:
- passwords,
- access tokens,
- API keys,
- authorization headers,
- full Teams message bodies,
- full knowledge document text,
- attachment contents,
- full prompts/responses,
- hidden chain-of-thought.

### FinOps invariants
- every billable model call creates a durable usage record;
- every turn can roll up child usage;
- `estimated_cost` and `billed_cost` are separate;
- pricing is versioned;
- cost estimates are reproducible;
- usage/cost ledgers are never trace-sampled;
- shared allocation stores method, driver, version and confidence;
- unallocated cost is visible.

### SRE invariants
- all blocking external calls have configured deadlines;
- no request remains indefinitely ambiguous;
- active-run current stage is queryable;
- observability exporter failure is itself measurable;
- safety violations such as unauthorized command exposure have zero tolerance.

## Target package

Prefer a modular package resembling:

```text
backend/observability/
├── __init__.py
├── config.py
├── schemas.py
├── attributes.py
├── stages.py
├── tracing.py
├── metrics.py
├── logging.py
├── instrumentation.py
├── model_instrumentation.py
├── tool_instrumentation.py
├── dependency_instrumentation.py
├── watchdog.py
├── errors.py
├── redaction.py
├── persistence.py
├── repositories.py
├── api_models.py
├── finops/
│   ├── usage_ledger.py
│   ├── pricing.py
│   ├── estimator.py
│   ├── reconciliation.py
│   ├── allocation.py
│   ├── unit_economics.py
│   ├── budgets.py
│   └── forecasting.py
└── exporters/
    ├── otlp.py
    └── local.py
```

Adapt names to repository conventions, but do not collapse all responsibilities into a monolith.

## Configuration

Add explicit environment-driven settings, including at minimum:

```text
SLOPANOC_OBSERVABILITY_ENABLED
SLOPANOC_OTEL_ENABLED
SLOPANOC_OTEL_EXPORTER_OTLP_ENDPOINT
SLOPANOC_OTEL_SERVICE_NAME
SLOPANOC_OTEL_ENVIRONMENT
SLOPANOC_TRACE_SAMPLE_RATE
SLOPANOC_TRACE_ERRORS_ALWAYS
SLOPANOC_TRACE_GOVERNED_TURNS_ALWAYS

SLOPANOC_MODEL_TIMEOUT_SECONDS
SLOPANOC_GRAPH_TIMEOUT_SECONDS
SLOPANOC_KNOWLEDGE_TIMEOUT_SECONDS
SLOPANOC_DATABASE_TIMEOUT_SECONDS
SLOPANOC_STORAGE_TIMEOUT_SECONDS
SLOPANOC_WATCHDOG_STALL_SECONDS
SLOPANOC_HEARTBEAT_SECONDS

SLOPANOC_FINOPS_ENABLED
SLOPANOC_FINOPS_CURRENCY
SLOPANOC_FINOPS_PRICE_SOURCE
SLOPANOC_FINOPS_BILLING_PROJECT
SLOPANOC_FINOPS_BILLING_DATASET
```

Critical policy must not be hidden in magic constants.

## Delivery rule

Implement milestone by milestone. Each milestone must leave the repository importable, migration-safe and compatible with existing safety controls.
