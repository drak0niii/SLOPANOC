
# 16 — Production Telemetry Backend and Collector Topology

## Production decision

The production topology is fixed:

```text
SLOPANOC application
        |
        | OTLP
        v
OpenTelemetry Collector
        |
        +--> Google Cloud Trace
        +--> Google Cloud Monitoring
        +--> Google Cloud Logging
        |
        +--> observability health metrics
```

FinOps uses BigQuery separately for billing, pricing, reconciliation and analytical workloads.

The application must not contain vendor-specific observability business logic beyond exporter/configuration adapters.

## Collector responsibility

The OpenTelemetry Collector is mandatory in production.

It must provide:
- OTLP ingestion,
- batching,
- retry/queueing,
- memory limiting,
- attribute filtering,
- redaction defense-in-depth,
- environment/resource enrichment,
- tail-sampling support,
- exporter health telemetry,
- bounded shutdown/flush behavior.

The Collector configuration is version-controlled as infrastructure-as-code.

## Collector processors

Production baseline must include:
- `memory_limiter`,
- `batch`,
- resource/environment enrichment,
- attribute filtering/redaction,
- tail-sampling processor when sampling is enabled.

Do not add processors that mutate semantic meaning of SLOPANOC events.

## Export destinations

### Traces
Primary: Google Cloud Trace.

### Metrics
Primary: Google Cloud Monitoring.

### Logs
Primary: Google Cloud Logging.

### FinOps
Primary runtime usage ledger: SLOPANOC durable relational store.  
Primary provider-billing analytics: BigQuery.

## Export failure behavior

A telemetry backend outage must not normally fail a user turn.

Required:
- exporter failure metric,
- dropped span/log metric,
- local bounded queue,
- retry with backoff,
- alert on sustained degradation.

Accounting-grade cost-ledger persistence has separate reliability rules in `20_FINOPS_ACCOUNTING_DURABILITY.md`.

## Semantic convention pinning

The repository must pin:
- OpenTelemetry SDK versions,
- instrumentation-library versions,
- semantic-convention version used for GenAI/HTTP/DB mapping.

Do not silently upgrade semantic-convention packages in production.

Every intentional semantic-convention upgrade requires:
- changelog,
- compatibility review,
- dashboard/query review,
- test update.

## Tail sampling production policy

Initial stabilization:
- 100% trace capture.

After stabilization, production tail sampling must retain 100% of:
- failed turns,
- timeout turns,
- stalled turns,
- safety events,
- governed operational turns,
- command-authority/approval flows,
- cost anomalies over configured threshold.

Healthy general-chat turns may be sampled at 10–20%.

Usage/cost ledgers and aggregate metrics are never sampled.

## Collector health

Monitor:
- accepted spans,
- exported spans,
- refused spans,
- dropped spans,
- queue size,
- retry count,
- memory pressure,
- exporter failures,
- collector CPU/memory,
- scrape/export latency.

Collector failure must appear in the SLOPANOC Health dashboard.
