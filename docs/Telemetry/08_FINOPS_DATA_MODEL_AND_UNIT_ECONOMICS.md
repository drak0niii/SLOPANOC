# 08 — FinOps Data Model and Unit Economics

## `ai_usage_ledger`

Suggested fields:

```text
usage_event_id
timestamp
run_id
turn_id
trace_id
span_id
provider
service
model
sku
operation
agent
domain
input_tokens
output_tokens
cached_input_tokens
cached_output_tokens
request_count
retry_count
quantity
unit
estimated_cost
currency
price_version
pricing_source
usage_estimated
environment
use_case_key
team_key
cost_center_key
created_at
```

Coverage target: 100%. No sampling.

## Price catalog

`finops_price_catalog`:

```text
price_version
provider
service
sku
model
region
effective_from
effective_to
input_unit_price
output_unit_price
cached_input_unit_price
request_unit_price
other_unit_price
billing_unit
currency
source
loaded_at
```

Historical prices used by ledger records must remain reproducible.

## Turn cost summary

`finops_turn_cost`:

```text
run_id
turn_id
session_id
estimated_direct_cost
estimated_shared_cost
estimated_total_cost
billed_direct_cost
allocated_shared_cost
reconciled_total_cost
currency
model_cost
graph_cost
database_cost
storage_cost
observability_cost
input_tokens
output_tokens
allocation_version
reconciliation_status
```

## Billing normalization

Stable views:
- `finops_billing_normalized`,
- `finops_pricing_normalized`,
- `finops_resource_cost_daily`.

Include project/service/SKU/resource/labels/credits/gross/net cost/usage quantity/unit/date.

## Reconciliation

Compare runtime estimates and provider billing at realistic levels:
1. model/provider direct usage,
2. service/day,
3. project/day,
4. billing period.

Shared infrastructure cannot be expected to reconcile exactly per turn.

Store:
- estimated,
- billed,
- variance,
- variance percentage,
- reason/status.

## Cost allocation

`finops_cost_allocation`:

```text
period_start
period_end
source_cost_pool
environment
service
target_type
target_key
direct_cost
shared_cost
allocated_cost
allocation_driver
allocation_method
allocation_confidence
allocation_version
currency
```

## Unit economics

### Technical units
- cost / turn,
- cost / completed turn,
- cost / model call,
- cost / specialist call,
- cost / governed answer,
- cost / Teams lookup,
- cost / knowledge retrieval,
- cost / authorized action.

### Operational/business units
- cost / troubleshooting case,
- cost / resolved incident,
- cost / first-time-correct outcome,
- cost / automation artifact,
- cost / use case,
- cost / SDU/team.

If outcome data does not yet exist, define the join/schema but mark the metric unavailable. Never fabricate business value.

## Quality + latency + cost

The data model must support comparison by model/agent/version:

```text
model A
  cost/turn
  p95 latency
  quality / first-time-correct
  governed-answer rate

model B
  ...
```

This enables optimization without optimizing cost at the expense of quality or safety.

## Budgets

`finops_budget`:

```text
budget_id
scope_type
scope_key
period
currency
budget_amount
warning_threshold_pct
critical_threshold_pct
forecast_amount
actual_amount
owner
```

Scopes:
- SLOPANOC total,
- environment,
- team/SDU,
- model,
- agent,
- use case.

## Forecasting

Start with transparent methods:
- daily run-rate,
- MTD trend,
- month-end projection,
- seasonality after sufficient history.

Do not begin with opaque ML forecasting.

## Anomaly detection

Detect:
- cost/turn jump,
- tokens/turn jump,
- calls/turn jump,
- retry-driven spend,
- failed-turn spend,
- observability cost spike,
- reconciliation variance spike.

Every anomaly includes baseline, observed value, variance, dimensions and sample run references where access policy permits.


## Accounting-grade ledger semantics

The usage ledger is immutable/idempotent and follows `20_FINOPS_ACCOUNTING_DURABILITY.md`.

Do not implement pricing solely as fixed input/output columns. Use a component/tariff model capable of:
- token type,
- modality,
- tier,
- batch mode,
- request charge,
- cache charge,
- embeddings,
- storage/compute.

Quality/value joins use `19_AI_QUALITY_OUTCOMES.md`.
