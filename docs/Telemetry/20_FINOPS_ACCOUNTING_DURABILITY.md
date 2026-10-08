
# 20 — FinOps Accounting-Grade Durability

## Production decision

The usage/cost ledger is accounting-grade, append-oriented, idempotent and unsampled.

A model call must not disappear financially because a trace was sampled or an exporter failed.

## Deterministic usage event identity

Every billable provider operation gets deterministic `usage_event_id`.

Recommended composition:
- run_id,
- stable call sequence or call UUID generated before provider invocation,
- provider,
- operation.

Database enforces uniqueness.

Retries are separate usage events when they incur provider usage.

## Write semantics

Preferred:
1. create call identity before provider request;
2. execute provider call;
3. receive usage;
4. write immutable ledger event;
5. roll up turn summary asynchronously or transactionally as appropriate.

If ledger persistence fails:
- record `COST_LEDGER_PERSIST_FAILED`,
- retry via durable outbox/recovery path,
- do not silently discard usage.

## Outbox/recovery

Use a durable recovery mechanism for ledger persistence failures.

Acceptable:
- transactional outbox in Cloud SQL,
- equivalent durable queue.

In-memory retry alone is insufficient.

## Immutable corrections

Do not overwrite historical usage records after reconciliation.

Corrections use:
- correction/reversal event,
- reference to original usage event,
- reason,
- actor/process,
- timestamp.

## Late-arriving usage

Support:
- provisional estimated usage,
- later provider-reported usage,
- reconciliation state,
- adjustment event.

## Price model

Use component/tariff rows, not only fixed input/output columns.

### `finops_price_component`

```text
price_component_id
price_version
provider
service
sku
model
region
charge_type
modality
tier_lower_bound
tier_upper_bound
unit
unit_price
currency
effective_from
effective_to
source
```

`charge_type` examples:
- input_token,
- output_token,
- cached_input_token,
- cache_write,
- request,
- image,
- audio,
- batch,
- embedding,
- storage,
- compute.

## Billing source decision

Primary production accounting inputs:
1. GCP Detailed Billing Export.
2. GCP Pricing Export/catalog.

FOCUS export:
- supplementary normalized cross-provider dataset,
- not the sole authoritative source until lifecycle/governance explicitly approves it.

## Reconciliation states

```text
UNRECONCILED
PARTIALLY_RECONCILED
RECONCILED
VARIANCE_REVIEW
CORRECTED
```

## Coverage controls

Metrics:
- provider calls observed,
- usage events persisted,
- missing usage count,
- outbox backlog,
- reconciliation lag,
- unallocated billed cost.

Target:
- effectively 100% usage-ledger coverage.

Any gap is visible.
