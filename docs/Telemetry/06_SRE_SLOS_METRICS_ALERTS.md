# 06 — SRE, SLOs, Metrics and Alerts

## Objective

Move SLOPANOC from subjective health assessment to measurable reliability.

## Golden signals

### Traffic
- turns/minute,
- active turns,
- model calls,
- tool calls.

### Latency
- turn p50/p95/p99,
- time to first token,
- planning latency,
- agent latency,
- Graph/knowledge/DB latency.

### Errors
- terminal failures,
- timeouts,
- planning failures,
- source gaps,
- persistence/export failures.

### Saturation
- concurrency,
- DB pool saturation,
- provider rate limits,
- exporter queue,
- active-run age.

## Core metric catalog

Counters:

```text
slopanoc_turn_total
slopanoc_turn_failed_total
slopanoc_turn_timeout_total
slopanoc_turn_stalled_total
slopanoc_planning_failed_total
slopanoc_source_gap_total
slopanoc_model_request_total
slopanoc_tool_request_total
slopanoc_model_reasoning_disclosure_total
slopanoc_command_authorized_total
slopanoc_command_rejected_total
```

Histograms:

```text
slopanoc_turn_duration_ms
slopanoc_model_duration_ms
slopanoc_model_ttft_ms
slopanoc_tool_duration_ms
slopanoc_context_tokens
slopanoc_input_tokens
slopanoc_output_tokens
```

Gauges:
- active turns,
- stalled turns,
- exporter queue/degraded state.

Labels must remain bounded: environment, domain, agent, tool, dependency, model, provider, status, error code.

## SLOs

Use the exact SLI formulas and provisional targets in `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md`. Recalibrate numeric targets only after baseline evidence; do not change numerator/denominator semantics without design review.

Implement definitions for:

### Availability
Valid terminal outcomes / accepted valid requests.
Clarification and safe source-gap outcomes are not necessarily infrastructure failures.

### Completion
Turns that reach a terminal state and are not orphaned.

### Latency
Maintain separate SLO classes for:
- general,
- Teams,
- troubleshooting,
- complex/multi-agent.

### Model/tool reliability
Success rate per provider/tool.

### SSE delivery
Backend completion emitted vs browser completion received.

### Trace completeness
Turns with required trace stages / turns.

### Cost-ledger completeness
Billable calls with usage record / billable calls. Target effectively 100%.

## Error budgets

Use error budgets for reliability SLOs.

Do not use an error budget for safety events such as unauthorized command exposure or secret leakage. These are zero-tolerance incidents.

## Alerts

### Critical
- unauthorized command exposure,
- credential/secret in telemetry,
- sustained application outage,
- severe DB persistence outage,
- severe cost-ledger coverage loss.

### High
- turn failure spike,
- p95 latency violation,
- model/Graph timeout spike,
- stalled active runs,
- SSE mismatch,
- sustained trace exporter failure.

### Medium
- source-gap spike,
- planning failure increase,
- token/cost per turn anomaly,
- observability cost spike.

For mature SLOs use multi-window burn-rate alerts.

## Synthetic monitoring

Implement safe synthetic checks for:
- general prompt,
- known Teams lookup if an approved synthetic tenant/workspace exists,
- governed knowledge query,
- DB/session path,
- SSE path.

Tag synthetic usage so it is excluded from business unit economics or shown separately.


## Infrastructure observability

Infrastructure metrics and alerts in `17_INFRASTRUCTURE_OBSERVABILITY.md` are mandatory parts of the SRE dashboard.
