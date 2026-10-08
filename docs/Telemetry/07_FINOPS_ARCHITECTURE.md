# 07 — FinOps Architecture

## Mission

FinOps must answer:
- what SLOPANOC costs,
- what each turn/agent/model/tool costs,
- who or what should receive allocation,
- how runtime estimated cost reconciles to provider billing,
- which workloads produce value efficiently,
- where cost anomalies/optimization opportunities exist.

FinOps is a first-class production subsystem.

## Two cost truths

### Runtime estimated cost
Available near turn completion.

Calculated from:
- model input/output/cached token usage,
- embedding/model usage,
- priceable API calls,
- measurable direct resource usage,
- optional allocated infrastructure estimate.

Store amount, currency, price version and method.

### Provider billed cost
Financial truth from provider/cloud billing export.

Store separately:
- gross cost,
- credits/discounts,
- net/effective cost,
- billing period,
- resource/SKU dimensions.

Never overwrite estimated cost with billed cost.

## FinOps pipeline

```text
Runtime usage events
       |
       +--> usage ledger --> price engine --> real-time estimated cost
       |
Provider billing export
       |
Pricing catalog
       |
Resource labels/metadata
       |
       +--> normalization --> reconciliation --> allocation --> unit economics
```

## GCP billing integration

Support:
- detailed Cloud Billing export to BigQuery,
- FOCUS-compatible export where available,
- pricing export/catalog,
- normalized SLOPANOC-owned views.

Do not wire dashboards directly to raw evolving billing tables.

## Allocation dimensions

Use bounded taxonomy:
- environment,
- application=`slopanoc`,
- service,
- agent,
- model/provider,
- operation,
- domain,
- use case,
- SDU/team,
- cost center,
- project,
- region,
- billing SKU/resource class.

Run/turn/session IDs are allowed in ledger drilldowns but not metrics labels.

## Direct/shared/unallocated

### Direct
Examples:
- model token charges,
- billable embedding request,
- per-request paid API where attributable.

### Shared
Examples:
- Cloud SQL baseline,
- Cloud Run minimum instances,
- shared monitoring,
- shared storage/network.

### Unallocated
Costs not safely attributable.

Unallocated spend is a first-class visible category. Do not force fake precision.

## Allocation

Every allocation records:
- cost pool,
- target,
- driver,
- method,
- version,
- confidence,
- period.

Possible drivers:
- turn volume,
- compute duration,
- token share where causal,
- query load,
- storage byte-days.

## FinOps health metrics

- usage-ledger coverage,
- estimated-cost coverage,
- billing-ingestion coverage,
- allocation coverage,
- unallocated percentage,
- reconciliation variance,
- price-catalog age.

## Observability's own cost

Track:
- trace ingestion,
- logging,
- metrics,
- BigQuery storage/query,
- retention,
- dashboards/exporters.

Expose:

```text
observability_cost / total_slopanoc_cost
```

and budget it.

## Financial access

Separate engineering/SRE/FinOps/finance access from conversation-content access.

A FinOps user should be able to see model/agent/team/use-case spend without reading Teams messages or prompts.

## Currency

Store provider/native currency and normalized reporting currency only when conversion has a versioned exchange-rate source and effective date.


## Fixed billing-source decision

Primary production accounting inputs:
1. GCP Detailed Billing Export.
2. GCP Pricing Export/catalog.

FOCUS is a supplementary normalized source, not the sole authoritative source until explicitly approved by lifecycle/governance review.

Primary SLOPANOC reporting currency: EUR.

Retain native provider currency and conversion metadata where conversion is performed.

Accounting durability is defined in `20_FINOPS_ACCOUNTING_DURABILITY.md`.
