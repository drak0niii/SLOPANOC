# 10 — Dashboards, Runbooks and Operating Model

## Dashboard: SLOPANOC Health

Show:
- request rate,
- active/stalled,
- completed/failed/timeout,
- p50/p95/p99,
- SLO/error budget,
- error codes,
- exporter health.

## Dashboard: AI / Model

Show:
- calls by model/agent,
- TTFT,
- total model latency,
- input/output tokens,
- retries,
- planning failures,
- provider errors,
- model cost.

## Dashboard: Agents

For Team Manager, TAE, Incident Manager, Problem Manager and AOE:
- volume,
- latency,
- failures,
- token usage,
- cost,
- role if useful.

## Dashboard: Dependencies

Microsoft Graph, Cloud SQL, knowledge, storage, model provider:
- success,
- p95 latency,
- timeout,
- retry,
- rate limit,
- saturation.

## Dashboard: Governance / Safety

- governed answer volume,
- source gaps,
- model-reasoning disclosures,
- command authority authorized/rejected,
- approvals,
- egress removals,
- provenance coverage,
- safety events.

## Dashboard: FinOps Executive

- MTD spend,
- forecast,
- budget variance,
- spend by environment/team/use case/model/agent,
- estimated vs reconciled variance,
- unallocated cost,
- observability cost ratio.

## Dashboard: Unit Economics

- cost/turn,
- cost/completed turn,
- cost/governed answer,
- cost/troubleshooting case,
- cost/resolved incident where available,
- quality/latency/cost comparisons.

## Mandatory runbooks

### Stuck request
1. obtain `run_id`;
2. inspect active current stage;
3. inspect current/critical span;
4. identify agent/tool/dependency;
5. check timeout/watchdog;
6. check provider health;
7. safely retry/terminate only if policy allows;
8. attach run to incident record.

### Model timeout spike
Check provider, rate limits, retries, model-specific pattern and approved fallback policy.

### Graph failure
Check auth, permissions, throttling, provider status, latency/timeouts.

### Database degradation
Check pool saturation, connection failures, slow queries and Cloud SQL health.

### Planning failures
Check invalid plans, model/tool compliance, prompt/tool declaration changes.

### Cost anomaly
Identify model/agent/use case, token/call/retry change, quality impact and rollback/optimization option.

### Telemetry outage
Confirm product availability, exporter health, dropped telemetry, FinOps coverage risk.

### Safety event
Immediate escalation for command/provenance/secret failures; zero-tolerance process.

## Ownership

Assign named owners for:
- observability platform,
- SRE/SLO,
- FinOps,
- agents/tools,
- security/privacy.

Dashboards without owners become stale.

## Cadence

Recommended:
- daily automated health/anomaly check,
- weekly SLO/incident review,
- weekly/biweekly AI quality-cost review,
- monthly FinOps reconciliation/budget review,
- quarterly retention/cardinality review.


## Observability cost guardrail

Track and budget:
- trace ingestion,
- log ingestion,
- metric ingestion,
- Collector compute,
- BigQuery storage/query,
- retention.

Executive FinOps dashboard shows:

```text
observability_cost / total_slopanoc_cost
```

Alert thresholds are configured during production baseline and reviewed monthly.

## Infrastructure dashboard

Mandatory infrastructure panels are defined in `17_INFRASTRUCTURE_OBSERVABILITY.md`.

## Continuous profiling dashboard

Include release-correlated CPU/memory profile comparisons from `24_CONTINUOUS_PROFILING.md`.
