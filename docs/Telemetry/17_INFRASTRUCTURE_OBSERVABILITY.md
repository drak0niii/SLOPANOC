
# 17 — Infrastructure Observability

## Scope decision

Production observability includes all major SLOPANOC infrastructure and dependencies:

- Cloud Run,
- Cloud SQL,
- Vertex AI / Gemini,
- Microsoft Graph,
- BigQuery,
- Google Cloud Storage,
- knowledge retrieval/vector/search infrastructure,
- network/external API dependencies,
- OpenTelemetry Collector,
- browser/SSE delivery path.

Application telemetry alone is not sufficient.

## Cloud Run

Monitor:
- request count,
- request latency,
- instance count,
- concurrency,
- CPU utilization,
- memory utilization,
- container startup/cold start,
- instance startup failures,
- 4xx/5xx,
- container restarts,
- minimum-instance utilization where configured.

Correlate deployments to service/revision identity.

## Cloud SQL

Monitor:
- CPU,
- memory,
- active connections,
- connection utilization,
- connection errors,
- storage utilization,
- disk latency/IOPS where available,
- transaction/query latency,
- lock/deadlock indicators,
- SQLAlchemy pool wait time,
- pool exhaustion,
- failed persistence.

Application DB spans complement platform metrics.

## Vertex AI / Gemini

Monitor application-visible:
- request rate,
- model,
- latency,
- TTFT,
- tokens,
- retries,
- rate limits,
- provider errors,
- timeout,
- cost.

Also ingest provider/platform service health where available.

## Microsoft Graph

Monitor:
- request rate,
- latency,
- 401/403,
- 404,
- 429,
- 5xx,
- retry count,
- pagination,
- timeout,
- auth/permission changes.

Never emit access tokens or message bodies.

## BigQuery

For FinOps/observability workloads track:
- query count,
- bytes processed,
- slot/resource use where available,
- query latency,
- failed queries,
- storage volume,
- cost of observability/FinOps queries.

Dashboard queries use curated views, not uncontrolled raw scans.

## Google Cloud Storage

Monitor:
- request rate,
- latency,
- failures,
- bytes uploaded/downloaded,
- storage growth.

## Knowledge subsystem

Monitor:
- sparse retrieval latency,
- dense retrieval latency,
- embedding latency/cost,
- candidate count,
- zero-result rate,
- relevance/fusion stages,
- applicability failures,
- DB/vector-provider failures.

## Network/external dependencies

Instrument every external HTTP boundary with:
- dependency name,
- duration,
- status class,
- timeout,
- retry,
- connection error.

## Infrastructure dashboard

Must provide:
- current saturation,
- latency,
- errors,
- dependency health,
- deployment/revision correlation,
- capacity trend.

## Capacity alerts

At minimum:
- Cloud Run sustained CPU/memory/concurrency saturation,
- Cloud SQL connection/storage thresholds,
- Collector queue/memory saturation,
- BigQuery cost/query anomaly,
- provider throttling/rate-limit spike.


## M5 application implementation and future platform integration contract

This section distinguishes locally implemented application observers from future
platform collection. No live cloud resource, platform metric query, dashboard,
alert, IAM binding or Terraform state change is performed by M5.

| Surface | Required future signals | Owner / integration boundary | M5 evidence |
|---|---|---|---|
| Cloud Run | Request count/latency, CPU, memory, concurrency, instance count, cold starts | Cloud Monitoring platform series scoped by service/revision/environment; correlate existing resource identity, never user/run labels | Contract only; platform ingestion/runtime values unvalidated |
| Cloud SQL | CPU, memory, connections, storage, platform/database latency | Cloud Monitoring instance/database series; combine with application DB health without equating process pools with instance connections | Contract only; four application DB owners instrumented locally |
| Collector | Queue occupancy/capacity, exporter errors, refused/dropped telemetry, memory/process health | Existing M1 loopback self-metrics endpoint; future approved scraper/dashboard | M1-owned source unchanged; local pipeline validated |
| BigQuery | Query count/latency/errors, bytes processed, resource/slot use, storage/cost | Future approved FinOps/observability workload client and curated views | No current runtime client; future only |

Platform dimensions must remain finite resource/service/revision/environment keys.
High-cardinality conversation/user/case/Teams/run IDs never become metric labels.
Platform CPU/memory/instance connections/storage/cold starts cannot be inferred
from application dependency latency or local SQLite tests. Effective infrastructure
configuration remains IaC-owned and read-only in future Settings.

Application coverage now includes Power Automate (no direct Graph visibility),
session/case/attachment/knowledge DB engines, chat/knowledge GCS object calls,
Secret Manager cache misses, and metadata/applicability/sparse/dense/fusion retrieval
stages. Existing M3 owns Vertex/Gemini and embedding requests. Inclusive
connection_acquire_duration covers pool wait + connect + pre_ping; pure pool wait
is unavailable. Pool gauges describe only supported process-local pool state,
aggregated per DB role, and are not Cloud SQL instance metrics. No new retries,
circuit breakers, deadlines, dashboards or provider configuration are introduced.
