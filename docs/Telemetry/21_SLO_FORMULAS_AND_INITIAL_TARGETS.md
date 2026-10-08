
# 21 — SLO Formulas and Initial Production Targets

## Principle

Definitions are fixed now. Numeric targets are provisional and recalibrated after a production baseline.

## Measurement window

Initial:
- rolling 28-day SLO window,
- operational dashboards also show 1h / 6h / 24h / 7d.

## Accepted valid request

A denominator-eligible request:
- passes authentication/authorization,
- passes request schema validation,
- enters canonical turn execution.

Excluded:
- rejected malformed requests,
- explicit client cancellation before execution begins,
- confirmed user/client-originated cancellation during execution (availability only),
- synthetic requests from business unit economics.

## Availability SLI

Numerator:
turns ending in a valid terminal application outcome:
- completed answer,
- valid clarification,
- valid source gap,
- valid approval request,
- safe policy rejection.

Denominator:
accepted valid requests, excluding confirmed user/client cancellation. Service/system
cancellation, deadline timeout and unknown cancellation origin remain eligible bad
events. Origin comes from bounded server metadata, never free-form text.

Infrastructure/model/provider failures are not successful.

Initial target:
- 99.5%.

## Terminal completion SLI

Numerator:
accepted requests reaching exactly one COMPLETED, FAILED, TIMEOUT or CANCELLED
canonical terminal state within maximum turn deadline. This denominator includes
user cancellations and is independent of successful application outcome.

Denominator:
accepted valid requests.

Initial target:
- 99.9%.

## Latency SLOs

Measure server end-to-end duration from API accepted to terminal server completion.

### General
Initial: at least 95% of classified terminal turns have root duration <= 8 s.

### Teams lookup
Initial: at least 95% of Teams-classified terminal turns have root duration <= 20 s.
This measures the complete server turn, not the Power Automate dependency call.

### Governed troubleshooting
Initial: at least 95% of governed terminal turns have root duration <= 30 s.

### Complex multi-agent
Initial: at least 95% of complex terminal turns have root duration <= 45 s.

Targets are provisional and recalibrated from real baseline without weakening safety.

## First-token SLO

For streaming user provider operations:
at least 95% of valid first_provider_output observations <= 5 s. This is not a
literal token boundary. Unknown/unavailable observations do not enter the evaluated
ratio and remain visible in measurement coverage; poor coverage is not healthy.

Server-rendered clarification/source-gap responses are excluded from model TTFT.

## Trace completeness SLI

Numerator:
terminal turns containing all required trace phases applicable to that turn.

Denominator:
terminal turns.

Initial target:
- 99.9%.

## Cost-ledger completeness SLI

Numerator:
billable provider operations with exactly one persisted accounting usage event.

Denominator:
billable provider operations observed by provider-call instrumentation.

Target:
- 100% design target;
- alert if < 99.99% over 24h.

## SSE delivery SLI

Numerator:
server `message.completed` events observed by browser completion telemetry when browser remains connected.

Denominator:
eligible connected SSE turns.

Initial target:
- 99.9%.

## Model reliability SLI

Numerator:
provider calls completing successfully.

Denominator:
provider calls excluding explicit client cancellation.

Initial target:
- 99.5%, recalibrated by provider/model.

## Dependency reliability

Track separately for:
- Graph,
- DB,
- storage,
- knowledge.

Initial target:
- 99.5% successful operations per dependency class, subject to baseline.

## Safety SLIs

Zero tolerance:
- unauthorized command exposure,
- command egress bypass,
- credential/secret telemetry leak,
- hidden chain-of-thought persistence.

Any occurrence = incident, not error-budget consumption.

## Error budget

```text
allowed_bad = eligible_events * (1 - SLO_target)
consumed_budget = observed_bad / allowed_bad
remaining_budget = 1 - consumed_budget
```

Zero-tolerance safety SLIs use incident semantics instead.

## Burn-rate alerts

M9 implementation correction (user-approved 2026-10-08) supersedes the prior
1h/6h proposal. Each pair requires both window burn rates strictly above threshold:
- fast page: long 1h / short 5m, >14.4x;
- sustained page: long 6h / short 30m, >6x;
- slow ticket: long 3d / short 6h, >1x.

Require at least 100 eligible measured events in each paired window, fresh complete
coverage and no unknown events. These are PROVISIONAL — CALIBRATION REQUIRED.
One combined PromQL condition represents a logical page, with a separate slow
policy. Cloud Monitoring may still create repeated incidents/notifications;
receiver grouping uses environment/service/SLO family. Policies are disabled and
undeployed. No optional intermediate tier is active.

## M9 canonical implementation

`backend/observability/slo_contract.py` is the central executable definition registry.
The deterministic evaluator uses UTC rolling 28 days with half-open [start,end)
minute boundaries and a deadline/ingest settlement watermark. Latency pass/fail is
an event ratio, never a separately interpolated p95 decision. Classification
precedence is complex multi-agent > governed troubleshooting > registered Teams
lookup > explicit general routing > UNKNOWN. Unknown class/timing remains coverage.

Trace completeness is an unsampled applicability-aware runtime receipt, separate
from trace retention/export. SSE completion receipts are authenticated, idempotent,
owner-scoped diagnostics only. Receipt delivery cannot change the business turn.
Missing receipts may be bad delivery observations or unknown transport coverage;
they are not automatically backend failure. Confirmed disconnect before expected
completion is excluded. Direct Graph remains source-unavailable: the directly
observed dependency is Power Automate / Teams gateway.

Cost-ledger completeness exposes definition metadata only: DEFINED_NOT_EVALUATED,
M10_COST_LEDGER, DATA_SOURCE_AVAILABLE_IN_M10; no ratios, budgets or finite burn.
No financial accounting or pricing is implemented in M9.

